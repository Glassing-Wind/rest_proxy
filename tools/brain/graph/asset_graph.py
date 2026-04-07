"""Asset and API linkage graph build helpers."""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import PurePosixPath
from typing import Awaitable, Callable

import graph_bootstrap
from _helpers import get_memory_modules, get_project_id
from tools.brain.graph import asset_graph_apple as apple_graph
from tools.brain.graph import asset_graph_write as asset_graph_write


ExecuteRead = Callable[..., Awaitable[list[dict[str, object]]]]
ExecuteWrite = Callable[..., Awaitable[None]]
DebugLog = Callable[..., None]
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
ROUTE_LITERAL_RE = re.compile(r"[\"'](/[^\"']+)[\"']")
TEMPLATE_LITERAL_RE = re.compile(r"`([^`]+)`")
API_LITERAL_RE = re.compile(r"[\"'](/api/[^\"']+)[\"']")
CLIENT_RE = re.compile(r"\b(fetch|axios|ky|ofetch)\b")
FETCH_METHOD_RE = re.compile(
    r"""\bfetch\(\s*([\"'`])(/[^\"'`]+)\1\s*,\s*\{[^{}]{0,300}?\bmethod\s*:\s*([\"'`])([A-Za-z]+)\3""",
    re.IGNORECASE | re.DOTALL,
)
CLIENT_METHOD_RE = re.compile(
    r"""\b(?:axios|ky)\.(get|post|put|patch|delete|head|options)\(\s*([\"'`])(/[^\"'`]+)\2""",
    re.IGNORECASE,
)
EXPRESS_ROUTE_RE = re.compile(
    r"\brouter\.(get|post|put|patch|delete|all|head|options)\s*\(\s*([\"'`])([^\"'`]+)\2",
    re.IGNORECASE,
)
ABSOLUTE_EXTERNAL_RE = re.compile(
    r"""(?:
        \bfetch\(\s*([\"'`])(https?://[^"'`]+)\1|
        \b(?:axios|ky|ofetch)\.(?:get|post|put|patch|delete|head|options)\(\s*([\"'`])(https?://[^"'`]+)\3|
        \bnew\s+URL\(\s*([\"'`])([^"'`]+)\5\s*,\s*([\"'`])(https?://[^"'`]+)\7
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _read_text(abs_path: str) -> str:
    try:
        if os.path.getsize(abs_path) > 1_000_000:
            return ""
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


async def _load_files(project_id: str, execute_read: ExecuteRead, neo4j_db_driver) -> dict[str, str]:
    async with neo4j_db_driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        rows = await execute_read(
            session,
            "MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid",
            p=project_id,
            op="build_asset_graph_files",
        )
    return {row["fp"]: row["fid"] for row in rows if row.get("fp") and row.get("fid")}


async def _load_file_facts(project_id: str) -> dict[str, dict[str, object]]:
    file_facts: dict[str, dict[str, object]] = {}
    try:
        memory_store, _, _, _, _ = get_memory_modules()
        await memory_store.open_pool()
        if not memory_store._pg_pool_available():
            return file_facts
        async with memory_store._pg_pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT DISTINCT ON (file_path) file_path, metadata->'file_facts'
                    FROM codebase_embeddings
                    WHERE project_id = %s AND metadata ? 'file_facts'
                    ORDER BY file_path, chunk_index
                    """,
                    (project_id,),
                )
                async for row in cur:
                    fp = row[0]
                    facts = row[1] or {}
                    if fp and isinstance(facts, dict):
                        file_facts[fp] = facts
    except Exception:
        return {}
    return file_facts


def _split_file_sets(files: dict[str, str]) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    html_files = [(fp, fid) for fp, fid in files.items() if fp.endswith((".html", ".astro"))]
    script_files = [
        (fp, fid)
        for fp, fid in files.items()
        if fp.endswith((".js", ".ts", ".tsx", ".astro", ".swift"))
    ]
    api_target_paths: list[str] = []
    for fp in files:
        if fp.endswith(("openapi.yaml", "openapi.json", "routes.ts", "routes.js")):
            if "/api/" in fp or "src/api/" in fp or fp.startswith("api/"):
                api_target_paths.append(fp)
        if ("/pages/api/" in fp or fp.startswith("pages/api/")) and fp.endswith((".ts", ".js", ".tsx", ".jsx")):
            api_target_paths.append(fp)
        if ("/app/" in fp or fp.startswith("app/")) and fp.endswith(("route.ts", "route.js", "route.tsx", "route.jsx")):
            api_target_paths.append(fp)
        if ("/api/" in fp or fp.startswith("api/")) and fp.endswith((".ts", ".js", ".tsx")):
            api_target_paths.append(fp)
    return html_files, script_files, list(dict.fromkeys(api_target_paths))


def _detect_api_prefixes(project_path: str, files: dict[str, str]) -> list[str]:
    prefixes: list[str] = []
    candidates = [fp for fp in files if fp.endswith(("app.ts", "app.js", "server.ts", "server.js"))]
    for fp in candidates:
        content = _read_text(os.path.join(project_path, fp))
        if not content:
            continue
        for match in re.finditer(r"app\.use\(\s*[\"']([^\"']+)[\"']\s*,", content):
            prefix = match.group(1)
            if "/api" in prefix:
                prefixes.append(prefix.rstrip("/"))
    if not prefixes:
        prefixes.append("/api")
    return list(dict.fromkeys(prefixes))


def _route_path_from_file(fp: str) -> str | None:
    path = PurePosixPath(fp)
    parts = path.parts
    if len(parts) < 2:
        return None
    idx = 0
    while idx < len(parts) and parts[idx] in {"packages", "apps", "src"}:
        if parts[idx] in {"packages", "apps"} and idx + 1 < len(parts):
            idx += 2
        else:
            idx += 1
    relevant_parts = parts[idx:]
    if not relevant_parts:
        return None
    if relevant_parts[0] == "app" and path.name.startswith("route."):
        route_parts = relevant_parts[1:-1]
        return "/" if not route_parts else "/" + "/".join(route_parts)
    if relevant_parts[0] == "pages" and len(relevant_parts) > 1 and relevant_parts[1] == "api":
        rel = relevant_parts[2:]
        if not rel:
            return "/api"
        file_stem = PurePosixPath(*rel).stem
        rel = rel[:-1] if file_stem in {"index", "route"} else rel[:-1] + (file_stem,)
        return "/api" if not rel else "/api/" + "/".join(rel)
    if relevant_parts[0] == "api":
        rel = relevant_parts[1:]
        if not rel:
            return "/api"
        file_stem = PurePosixPath(*rel).stem
        rel = rel[:-1] if file_stem in {"index", "route"} else rel[:-1] + (file_stem,)
        return "/api" if not rel else "/api/" + "/".join(rel)
    return None


def _is_backend_route_file(fp: str) -> bool:
    path = PurePosixPath(fp)
    parts = path.parts
    if "/api/" in fp or fp.startswith("api/"):
        return True
    if "pages" in parts and "api" in parts:
        return True
    return path.name.startswith("route.") and "app" in parts


def _normalize_http_method(method: str | None) -> str:
    method = (method or "").strip().upper()
    return method if method in HTTP_METHODS else "ANY"


def _extract_next_route_methods(content: str) -> list[str]:
    methods: set[str] = set()
    if not content:
        return ["ANY"]
    for pattern in (
        r"\bexport\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
        r"\bexport\s+const\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
    ):
        for match in re.findall(pattern, content, re.IGNORECASE):
            methods.add(_normalize_http_method(match))
    return sorted(methods) or ["ANY"]


def _build_route_targets(
    project_path: str,
    files: dict[str, str],
    file_facts: dict[str, dict[str, object]],
) -> tuple[dict[tuple[str, str], str], list[tuple[str, str, str]], list[tuple[re.Pattern[str], str, str]]]:
    route_targets: dict[tuple[str, str], str] = {}
    for fp, fid in files.items():
        route_path = _route_path_from_file(fp)
        if not route_path:
            continue
        methods = ["ANY"]
        if PurePosixPath(fp).name.startswith("route."):
            methods = _extract_next_route_methods(_read_text(os.path.join(project_path, fp)))
        for method in methods:
            route_targets.setdefault((route_path, method), fid)
    for fp, fid in files.items():
        facts = file_facts.get(fp) or {}
        for route in facts.get("route_defs") or []:
            if not isinstance(route, dict):
                continue
            path = route.get("path")
            method = _normalize_http_method(route.get("method"))
            if isinstance(path, str) and path.startswith("/"):
                route_targets.setdefault((path, method), fid)

    express_routes: list[tuple[str, str, str]] = []
    for fp, fid in files.items():
        if not ("/api/" in fp or fp.startswith("api/")):
            continue
        if not fp.endswith((".ts", ".js", ".tsx")):
            continue
        content = _read_text(os.path.join(project_path, fp))
        if not content:
            continue
        for match in EXPRESS_ROUTE_RE.findall(content):
            method = _normalize_http_method(match[0])
            raw = match[2].strip()
            if raw.startswith("/"):
                express_routes.append((raw, method, fid))

    express_patterns = [(_route_regex_from_path(path), method, fid) for path, method, fid in express_routes]
    return route_targets, express_routes, express_patterns


def _route_regex_from_path(path: str) -> re.Pattern[str]:
    parts = [part for part in path.split("/") if part]
    pattern_parts: list[str] = []
    for part in parts:
        if part.startswith(":"):
            pattern_parts.append(r"[^/]+")
        elif part == "*":
            pattern_parts.append(r".+")
        else:
            pattern_parts.append(re.escape(part))
    return re.compile("^/" + "/".join(pattern_parts) + "/?$")


def _resolve_href(src_fp: str, raw: str, files: dict[str, str]) -> str | None:
    import posixpath

    raw = raw.split("#", 1)[0].split("?", 1)[0].strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://", "//", "data:", "mailto:")):
        return None
    if raw.startswith("/"):
        candidate = raw.lstrip("/")
        if candidate.startswith("assets/"):
            candidate = posixpath.join("src/public", candidate)
        else:
            public_candidate = posixpath.join("src/public", candidate)
            if public_candidate in files:
                candidate = public_candidate
    else:
        src_dir = str(PurePosixPath(src_fp).parent)
        candidate = posixpath.normpath(posixpath.join(src_dir, raw)).lstrip("/")
        if candidate.startswith("../"):
            return None
    return candidate if candidate in files else None


def _collect_html_asset_edges(project_path: str, html_files: list[tuple[str, str]], files: dict[str, str]) -> list[tuple[str, str]]:
    html_edges: list[tuple[str, str]] = []
    for fp, fid in html_files:
        content = _read_text(os.path.join(project_path, fp))
        if not content:
            continue
        for match in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", content, re.I):
            target = _resolve_href(fp, match.group(1), files)
            if target and files[target] != fid:
                html_edges.append((fid, files[target]))
        for match in re.finditer(r"<link[^>]+href=[\"']([^\"']+)[\"']", content, re.I):
            href = match.group(1)
            if not href.lower().endswith(".css"):
                continue
            target = _resolve_href(fp, href, files)
            if target and files[target] != fid:
                html_edges.append((fid, files[target]))
    return list(set(html_edges))


def _collect_route_calls(content: str) -> list[tuple[str, str | None]]:
    calls: list[tuple[str, str | None]] = []
    if not content:
        return calls
    for _, path, _, method in FETCH_METHOD_RE.findall(content):
        calls.append((path, _normalize_http_method(method)))
    for method, _, path in CLIENT_METHOD_RE.findall(content):
        calls.append((path, _normalize_http_method(method)))
    return calls


def _collect_external_api_literals(content: str) -> list[str]:
    urls: list[str] = []
    for match in ABSOLUTE_EXTERNAL_RE.findall(content):
        literal = match[1] or match[3] or ""
        if not literal and match[5] and match[7]:
            literal = f"{match[7].rstrip('/')}/{match[5].lstrip('/')}"
        if literal.startswith(("http://", "https://")):
            urls.append(literal)
    return sorted(set(urls))


def _collect_api_edges(
    project_path: str,
    script_files: list[tuple[str, str]],
    files: dict[str, str],
    file_facts: dict[str, dict[str, object]],
    api_targets: list[str],
    route_targets: dict[tuple[str, str], str],
    express_routes: list[tuple[str, str, str]],
    express_patterns: list[tuple[re.Pattern[str], str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]], list[tuple[str, str, str]], list[tuple[str, str]]]:
    api_edges: list[tuple[str, str]] = []
    api_route_edges: list[tuple[str, str, str]] = []
    api_route_handler_edges: list[tuple[str, str, str]] = []
    external_edges: list[tuple[str, str]] = []
    api_target_ids = [files[p] for p in api_targets if p in files]

    for fp, fid in script_files:
        content = _read_text(os.path.join(project_path, fp))
        if not content:
            continue

        facts = file_facts.get(fp) or {}
        matched_targets: set[str] = set()
        matched_routes: set[tuple[str, str]] = set()
        literal_paths: list[tuple[str, str | None]] = []
        for call in facts.get("http_calls") or []:
            if not isinstance(call, dict):
                continue
            path = call.get("path")
            method = call.get("method")
            if isinstance(path, str) and path.startswith("/"):
                literal_paths.append((path, _normalize_http_method(method)))

        if not literal_paths:
            literal_paths = _collect_route_calls(content)
            if not _is_backend_route_file(fp):
                for match in ROUTE_LITERAL_RE.finditer(content):
                    literal_paths.append((match.group(1), None))
                for match in TEMPLATE_LITERAL_RE.finditer(content):
                    literal = match.group(1)
                    if "${" in literal:
                        literal = literal.split("${", 1)[0]
                    if literal.startswith("/"):
                        literal_paths.append((literal, None))

        for literal, method_hint in literal_paths:
            cleaned = literal.split("?", 1)[0].split("#", 1)[0]
            for pattern, route_method, target in express_patterns:
                if pattern.match(cleaned):
                    if method_hint and route_method not in {"ANY", method_hint}:
                        continue
                    matched_targets.add(target)
                    matched_routes.add((cleaned, route_method))
            route_matches = [
                ((path, method), target)
                for (path, method), target in route_targets.items()
                if path == cleaned and (method_hint is None or method in {"ANY", method_hint})
            ]
            if route_matches:
                for (path, method), target in route_matches:
                    matched_targets.add(target)
                    matched_routes.add((path, method))
            elif cleaned.startswith("/api/") and api_target_ids:
                matched_targets.update(api_target_ids)

        if not matched_targets and API_LITERAL_RE.search(content):
            matched_targets.update(api_target_ids)

        for target in matched_targets:
            if target != fid:
                api_edges.append((fid, target))
        for route_path, route_method in matched_routes:
            api_route_edges.append((fid, route_path, route_method))
            for path, method, handler_fid in express_routes:
                if path == route_path and (route_method in {"ANY", method} or method == "ANY") and handler_fid != fid:
                    api_route_handler_edges.append((route_path, method, handler_fid))
            for (path, method), handler_fid in route_targets.items():
                if path == route_path and (route_method in {"ANY", method} or method == "ANY") and handler_fid != fid:
                    api_route_handler_edges.append((route_path, method, handler_fid))

        for url in _collect_external_api_literals(content):
            external_edges.append((fid, url))

    return (
        list(set(api_edges)),
        list(set(api_route_edges)),
        list(set(api_route_handler_edges)),
        list(set(external_edges)),
    )




def _collect_service_edges(project_path: str, files: dict[str, str]) -> list[tuple[str, str]]:
    service_files = {
        os.path.splitext(os.path.basename(fp))[0]: fid
        for fp, fid in files.items()
        if ("/services/" in fp or fp.startswith("services/")) and fp.endswith((".ts", ".js", ".tsx", ".jsx", ".swift"))
    }
    edges: list[tuple[str, str]] = []
    if not service_files:
        return edges
    backend_files = [
        (fp, fid)
        for fp, fid in files.items()
        if (
            "/api/" in fp or "/webhooks/" in fp or "/jobs/" in fp or fp.startswith("api/") or fp.startswith("webhooks/") or fp.startswith("jobs/")
        )
        and fp.endswith((".ts", ".js", ".swift"))
    ]
    for fp, fid in backend_files:
        content = _read_text(os.path.join(project_path, fp))
        if not content:
            continue
        for name, svc_fid in service_files.items():
            if name in {"index", "types"}:
                continue
            if re.search(rf"\b{name}\b", content):
                edges.append((fid, svc_fid))
    return list(set(edges))


def _collect_db_edges(project_path: str, files: dict[str, str]) -> list[tuple[str, str]]:
    schema_fp = next((fp for fp in files if fp.endswith("prisma/schema.prisma")), None)
    schema_fid = files.get(schema_fp) if schema_fp else None
    if not schema_fid:
        return []
    schema_text = _read_text(os.path.join(project_path, schema_fp))
    models = set(re.findall(r"\bmodel\s+(\w+)\s+\{", schema_text))
    if not models:
        return []
    delegates = set(models)
    for name in list(models):
        delegates.add(name[0].lower() + name[1:])
    prisma_re = re.compile(r"\b(?:this\.)?prisma\.([A-Za-z_][A-Za-z0-9_]*)\b")
    edges: list[tuple[str, str]] = []
    for fp, fid in files.items():
        if not fp.endswith((".ts", ".js")):
            continue
        content = _read_text(os.path.join(project_path, fp))
        if any(match in delegates for match in prisma_re.findall(content)):
            edges.append((fid, schema_fid))
    return list(set(edges))


async def build_asset_graph(
    project_path: str,
    execute_read: ExecuteRead,
    execute_write: ExecuteWrite,
    debug_log: DebugLog,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
) -> str:
    """Build asset/API/resource linkage edges for a project."""
    try:
        debug_log("asset_graph_start", project_path=project_path)
        start = time.perf_counter()
        project_id = get_project_id(project_path)
        driver = await graph_bootstrap.require_driver()
        files = await _load_files(project_id, execute_read, driver)
        if not files:
            return "No files found for asset graph."

        file_facts = await _load_file_facts(project_id)
        html_files, script_files, api_target_paths = _split_file_sets(files)
        api_prefixes = _detect_api_prefixes(project_path, files)
        route_targets, express_routes, express_patterns = _build_route_targets(project_path, files, file_facts)

        prefixed_express_routes = []
        for path, method, fid in express_routes:
            for prefix in api_prefixes:
                prefixed_express_routes.append((f"{prefix}{path}".replace("//", "/"), method, fid))
            prefixed_express_routes.append((path, method, fid))
        express_routes = list(dict.fromkeys(prefixed_express_routes))
        express_patterns = [(_route_regex_from_path(path), method, fid) for path, method, fid in express_routes]

        html_edges = _collect_html_asset_edges(project_path, html_files, files)
        api_edges, api_route_edges, api_route_handler_edges, external_edges = _collect_api_edges(
            project_path,
            script_files,
            files,
            file_facts,
            api_target_paths,
            route_targets,
            express_routes,
            express_patterns,
        )
        service_edges = _collect_service_edges(project_path, files)
        db_edges = _collect_db_edges(project_path, files)
        resource_edges = apple_graph.collect_swift_resource_edges(project_path, file_facts, files)
        xcode_targets, xcode_file_edges, xcode_resource_edges = apple_graph.collect_xcode_target_edges(
            project_path, files, resource_edges
        )
        workspace_rows, workspace_project_edges, scheme_rows, scheme_target_edges, scheme_file_edges = (
            apple_graph.collect_xcode_workspace_scheme_edges(project_path, files, xcode_targets)
        )

        if not any(
            [
                html_edges,
                api_edges,
                api_route_edges,
                external_edges,
                service_edges,
                db_edges,
                resource_edges,
                xcode_file_edges,
                xcode_resource_edges,
                workspace_project_edges,
                scheme_target_edges,
                scheme_file_edges,
            ]
        ):
            return "No asset edges resolved."

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await asset_graph_write.clear_existing_edges(session, execute_write, project_id, write_timeout_s)
            await asset_graph_write.write_file_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, "ASSET_LINKS", html_edges
            )
            await asset_graph_write.write_file_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, "CALLS_API", api_edges
            )
            await asset_graph_write.write_api_route_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, project_id, api_route_edges, api_route_handler_edges
            )
            await asset_graph_write.write_external_api_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, project_id, external_edges
            )
            await asset_graph_write.write_file_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, "CALLS_SERVICE", service_edges
            )
            await asset_graph_write.write_file_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, "CALLS_DB", db_edges
            )
            await apple_graph.write_resource_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, project_id, resource_edges
            )
            await apple_graph.write_resource_backing_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, project_id, resource_edges
            )
            await apple_graph.write_xcode_target_edges(
                session,
                execute_write,
                write_semaphore,
                batch_size,
                write_timeout_s,
                project_path,
                project_id,
                xcode_targets,
                xcode_file_edges,
                xcode_resource_edges,
            )
            await apple_graph.write_xcode_workspace_scheme_edges(
                session,
                execute_write,
                write_semaphore,
                batch_size,
                write_timeout_s,
                project_id,
                workspace_rows,
                workspace_project_edges,
                scheme_rows,
                scheme_target_edges,
                scheme_file_edges,
            )

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        debug_log(
            "asset_graph_done",
            project_path=project_path,
            elapsed_ms=elapsed_ms,
            asset_links=len(html_edges),
            api_links=len(api_edges),
            api_route_links=len(api_route_edges),
            external_api_links=len(external_edges),
            resource_links=len(resource_edges),
            xcode_target_links=len(xcode_file_edges) + len(xcode_resource_edges),
            xcode_workspace_scheme_links=len(workspace_project_edges) + len(scheme_target_edges) + len(scheme_file_edges),
            service_links=len(service_edges),
            db_links=len(db_edges),
        )
        return (
            f"ASSET graph built for {project_path.split('/')[-1]}:\n"
            f"  {len(html_edges)} ASSET_LINKS edges\n"
            f"  {len(api_edges)} CALLS_API edges\n"
            f"  {len(api_route_edges)} CALLS_API_ROUTE edges\n"
            f"  {len(external_edges)} CALLS_API_EXTERNAL edges\n"
            f"  {len(resource_edges)} resource edges\n"
            f"  {len(xcode_file_edges)} BUNDLES_FILE edges\n"
            f"  {len(xcode_resource_edges)} BUNDLED_IN_TARGET edges\n"
            f"  {len(workspace_project_edges)} REFERENCES_PROJECT edges\n"
            f"  {len(scheme_target_edges)} BUILDS_TARGET edges\n"
            f"  {len(scheme_file_edges)} DEFINED_IN_FILE edges\n"
            f"  {len(service_edges)} CALLS_SERVICE edges\n"
            f"  {len(db_edges)} CALLS_DB edges\n"
            f"  elapsed={elapsed_ms}ms"
        )
    except Exception as exc:
        debug_log("asset_graph_error", project_path=project_path, error=str(exc))
        return f"Error building asset graph: {str(exc)}"

"""tools/graph/asset_graph.py — asset linkage graph build."""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import PurePosixPath
from typing import Awaitable, Callable

import graph_bootstrap
from _helpers import get_memory_modules, get_project_id


ExecuteRead = Callable[..., Awaitable[list[dict[str, object]]]]
ExecuteWrite = Callable[..., Awaitable[None]]
DebugLog = Callable[..., None]
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


async def build_asset_graph(
    project_path: str,
    execute_read: ExecuteRead,
    execute_write: ExecuteWrite,
    debug_log: DebugLog,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
) -> str:
    """Build asset linkage edges (HTML → assets, JS/TS → API spec/routes)."""
    try:
        debug_log("asset_graph_start", project_path=project_path)
        start = time.perf_counter()
        import posixpath

        project_id = get_project_id(project_path)
        driver = await graph_bootstrap.require_driver()

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r = await execute_read(
                session,
                "MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid",
                p=project_id,
                op="build_asset_graph_files",
            )
            files: dict[str, str] = {}
            for rec in r:
                files[rec["fp"]] = rec["fid"]

        if not files:
            return "No files found for asset graph."

        file_facts: dict[str, dict[str, object]] = {}
        try:
            memory_store, _, _, _, _ = get_memory_modules()
            await memory_store.open_pool()
            if memory_store._pg_pool_available():
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
            file_facts = {}

        html_files = [
            (fp, fid) for fp, fid in files.items() if fp.endswith((".html", ".astro"))
        ]
        script_files = [
            (fp, fid)
            for fp, fid in files.items()
            if fp.endswith((".js", ".ts", ".tsx", ".astro"))
        ]

        api_target_paths: list[str] = []
        for fp in files:
            # 1. Recognized API Root files
            if fp.endswith(("openapi.yaml", "openapi.json", "routes.ts", "routes.js")):
                if "/api/" in fp or "src/api/" in fp or fp.startswith("api/"):
                    api_target_paths.append(fp)
            
            # 2. Next.js / Standard API directories (Pages or App Router)
            # Supports: src/pages/api, pages/api, packages/app/src/pages/api, etc.
            if "/pages/api/" in fp or fp.startswith("pages/api/"):
                if fp.endswith((".ts", ".js", ".tsx", ".jsx")):
                    api_target_paths.append(fp)
            
            # App Router: matches route.ts in any 'app' directory (e.g., packages/web/src/app/api/...)
            if "/app/" in fp or fp.startswith("app/"):
                if fp.endswith(("route.ts", "route.js", "route.tsx", "route.jsx")):
                    api_target_paths.append(fp)

            # 3. Generic API directory (e.g. src/api/...)
            if "/api/" in fp or fp.startswith("api/"):
                if fp.endswith((".ts", ".js", ".tsx")):
                    api_target_paths.append(fp)

        api_targets = [files[p] for p in api_target_paths if p in files]
        api_targets = list(dict.fromkeys(api_targets))

        def _read_text(abs_path: str) -> str:
            try:
                if os.path.getsize(abs_path) > 1_000_000:
                    return ""
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    return fh.read()
            except OSError:
                return ""

        def _detect_api_prefixes() -> list[str]:
            prefixes: list[str] = []
            candidates = [
                fp
                for fp in files
                if fp.endswith(("app.ts", "app.js", "server.ts", "server.js"))
            ]
            for fp in candidates:
                abs_path = os.path.join(project_path, fp)
                content = _read_text(abs_path)
                if not content:
                    continue
                for m in re.finditer(r"app\.use\(\s*[\"']([^\"']+)[\"']\s*,", content):
                    prefix = m.group(1)
                    if "/api" in prefix:
                        prefixes.append(prefix.rstrip("/"))
            if not prefixes:
                prefixes.append("/api")
            return list(dict.fromkeys(prefixes))

        api_prefixes = _detect_api_prefixes()

        def _route_path_from_file(fp: str) -> str | None:
            path = PurePosixPath(fp)
            parts = path.parts
            if len(parts) < 2:
                return None
            
            # Monorepo support: skip package/app prefixes
            # e.g. packages/web/src/app/api/hello/route.ts -> app/api/hello/route.ts
            idx = 0
            while idx < len(parts) and parts[idx] in {"packages", "apps", "src"}:
                # If we're at 'src', we skip it but stay alert for the next part
                # If we are in a subfolder of packages/ or apps/, we skip that subfolder too
                if parts[idx] in {"packages", "apps"} and idx + 1 < len(parts):
                    idx += 2 # Skip 'packages' AND the package name
                else:
                    idx += 1
            
            relevant_parts = parts[idx:]
            if not relevant_parts:
                return None
                
            if relevant_parts[0] == "app" and path.name.startswith("route."):
                route_parts = relevant_parts[1:-1]
                if not route_parts:
                    return "/"
                return "/" + "/".join(route_parts)
                
            if relevant_parts[0] == "pages" and len(relevant_parts) > 1 and relevant_parts[1] == "api":
                rel = relevant_parts[2:]
                if not rel:
                    return "/api"
                file_stem = PurePosixPath(*rel).stem
                if file_stem == "index":
                    rel = rel[:-1]
                else:
                    rel = rel[:-1] + (file_stem,)
                if not rel:
                    return "/api"
                return "/api/" + "/".join(rel)
                
            if relevant_parts[0] == "api":
                rel = relevant_parts[1:]
                if not rel:
                    return "/api"
                file_stem = PurePosixPath(*rel).stem
                if file_stem == "index":
                    rel = rel[:-1]
                else:
                    rel = rel[:-1] + (file_stem,)
                if not rel:
                    return "/api"
                return "/api/" + "/".join(rel)
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
                method = _normalize_http_method(route.get("method")) or "ANY"
                if isinstance(path, str) and path.startswith("/"):
                    route_targets.setdefault((path, method), fid)

        def _collect_express_routes() -> list[tuple[str, str, str]]:
            routes: list[tuple[str, str, str]] = []
            route_re = re.compile(
                r"\brouter\.(get|post|put|patch|delete|all|head|options)\s*\(\s*([\"'`])([^\"'`]+)\2",
                re.IGNORECASE,
            )
            for fp, fid in files.items():
                if not ("/api/" in fp or fp.startswith("api/")):
                    continue
                if not fp.endswith((".ts", ".js", ".tsx")):
                    continue
                abs_path = os.path.join(project_path, fp)
                content = _read_text(abs_path)
                if not content:
                    continue
                for match in route_re.findall(content):
                    method = _normalize_http_method(match[0])
                    raw = match[2].strip()
                    if not raw.startswith("/"):
                        continue
                    for prefix in api_prefixes:
                        full_path = f"{prefix}{raw}".replace("//", "/")
                        routes.append((full_path, method, fid))
                    routes.append((raw, method, fid))
            return routes

        express_routes = _collect_express_routes()

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
            pattern = "^/" + "/".join(pattern_parts) + "/?$"
            return re.compile(pattern)

        express_route_patterns: list[tuple[re.Pattern[str], str, str]] = [
            (_route_regex_from_path(path), method, fid)
            for path, method, fid in express_routes
        ]

        def _collect_route_calls(content: str) -> list[tuple[str, str | None]]:
            calls: list[tuple[str, str | None]] = []
            if not content:
                return calls

            fetch_method_re = re.compile(
                r"""\bfetch\(\s*([\"'`])(/[^\"'`]+)\1\s*,\s*\{[^{}]{0,300}?\bmethod\s*:\s*([\"'`])([A-Za-z]+)\3""",
                re.IGNORECASE | re.DOTALL,
            )
            client_method_re = re.compile(
                r"""\b(?:axios|ky)\.(get|post|put|patch|delete|head|options)\(\s*([\"'`])(/[^\"'`]+)\2""",
                re.IGNORECASE,
            )
            for _, path, _, method in fetch_method_re.findall(content):
                calls.append((path, _normalize_http_method(method)))
            for method, _, path in client_method_re.findall(content):
                calls.append((path, _normalize_http_method(method)))
            return calls

        def _resolve_href(src_fp: str, raw: str) -> str | None:
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

        html_edges: list[tuple[str, str]] = []
        for fp, fid in html_files:
            abs_path = os.path.join(project_path, fp)
            content = _read_text(abs_path)
            if not content:
                continue
            for m in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", content, re.I):
                target = _resolve_href(fp, m.group(1))
                if target and files[target] != fid:
                    html_edges.append((fid, files[target]))
            for m in re.finditer(r"<link[^>]+href=[\"']([^\"']+)[\"']", content, re.I):
                href = m.group(1)
                if not href.lower().endswith(".css"):
                    continue
                target = _resolve_href(fp, href)
                if target and files[target] != fid:
                    html_edges.append((fid, files[target]))

        api_edges: list[tuple[str, str]] = []
        api_route_edges: list[tuple[str, str, str]] = []
        api_route_handler_edges: list[tuple[str, str, str]] = []
        if api_targets or route_targets:
            api_re = re.compile(r"[\"'](/api/[^\"']+)[\"']")
            client_re = re.compile(r"\b(fetch|axios|ky|ofetch)\b")
            for fp, fid in script_files:
                abs_path = os.path.join(project_path, fp)
                content = _read_text(abs_path)
                if not content:
                    continue
                if not api_re.search(content) and not client_re.search(content):
                    facts = file_facts.get(fp) or {}
                    if not (facts.get("http_calls") or []):
                        continue
                matched_targets: set[str] = set()
                matched_routes: set[tuple[str, str]] = set()
                facts = file_facts.get(fp) or {}
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
                        for m in re.finditer(r"[\"'](/[^\"']+)[\"']", content):
                            literal_paths.append((m.group(1), None))
                        for m in re.finditer(r"`([^`]+)`", content):
                            literal = m.group(1)
                            if "${" in literal:
                                literal = literal.split("${", 1)[0]
                            if literal.startswith("/"):
                                literal_paths.append((literal, None))

                for literal, method_hint in literal_paths:
                    cleaned = literal.split("?", 1)[0].split("#", 1)[0]
                    for pattern, route_method, target in express_route_patterns:
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
                    elif cleaned.startswith("/api/") and api_targets:
                        matched_targets.update(api_targets)
                if not matched_targets and api_re.search(content):
                    matched_targets.update(api_targets)
                for tgt in matched_targets:
                    if tgt != fid:
                        api_edges.append((fid, tgt))
                for route_path, route_method in matched_routes:
                    api_route_edges.append((fid, route_path, route_method))
                    for path, method, handler_fid in express_routes:
                        if path != route_path:
                            continue
                        if route_method not in {"ANY", method} and method != "ANY":
                            continue
                        if handler_fid != fid:
                            api_route_handler_edges.append((route_path, method, handler_fid))
                    for (path, method), handler_fid in route_targets.items():
                        if path != route_path:
                            continue
                        if route_method not in {"ANY", method} and method != "ANY":
                            continue
                        if handler_fid != fid:
                            api_route_handler_edges.append((route_path, method, handler_fid))

        service_edges: list[tuple[str, str]] = []
        service_files = {
            os.path.splitext(os.path.basename(fp))[0]: fid
            for fp, fid in files.items()
            if ("/services/" in fp or fp.startswith("services/")) and fp.endswith((".ts", ".js", ".tsx", ".jsx"))
        }
        if service_files:
            backend_files = [
                (fp, fid)
                for fp, fid in files.items()
                if (
                    "/api/" in fp
                    or "/webhooks/" in fp
                    or "/jobs/" in fp
                    or fp.startswith("api/")
                    or fp.startswith("webhooks/")
                    or fp.startswith("jobs/")
                )
                and fp.endswith((".ts", ".js"))
            ]
            for fp, fid in backend_files:
                abs_path = os.path.join(project_path, fp)
                content = _read_text(abs_path)
                if not content:
                    continue
                for name, svc_fid in service_files.items():
                    if name in {"index", "types"}:
                        continue
                    if re.search(rf"\b{name}\b", content):
                        service_edges.append((fid, svc_fid))

        db_edges: list[tuple[str, str]] = []
        schema_fp = next((fp for fp in files if fp.endswith("prisma/schema.prisma")), None)
        schema_fid = files.get(schema_fp) if schema_fp else None
        if schema_fid:
            schema_path = os.path.join(project_path, schema_fp)
            schema_text = _read_text(schema_path)
            models = set(re.findall(r"\bmodel\s+(\w+)\s+\{", schema_text))
            if models:
                delegates = set(models)
                for name in list(models):
                    if name:
                        delegates.add(name[0].lower() + name[1:])
                prisma_re = re.compile(
                    r"\b(?:this\.)?prisma\.([A-Za-z_][A-Za-z0-9_]*)\b"
                )
                scan_files = [
                    (fp, fid)
                    for fp, fid in files.items()
                    if fp.endswith((".ts", ".js")) # Scan all TS/JS for Prisma usage
                ]
                for fp, fid in scan_files:
                    abs_path = os.path.join(project_path, fp)
                    content = _read_text(abs_path)
                    if not content:
                        continue
                    for match in prisma_re.findall(content):
                        if match in delegates:
                            db_edges.append((fid, schema_fid))
                            break

        html_edges = list(set(html_edges))
        api_edges = list(set(api_edges))
        api_route_edges = list(set(api_route_edges))
        api_route_handler_edges = list(set(api_route_handler_edges))
        service_edges = list(set(service_edges))
        db_edges = list(set(db_edges))
        if not html_edges and not api_edges and not api_route_edges and not service_edges and not db_edges:
            return "No asset edges resolved."

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:ASSET_LINKS]->() DELETE r",
                p=project_id,
                timeout=write_timeout_s,
            )
            await execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:CALLS_API]->() DELETE r",
                p=project_id,
                timeout=write_timeout_s,
            )
            await execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:CALLS_API_ROUTE]->() DELETE r",
                p=project_id,
                timeout=write_timeout_s,
            )
            await execute_write(
                session,
                "MATCH (r:ApiRoute {project_id:$p})-[rel:HANDLED_BY]->() DELETE rel",
                p=project_id,
                timeout=write_timeout_s,
            )
            await execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:CALLS_SERVICE]->() DELETE r",
                p=project_id,
                timeout=write_timeout_s,
            )
            await execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:CALLS_DB]->() DELETE r",
                p=project_id,
                timeout=write_timeout_s,
            )
            if html_edges:
                for i in range(0, len(html_edges), batch_size):
                    batch = [
                        {"src": s, "tgt": t} for s, t in html_edges[i : i + batch_size]
                    ]
                    async with write_semaphore:
                        await execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:ASSET_LINKS]->(b)
                            """,
                            batch=batch,
                            timeout=write_timeout_s,
                        )
            if api_edges:
                for i in range(0, len(api_edges), batch_size):
                    batch = [
                        {"src": s, "tgt": t} for s, t in api_edges[i : i + batch_size]
                    ]
                    async with write_semaphore:
                        await execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:CALLS_API]->(b)
                            """,
                            batch=batch,
                            timeout=write_timeout_s,
                        )

            if api_route_edges:
                for i in range(0, len(api_route_edges), batch_size):
                    batch = [
                        {"src": s, "path": p, "method": m, "project_id": project_id}
                        for s, p, m in api_route_edges[i : i + batch_size]
                    ]
                    async with write_semaphore:
                        await execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MERGE (r:ApiRoute {project_id: edge.project_id, path: edge.path, method: edge.method})
                            ON CREATE SET r.name = edge.method + ' ' + edge.path
                            SET r.filepath = edge.path
                            MERGE (a)-[:CALLS_API_ROUTE]->(r)
                            """,
                            batch=batch,
                            timeout=write_timeout_s,
                        )

            if api_route_handler_edges:
                for i in range(0, len(api_route_handler_edges), batch_size):
                    batch = [
                        {"path": p, "method": m, "tgt": t, "project_id": project_id}
                        for p, m, t in api_route_handler_edges[i : i + batch_size]
                    ]
                    async with write_semaphore:
                        await execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (r:ApiRoute {project_id: edge.project_id, path: edge.path, method: edge.method})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (r)-[:HANDLED_BY]->(b)
                            """,
                            batch=batch,
                            timeout=write_timeout_s,
                        )

            if service_edges:
                for i in range(0, len(service_edges), batch_size):
                    batch = [
                        {"src": s, "tgt": t}
                        for s, t in service_edges[i : i + batch_size]
                    ]
                    async with write_semaphore:
                        await execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:CALLS_SERVICE]->(b)
                            """,
                            batch=batch,
                            timeout=write_timeout_s,
                        )

            if db_edges:
                for i in range(0, len(db_edges), batch_size):
                    batch = [
                        {"src": s, "tgt": t} for s, t in db_edges[i : i + batch_size]
                    ]
                    async with write_semaphore:
                        await execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:CALLS_DB]->(b)
                            """,
                            batch=batch,
                            timeout=write_timeout_s,
                        )

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        debug_log(
            "asset_graph_done",
            project_path=project_path,
            elapsed_ms=elapsed_ms,
            asset_links=len(html_edges),
            api_links=len(api_edges),
            api_route_links=len(api_route_edges),
            service_links=len(service_edges),
            db_links=len(db_edges),
        )
        return (
            f"ASSET graph built for {project_path.split('/')[-1]}:\n"
            f"  {len(html_edges)} ASSET_LINKS edges\n"
            f"  {len(api_edges)} CALLS_API edges\n"
            f"  {len(api_route_edges)} CALLS_API_ROUTE edges\n"
            f"  {len(service_edges)} CALLS_SERVICE edges\n"
            f"  {len(db_edges)} CALLS_DB edges\n"
            f"  elapsed={elapsed_ms}ms"
        )
    except Exception as exc:
        debug_log("asset_graph_error", project_path=project_path, error=str(exc))
        return f"Error building asset graph: {str(exc)}"

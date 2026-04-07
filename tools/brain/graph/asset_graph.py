"""Asset and API linkage graph build helpers."""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import PurePosixPath
from typing import Awaitable, Callable
from xml.etree import ElementTree as ET

import graph_bootstrap
from _helpers import get_memory_modules, get_project_id


ExecuteRead = Callable[..., Awaitable[list[dict[str, object]]]]
ExecuteWrite = Callable[..., Awaitable[None]]
DebugLog = Callable[..., None]
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
RESOURCE_REL_BY_KIND = {
    "image": "USES_ASSET",
    "color": "USES_COLOR_ASSET",
    "nib": "USES_XIB",
    "storyboard": "USES_STORYBOARD",
}
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


def _collect_swift_resource_edges(
    project_path: str,
    file_facts: dict[str, dict[str, object]],
    files: dict[str, str],
) -> list[tuple[str, str, str, str | None, str]]:
    resource_catalog = _discover_apple_resources(project_path)
    resource_edges: list[tuple[str, str, str, str | None, str]] = []
    for fp, fid in files.items():
        facts = file_facts.get(fp) or {}
        for ref in facts.get("resource_refs") or []:
            if not isinstance(ref, dict):
                continue
            kind = ref.get("kind")
            name = ref.get("name")
            if not isinstance(kind, str) or not isinstance(name, str):
                continue
            rel = RESOURCE_REL_BY_KIND.get(kind)
            if not rel:
                continue
            resource_edges.append((fid, rel, name, resource_catalog.get((kind, name)), kind))
    return list(set(resource_edges))


def _discover_apple_resources(project_path: str) -> dict[tuple[str, str], str]:
    discovered: dict[tuple[str, str], str] = {}
    for current_root, dirnames, filenames in os.walk(project_path):
        rel_root = PurePosixPath(os.path.relpath(current_root, project_path))
        if rel_root == PurePosixPath("."):
            rel_root = PurePosixPath("")
        for dirname in list(dirnames):
            if dirname.endswith(".imageset"):
                name = dirname[:-9]
                contents = rel_root / dirname / "Contents.json"
                abs_contents = os.path.join(project_path, str(contents))
                discovered.setdefault(
                    ("image", name),
                    str(contents.as_posix()) if os.path.exists(abs_contents) else str((rel_root / dirname).as_posix()),
                )
            elif dirname.endswith(".colorset"):
                name = dirname[:-9]
                contents = rel_root / dirname / "Contents.json"
                abs_contents = os.path.join(project_path, str(contents))
                discovered.setdefault(
                    ("color", name),
                    str(contents.as_posix()) if os.path.exists(abs_contents) else str((rel_root / dirname).as_posix()),
                )
        for filename in filenames:
            if filename.endswith(".xib"):
                discovered.setdefault(("nib", PurePosixPath(filename).stem), str((rel_root / filename).as_posix()))
            elif filename.endswith(".storyboard"):
                discovered.setdefault(("storyboard", PurePosixPath(filename).stem), str((rel_root / filename).as_posix()))
    return discovered


def _parse_xcode_target_membership(project_path: str) -> tuple[dict[str, dict[str, str]], list[tuple[str, str]]]:
    project_files = sorted(PurePosixPath(path) for path in os.listdir(project_path) if path.endswith(".xcodeproj"))
    for root, dirnames, _ in os.walk(project_path):
        for dirname in dirnames:
            if dirname.endswith(".xcodeproj"):
                rel = os.path.relpath(os.path.join(root, dirname), project_path)
                project_files.append(PurePosixPath(rel))
    unique_projects = []
    seen_projects: set[str] = set()
    for rel in project_files:
        rel_str = rel.as_posix()
        if rel_str in seen_projects:
            continue
        seen_projects.add(rel_str)
        unique_projects.append(rel)

    targets: dict[str, dict[str, str]] = {}
    memberships: list[tuple[str, str]] = []
    for project_rel in unique_projects:
        pbxproj = os.path.join(project_path, project_rel.as_posix(), "project.pbxproj")
        if not os.path.exists(pbxproj):
            continue
        text = _read_text(pbxproj)
        if not text:
            continue
        targets.update(_extract_xcode_targets(text, project_rel.as_posix()))
        memberships.extend(_extract_xcode_resource_memberships(text))
    return targets, list(dict.fromkeys(memberships))


def _extract_xcode_targets(text: str, project_file: str) -> dict[str, dict[str, str]]:
    targets: dict[str, dict[str, str]] = {}
    for target_id, name in re.findall(
        r"([A-F0-9]{8,}) /\* [^*]+ \*/ = \{\s*isa = PBXNativeTarget;.*?\bname = ([^;]+);",
        text,
        re.DOTALL,
    ):
        clean_name = name.strip().strip('"')
        if clean_name:
            targets[target_id] = {"name": clean_name, "project_file": project_file}
    return targets


def _extract_xcode_resource_memberships(text: str) -> list[tuple[str, str]]:
    build_file_to_ref: dict[str, str] = {}
    file_ref_to_path: dict[str, str] = {}
    build_phase_to_files: dict[str, list[str]] = {}
    target_to_build_phases: dict[str, list[str]] = {}

    for build_file_id, file_ref_id in re.findall(
        r"([A-F0-9]{8,}) /\* [^*]+ \*/ = \{\s*isa = PBXBuildFile;\s*fileRef = ([A-F0-9]{8,})",
        text,
        re.DOTALL,
    ):
        build_file_to_ref[build_file_id] = file_ref_id

    for file_ref_id, path, source_tree in re.findall(
        r"([A-F0-9]{8,}) /\* [^*]+ \*/ = \{\s*isa = PBXFileReference;.*?\bpath = ([^;]+);.*?\bsourceTree = ([^;]+);",
        text,
        re.DOTALL,
    ):
        clean_path = path.strip().strip('"')
        clean_source = source_tree.strip().strip('"')
        if clean_path and clean_source != "BUILT_PRODUCTS_DIR":
            file_ref_to_path[file_ref_id] = clean_path

    for phase_id, files_blob in re.findall(
        r"([A-F0-9]{8,}) /\* Resources \*/ = \{\s*isa = PBXResourcesBuildPhase;.*?\bfiles = \((.*?)\);",
        text,
        re.DOTALL,
    ):
        build_phase_to_files[phase_id] = re.findall(r"([A-F0-9]{8,}) /\*", files_blob)

    for target_id, phases_blob in re.findall(
        r"([A-F0-9]{8,}) /\* [^*]+ \*/ = \{\s*isa = PBXNativeTarget;.*?\bbuildPhases = \((.*?)\);",
        text,
        re.DOTALL,
    ):
        target_to_build_phases[target_id] = re.findall(r"([A-F0-9]{8,}) /\*", phases_blob)

    memberships: list[tuple[str, str]] = []
    for target_id, phase_ids in target_to_build_phases.items():
        for phase_id in phase_ids:
            for build_file_id in build_phase_to_files.get(phase_id, []):
                file_ref_id = build_file_to_ref.get(build_file_id)
                file_path = file_ref_to_path.get(file_ref_id or "")
                if file_path:
                    memberships.append((target_id, file_path))
    return memberships


def _collect_xcode_target_edges(
    project_path: str,
    files: dict[str, str],
    resource_edges: list[tuple[str, str, str, str | None, str]],
) -> tuple[dict[str, dict[str, str]], list[tuple[str, str]], list[tuple[str, str, str]]]:
    targets, raw_memberships = _parse_xcode_target_membership(project_path)
    if not targets or not raw_memberships:
        return targets, [], []

    file_edges: list[tuple[str, str]] = []
    resource_target_edges: list[tuple[str, str, str]] = []
    resource_by_path = {
        resource_path: (name, kind)
        for _, _, name, resource_path, kind in resource_edges
        if resource_path
    }
    for target_id, raw_path in raw_memberships:
        normalized = raw_path.replace("\\", "/").lstrip("./")
        normalized = normalized.strip('"')
        candidates = [normalized]
        project_file = targets.get(target_id, {}).get("project_file")
        if project_file:
            project_dir = PurePosixPath(project_file).parent
            if str(project_dir) not in {"", "."}:
                candidates.append(str((project_dir / normalized).as_posix()))
        target_path = next((candidate for candidate in candidates if candidate in files), None)
        target_file_id = files.get(target_path) if target_path else None
        if target_file_id:
            file_edges.append((target_id, target_file_id))
        resource_path = next((candidate for candidate in candidates if candidate in resource_by_path), None)
        resource_info = resource_by_path.get(resource_path or "")
        if resource_info:
            resource_target_edges.append((target_id, resource_info[0], resource_info[1]))
    return targets, list(dict.fromkeys(file_edges)), list(dict.fromkeys(resource_target_edges))


def _collect_xcode_workspace_scheme_edges(
    project_path: str,
    files: dict[str, str],
    xcode_targets: dict[str, dict[str, str]],
) -> tuple[
    list[dict[str, str]],
    list[tuple[str, str]],
    list[dict[str, str]],
    list[tuple[str, str]],
    list[tuple[str, str]],
]:
    workspaces = _parse_xcode_workspaces(project_path, files)
    schemes = _parse_xcode_schemes(project_path, files, xcode_targets)
    workspace_rows = [
        {"workspace_path": workspace_path, "name": PurePosixPath(workspace_path).parent.stem or PurePosixPath(workspace_path).stem}
        for workspace_path in sorted(workspaces.keys())
    ]
    workspace_project_edges = [
        (workspace_path, file_id)
        for workspace_path, project_files in workspaces.items()
        for file_id in sorted(project_files)
    ]
    scheme_rows = [
        {
            "scheme_path": scheme["scheme_path"],
            "name": scheme["name"],
            "container_path": scheme.get("container_path", ""),
        }
        for scheme in schemes
    ]
    scheme_target_edges = [
        (scheme["scheme_path"], target_id)
        for scheme in schemes
        for target_id in scheme["target_ids"]
    ]
    scheme_file_edges = [
        (scheme["scheme_path"], files[scheme["scheme_path"]])
        for scheme in schemes
        if scheme["scheme_path"] in files
    ]
    return (
        workspace_rows,
        workspace_project_edges,
        scheme_rows,
        scheme_target_edges,
        scheme_file_edges,
    )


def _parse_xcode_workspaces(project_path: str, files: dict[str, str]) -> dict[str, set[str]]:
    workspaces: dict[str, set[str]] = {}
    workspace_files = [fp for fp in files if fp.endswith(".xcworkspace/contents.xcworkspacedata")]
    for workspace_path in workspace_files:
        abs_path = os.path.join(project_path, workspace_path)
        text = _read_text(abs_path)
        if not text:
            continue
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        workspace_dir = PurePosixPath(workspace_path).parent
        for file_ref in root.findall(".//FileRef"):
            location = file_ref.attrib.get("location", "")
            if ":" in location:
                _, rel_ref = location.split(":", 1)
            else:
                rel_ref = location
            rel_ref = rel_ref.strip()
            if not rel_ref.endswith(".xcodeproj"):
                continue
            candidates = [
                rel_ref.lstrip("./"),
                str((workspace_dir / rel_ref).as_posix()).lstrip("./"),
                str((workspace_dir.parent / rel_ref).as_posix()).lstrip("./"),
            ]
            for candidate in candidates:
                project_file = f"{candidate.rstrip('/')}/project.pbxproj"
                file_id = files.get(project_file)
                if file_id:
                    workspaces.setdefault(workspace_path, set()).add(file_id)
                    break
    return workspaces


def _parse_xcode_schemes(
    project_path: str,
    files: dict[str, str],
    xcode_targets: dict[str, dict[str, str]],
) -> list[dict[str, object]]:
    schemes: list[dict[str, object]] = []
    scheme_files = [fp for fp in files if fp.endswith(".xcscheme")]
    for scheme_path in scheme_files:
        abs_path = os.path.join(project_path, scheme_path)
        text = _read_text(abs_path)
        if not text:
            continue
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        target_ids: set[str] = set()
        container_path = ""
        for buildable in root.findall(".//BuildableReference"):
            blueprint_id = buildable.attrib.get("BlueprintIdentifier", "").strip()
            if blueprint_id in xcode_targets:
                target_ids.add(blueprint_id)
            container = buildable.attrib.get("ReferencedContainer", "").strip()
            if container and not container_path:
                container_path = _normalize_scheme_container_path(scheme_path, container)
        schemes.append(
            {
                "scheme_path": scheme_path,
                "name": PurePosixPath(scheme_path).stem,
                "container_path": container_path,
                "target_ids": sorted(target_ids),
            }
        )
    return schemes


def _normalize_scheme_container_path(scheme_path: str, container_ref: str) -> str:
    if ":" in container_ref:
        _, rel_ref = container_ref.split(":", 1)
    else:
        rel_ref = container_ref
    rel_ref = rel_ref.strip().lstrip("./")
    if rel_ref.endswith(".xcodeproj"):
        return f"{rel_ref}/project.pbxproj"
    scheme_dir = PurePosixPath(scheme_path).parent
    candidate = (scheme_dir / rel_ref).as_posix().lstrip("./")
    if candidate.endswith(".xcodeproj"):
        return f"{candidate}/project.pbxproj"
    return candidate


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


async def _clear_existing_edges(session, execute_write: ExecuteWrite, project_id: str, write_timeout_s: float) -> None:
    statements = [
        "MATCH (a:File {project_id:$p})-[r:ASSET_LINKS]->() DELETE r",
        "MATCH (a:File {project_id:$p})-[r:CALLS_API]->() DELETE r",
        "MATCH (a:File {project_id:$p})-[r:CALLS_API_ROUTE]->() DELETE r",
        "MATCH (r:ApiRoute {project_id:$p})-[rel:HANDLED_BY]->() DELETE rel",
        "MATCH (a:File {project_id:$p})-[r:CALLS_SERVICE]->() DELETE r",
        "MATCH (a:File {project_id:$p})-[r:CALLS_DB]->() DELETE r",
        "MATCH (a:File {project_id:$p})-[r:CALLS_API_EXTERNAL]->() DELETE r",
        "MATCH (a:File {project_id:$p})-[r:USES_ASSET|USES_COLOR_ASSET|USES_XIB|USES_STORYBOARD]->() DELETE r",
        "MATCH (r:Resource {project_id:$p})-[rel:BACKED_BY_FILE]->() DELETE rel",
        "MATCH (:XcodeTarget {project_id:$p})-[rel:BUNDLES_FILE]->() DELETE rel",
        "MATCH (r:Resource {project_id:$p})-[rel:BUNDLED_IN_TARGET]->() DELETE rel",
        "MATCH (:XcodeWorkspace {project_id:$p})-[rel:REFERENCES_PROJECT]->() DELETE rel",
        "MATCH (:XcodeScheme {project_id:$p})-[rel:BUILDS_TARGET]->() DELETE rel",
        "MATCH (:XcodeScheme {project_id:$p})-[rel:DEFINED_IN_FILE]->() DELETE rel",
        "MATCH (t:XcodeTarget {project_id:$p}) DELETE t",
        "MATCH (s:XcodeScheme {project_id:$p}) DELETE s",
        "MATCH (w:XcodeWorkspace {project_id:$p}) DELETE w",
    ]
    for query in statements:
        await execute_write(session, query, p=project_id, timeout=write_timeout_s)


async def _write_file_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    rel_name: str,
    edges: list[tuple[str, str]],
) -> None:
    if not edges:
        return
    query = f"""
    UNWIND $batch AS edge
    MATCH (a:File {{id: edge.src}})
    MATCH (b:File {{id: edge.tgt}})
    MERGE (a)-[:{rel_name}]->(b)
    """
    for i in range(0, len(edges), batch_size):
        batch = [{"src": s, "tgt": t} for s, t in edges[i : i + batch_size]]
        async with write_semaphore:
            await execute_write(session, query, batch=batch, timeout=write_timeout_s)


async def _write_api_route_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    api_route_edges: list[tuple[str, str, str]],
    api_route_handler_edges: list[tuple[str, str, str]],
) -> None:
    if api_route_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (a:File {id: edge.src})
        MERGE (r:ApiRoute {project_id: edge.project_id, path: edge.path, method: edge.method})
        ON CREATE SET r.name = edge.method + ' ' + edge.path
        SET r.filepath = edge.path
        MERGE (a)-[:CALLS_API_ROUTE]->(r)
        """
        for i in range(0, len(api_route_edges), batch_size):
            batch = [{"src": s, "path": p, "method": m, "project_id": project_id} for s, p, m in api_route_edges[i : i + batch_size]]
            async with write_semaphore:
                await execute_write(session, query, batch=batch, timeout=write_timeout_s)
    if api_route_handler_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (r:ApiRoute {project_id: edge.project_id, path: edge.path, method: edge.method})
        MATCH (b:File {id: edge.tgt})
        MERGE (r)-[:HANDLED_BY]->(b)
        """
        for i in range(0, len(api_route_handler_edges), batch_size):
            batch = [{"path": p, "method": m, "tgt": t, "project_id": project_id} for p, m, t in api_route_handler_edges[i : i + batch_size]]
            async with write_semaphore:
                await execute_write(session, query, batch=batch, timeout=write_timeout_s)


async def _write_external_api_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    external_edges: list[tuple[str, str]],
) -> None:
    if not external_edges:
        return
    query = """
    UNWIND $batch AS edge
    MATCH (a:File {id: edge.src})
    MERGE (ext:ExternalAPI {project_id: edge.project_id, url: edge.url})
    ON CREATE SET ext.name = edge.url
    MERGE (a)-[:CALLS_API_EXTERNAL]->(ext)
    """
    for i in range(0, len(external_edges), batch_size):
        batch = [{"src": s, "url": u, "project_id": project_id} for s, u in external_edges[i : i + batch_size]]
        async with write_semaphore:
            await execute_write(session, query, batch=batch, timeout=write_timeout_s)


async def _write_resource_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    resource_edges: list[tuple[str, str, str, str | None, str]],
) -> None:
    if not resource_edges:
        return
    grouped: dict[str, list[tuple[str, str, str | None, str]]] = {}
    for src, rel, name, resource_path, resource_kind in resource_edges:
        grouped.setdefault(rel, []).append((src, name, resource_path, resource_kind))
    for rel_name, edges in grouped.items():
        query = f"""
        UNWIND $batch AS edge
        MATCH (a:File {{id: edge.src}})
        MERGE (res:Resource {{project_id: edge.project_id, name: edge.name, kind: edge.kind}})
        ON CREATE SET res.filepath = edge.name
        SET res.filepath = coalesce(edge.filepath, res.filepath)
        MERGE (a)-[:{rel_name}]->(res)
        """
        for i in range(0, len(edges), batch_size):
            batch = [
                {
                    "src": s,
                    "name": n,
                    "filepath": p,
                    "kind": k,
                    "project_id": project_id,
                }
                for s, n, p, k in edges[i : i + batch_size]
            ]
            async with write_semaphore:
                await execute_write(session, query, batch=batch, timeout=write_timeout_s)


async def _write_resource_backing_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    resource_edges: list[tuple[str, str, str, str | None, str]],
) -> None:
    backing_rows = []
    seen: set[tuple[str, str, str]] = set()
    for _, _, name, resource_path, resource_kind in resource_edges:
        if not resource_path:
            continue
        key = (name, resource_kind, resource_path)
        if key in seen:
            continue
        seen.add(key)
        backing_rows.append(
            {
                "name": name,
                "kind": resource_kind,
                "filepath": resource_path,
                "project_id": project_id,
            }
        )
    if not backing_rows:
        return
    query = """
    UNWIND $batch AS edge
    MATCH (res:Resource {project_id: edge.project_id, name: edge.name, kind: edge.kind})
    MATCH (f:File {project_id: edge.project_id, filepath: edge.filepath})
    MERGE (res)-[:BACKED_BY_FILE]->(f)
    """
    for i in range(0, len(backing_rows), batch_size):
        batch = backing_rows[i : i + batch_size]
        async with write_semaphore:
            await execute_write(session, query, batch=batch, timeout=write_timeout_s)


async def _write_xcode_target_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    xcode_targets: dict[str, dict[str, str]],
    xcode_file_edges: list[tuple[str, str]],
    xcode_resource_edges: list[tuple[str, str, str]],
) -> None:
    if xcode_targets:
        query = """
        UNWIND $batch AS edge
        MERGE (t:XcodeTarget {project_id: edge.project_id, target_id: edge.target_id})
        SET t.name = edge.name, t.project_file = edge.project_file
        """
        rows = [
            {
                "project_id": project_id,
                "target_id": target_id,
                "name": meta["name"],
                "project_file": meta["project_file"],
            }
            for target_id, meta in xcode_targets.items()
        ]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if xcode_file_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (t:XcodeTarget {project_id: edge.project_id, target_id: edge.target_id})
        MATCH (f:File {id: edge.file_id})
        MERGE (t)-[:BUNDLES_FILE]->(f)
        """
        rows = [{"project_id": project_id, "target_id": t, "file_id": f} for t, f in xcode_file_edges]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if xcode_resource_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (t:XcodeTarget {project_id: edge.project_id, target_id: edge.target_id})
        MATCH (r:Resource {project_id: edge.project_id, name: edge.name, kind: edge.kind})
        MERGE (r)-[:BUNDLED_IN_TARGET]->(t)
        """
        rows = [{"project_id": project_id, "target_id": t, "name": n, "kind": k} for t, n, k in xcode_resource_edges]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)


async def _write_xcode_workspace_scheme_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    workspace_rows: list[dict[str, str]],
    workspace_project_edges: list[tuple[str, str]],
    scheme_rows: list[dict[str, str]],
    scheme_target_edges: list[tuple[str, str]],
    scheme_file_edges: list[tuple[str, str]],
) -> None:
    if workspace_rows:
        query = """
        UNWIND $batch AS edge
        MERGE (w:XcodeWorkspace {project_id: edge.project_id, filepath: edge.workspace_path})
        SET w.name = edge.name
        """
        rows = [{"project_id": project_id, **row} for row in workspace_rows]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if workspace_project_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (w:XcodeWorkspace {project_id: edge.project_id, filepath: edge.workspace_path})
        MATCH (f:File {id: edge.file_id})
        MERGE (w)-[:REFERENCES_PROJECT]->(f)
        """
        rows = [{"project_id": project_id, "workspace_path": w, "file_id": f} for w, f in workspace_project_edges]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if scheme_rows:
        query = """
        UNWIND $batch AS edge
        MERGE (s:XcodeScheme {project_id: edge.project_id, filepath: edge.scheme_path})
        SET s.name = edge.name, s.container_path = edge.container_path
        """
        rows = [{"project_id": project_id, **row} for row in scheme_rows]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if scheme_target_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (s:XcodeScheme {project_id: edge.project_id, filepath: edge.scheme_path})
        MATCH (t:XcodeTarget {project_id: edge.project_id, target_id: edge.target_id})
        MERGE (s)-[:BUILDS_TARGET]->(t)
        """
        rows = [{"project_id": project_id, "scheme_path": s, "target_id": t} for s, t in scheme_target_edges]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if scheme_file_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (s:XcodeScheme {project_id: edge.project_id, filepath: edge.scheme_path})
        MATCH (f:File {id: edge.file_id})
        MERGE (s)-[:DEFINED_IN_FILE]->(f)
        """
        rows = [{"project_id": project_id, "scheme_path": s, "file_id": f} for s, f in scheme_file_edges]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)


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
        resource_edges = _collect_swift_resource_edges(project_path, file_facts, files)
        xcode_targets, xcode_file_edges, xcode_resource_edges = _collect_xcode_target_edges(
            project_path, files, resource_edges
        )
        workspace_rows, workspace_project_edges, scheme_rows, scheme_target_edges, scheme_file_edges = (
            _collect_xcode_workspace_scheme_edges(project_path, files, xcode_targets)
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
            await _clear_existing_edges(session, execute_write, project_id, write_timeout_s)
            await _write_file_edges(session, execute_write, write_semaphore, batch_size, write_timeout_s, "ASSET_LINKS", html_edges)
            await _write_file_edges(session, execute_write, write_semaphore, batch_size, write_timeout_s, "CALLS_API", api_edges)
            await _write_api_route_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, project_id, api_route_edges, api_route_handler_edges
            )
            await _write_external_api_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, project_id, external_edges
            )
            await _write_file_edges(session, execute_write, write_semaphore, batch_size, write_timeout_s, "CALLS_SERVICE", service_edges)
            await _write_file_edges(session, execute_write, write_semaphore, batch_size, write_timeout_s, "CALLS_DB", db_edges)
            await _write_resource_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, project_id, resource_edges
            )
            await _write_resource_backing_edges(
                session, execute_write, write_semaphore, batch_size, write_timeout_s, project_id, resource_edges
            )
            await _write_xcode_target_edges(
                session,
                execute_write,
                write_semaphore,
                batch_size,
                write_timeout_s,
                project_id,
                xcode_targets,
                xcode_file_edges,
                xcode_resource_edges,
            )
            await _write_xcode_workspace_scheme_edges(
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

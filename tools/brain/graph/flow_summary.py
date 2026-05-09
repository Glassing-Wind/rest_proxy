"""Flow summary helpers for UI/API/service graph views."""

from __future__ import annotations

import fnmatch
import os
import re
from collections import defaultdict

from _helpers import get_project_id, get_workspace_path
from tools.brain.graph_contract import node_label, rel_type
from tools.brain.graph import core as graph_core
from tools.brain.graph import flow_summary_apple

FILE_LABEL = node_label("file")
MODEL_LABEL = node_label("model")
EXTERNAL_API_LABEL = node_label("external_api")
API_ROUTE_LABEL = node_label("api_route")
CARGO_CRATE_LABEL = node_label("cargo_crate")
REL_ASSET_LINKS = rel_type("asset_links")
REL_CALLS_API_ROUTE = rel_type("calls_api_route")
REL_HANDLED_BY = rel_type("handled_by")
REL_CALLS_SERVICE = rel_type("calls_service")
REL_CALLS_DB_MODEL = rel_type("calls_db_model")
REL_CALLS_DB = rel_type("calls_db")
REL_CALLS_API_EXTERNAL = rel_type("calls_api_external")
REL_CALLS_API = rel_type("calls_api")
REL_IMPORTS = rel_type("imports")
REL_DEFINED_IN_FILE = rel_type("defined_in_file")
REL_FILE_GRAPH_LINK = rel_type("file_graph_link")


def _schema_cypher(text: str) -> str:
    replacements = {
        "__FILE__": FILE_LABEL,
        "__MODEL__": MODEL_LABEL,
        "__EXTERNAL_API__": EXTERNAL_API_LABEL,
        "__API_ROUTE__": API_ROUTE_LABEL,
        "__CARGO_CRATE__": CARGO_CRATE_LABEL,
        "__ASSET_LINKS__": REL_ASSET_LINKS,
        "__CALLS_API_ROUTE__": REL_CALLS_API_ROUTE,
        "__HANDLED_BY__": REL_HANDLED_BY,
        "__CALLS_SERVICE__": REL_CALLS_SERVICE,
        "__CALLS_DB_MODEL__": REL_CALLS_DB_MODEL,
        "__CALLS_DB__": REL_CALLS_DB,
        "__CALLS_API_EXTERNAL__": REL_CALLS_API_EXTERNAL,
        "__CALLS_API__": REL_CALLS_API,
        "__IMPORTS__": REL_IMPORTS,
        "__DEFINED_IN_FILE__": REL_DEFINED_IN_FILE,
        "__FILE_GRAPH_LINK__": REL_FILE_GRAPH_LINK,
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


_APP_FLOW_QUERY = _schema_cypher("""
CALL () {
    MATCH (ui:__FILE__ {project_id:$p})-[:__ASSET_LINKS__]->(js:__FILE__ {project_id:$p})
    MATCH (js)-[:__CALLS_API_ROUTE__]->(route:__API_ROUTE__ {project_id:$p})
    OPTIONAL MATCH (route)-[:__HANDLED_BY__]->(api:__FILE__ {project_id:$p})
    OPTIONAL MATCH (api)-[:__CALLS_SERVICE__]->(svc:__FILE__ {project_id:$p})
    OPTIONAL MATCH (svc)-[:__CALLS_DB_MODEL__]->(model:__MODEL__ {project_id:$p})
    OPTIONAL MATCH (svc)-[:__CALLS_DB__]->(schema:__FILE__ {project_id:$p, filepath:'prisma/schema.prisma'})
    OPTIONAL MATCH (js)-[:__CALLS_API_EXTERNAL__]->(ext:__EXTERNAL_API__ {project_id:$p})
    RETURN ui.filepath AS ui,
       ui.semantic_file_roles AS ui_roles,
       js.filepath AS js,
       js.semantic_file_roles AS js_roles,
       (coalesce(route.method, 'ANY') + ' ' + route.path) AS route,
       api.filepath AS api,
       api.semantic_file_roles AS api_roles,
       svc.filepath AS svc,
       svc.semantic_file_roles AS svc_roles,
       model.name AS model,
       schema.filepath AS schema,
       ext.url AS external
    UNION
    MATCH (ui:__FILE__ {project_id:$p})-[:__ASSET_LINKS__]->(js:__FILE__ {project_id:$p})
    WHERE NOT EXISTS {
        MATCH (js)-[:__CALLS_API_ROUTE__]->(:__API_ROUTE__ {project_id:$p})
    }
    MATCH (js)-[:__CALLS_API__]->(api:__FILE__ {project_id:$p})
    OPTIONAL MATCH (api)-[:__CALLS_SERVICE__]->(svc:__FILE__ {project_id:$p})
    OPTIONAL MATCH (svc)-[:__CALLS_DB_MODEL__]->(model:__MODEL__ {project_id:$p})
    OPTIONAL MATCH (svc)-[:__CALLS_DB__]->(schema:__FILE__ {project_id:$p, filepath:'prisma/schema.prisma'})
    OPTIONAL MATCH (js)-[:__CALLS_API_EXTERNAL__]->(ext:__EXTERNAL_API__ {project_id:$p})
    RETURN ui.filepath AS ui,
       ui.semantic_file_roles AS ui_roles,
       js.filepath AS js,
       js.semantic_file_roles AS js_roles,
       NULL AS route,
       api.filepath AS api,
       api.semantic_file_roles AS api_roles,
       svc.filepath AS svc,
       svc.semantic_file_roles AS svc_roles,
       model.name AS model,
       schema.filepath AS schema,
       ext.url AS external
    UNION
    MATCH (ui:__FILE__ {project_id:$p})-[:__CALLS_API_ROUTE__]->(route:__API_ROUTE__ {project_id:$p})
    OPTIONAL MATCH (route)-[:__HANDLED_BY__]->(api:__FILE__ {project_id:$p})
    OPTIONAL MATCH (api)-[:__CALLS_SERVICE__]->(svc:__FILE__ {project_id:$p})
    OPTIONAL MATCH (svc)-[:__CALLS_DB_MODEL__]->(model:__MODEL__ {project_id:$p})
    OPTIONAL MATCH (svc)-[:__CALLS_DB__]->(schema:__FILE__ {project_id:$p, filepath:'prisma/schema.prisma'})
    OPTIONAL MATCH (ui)-[:__CALLS_API_EXTERNAL__]->(ext:__EXTERNAL_API__ {project_id:$p})
    RETURN ui.filepath AS ui,
       ui.semantic_file_roles AS ui_roles,
       ui.filepath AS js,
       ui.semantic_file_roles AS js_roles,
       (coalesce(route.method, 'ANY') + ' ' + route.path) AS route,
       api.filepath AS api,
       api.semantic_file_roles AS api_roles,
       svc.filepath AS svc,
       svc.semantic_file_roles AS svc_roles,
       model.name AS model,
       schema.filepath AS schema,
       ext.url AS external
    UNION
    MATCH (ui:__FILE__ {project_id:$p})
    WHERE NOT EXISTS {
        MATCH (ui)-[:__CALLS_API_ROUTE__]->(:__API_ROUTE__ {project_id:$p})
    }
    MATCH (ui)-[:__CALLS_API__]->(api:__FILE__ {project_id:$p})
    OPTIONAL MATCH (api)-[:__CALLS_SERVICE__]->(svc:__FILE__ {project_id:$p})
    OPTIONAL MATCH (svc)-[:__CALLS_DB_MODEL__]->(model:__MODEL__ {project_id:$p})
    OPTIONAL MATCH (svc)-[:__CALLS_DB__]->(schema:__FILE__ {project_id:$p, filepath:'prisma/schema.prisma'})
    OPTIONAL MATCH (ui)-[:__CALLS_API_EXTERNAL__]->(ext:__EXTERNAL_API__ {project_id:$p})
    RETURN ui.filepath AS ui,
       ui.semantic_file_roles AS ui_roles,
       ui.filepath AS js,
       ui.semantic_file_roles AS js_roles,
       NULL AS route,
       api.filepath AS api,
       api.semantic_file_roles AS api_roles,
       svc.filepath AS svc,
       svc.semantic_file_roles AS svc_roles,
       model.name AS model,
       schema.filepath AS schema,
       ext.url AS external
}
RETURN ui, ui_roles, js, js_roles, route, api, api_roles, svc, svc_roles, model, schema, external
ORDER BY ui, js, route, api, svc, model, schema, external
LIMIT $limit
""")


_BACKEND_FLOW_QUERY = _schema_cypher("""
MATCH (api:__FILE__ {project_id:$p})
OPTIONAL MATCH (api)-[:__CALLS_SERVICE__]->(svc:__FILE__ {project_id:$p})
OPTIONAL MATCH (svc)-[:__CALLS_DB_MODEL__]->(model:__MODEL__ {project_id:$p})
OPTIONAL MATCH (svc)-[:__CALLS_DB__]->(schema:__FILE__ {project_id:$p, filepath:'prisma/schema.prisma'})
OPTIONAL MATCH (api)-[:__CALLS_API_EXTERNAL__]->(ext:__EXTERNAL_API__ {project_id:$p})
WHERE (svc IS NOT NULL OR model IS NOT NULL OR schema IS NOT NULL OR ext IS NOT NULL)
RETURN api.filepath AS api,
   api.semantic_file_roles AS api_roles,
   svc.filepath AS svc,
   svc.semantic_file_roles AS svc_roles,
   model.name AS model,
   schema.filepath AS schema,
   ext.url AS external
ORDER BY api, svc, model, schema, external
LIMIT $limit
""")

_BACKEND_FLOW_FALLBACK_QUERY = _schema_cypher("""
MATCH (entry:__FILE__ {project_id:$p})
OPTIONAL MATCH (entry)-[:__CALLS_SERVICE__]->(svc:__FILE__ {project_id:$p})
OPTIONAL MATCH (entry)-[:__CALLS_DB_MODEL__]->(direct_model:__MODEL__ {project_id:$p})
OPTIONAL MATCH (entry)-[:__CALLS_DB__]->(direct_schema:__FILE__ {project_id:$p})
OPTIONAL MATCH (entry)-[:__CALLS_API_EXTERNAL__]->(direct_ext:__EXTERNAL_API__ {project_id:$p})
OPTIONAL MATCH (svc)-[:__CALLS_DB_MODEL__]->(svc_model:__MODEL__ {project_id:$p})
OPTIONAL MATCH (svc)-[:__CALLS_DB__]->(svc_schema:__FILE__ {project_id:$p})
OPTIONAL MATCH (svc)-[:__CALLS_API_EXTERNAL__]->(svc_ext:__EXTERNAL_API__ {project_id:$p})
WHERE (
    direct_model IS NOT NULL
    OR direct_schema IS NOT NULL
    OR direct_ext IS NOT NULL
    OR svc IS NOT NULL
    OR svc_model IS NOT NULL
    OR svc_schema IS NOT NULL
    OR svc_ext IS NOT NULL
)
RETURN entry.filepath AS api,
   entry.semantic_file_roles AS api_roles,
   svc.filepath AS svc,
   svc.semantic_file_roles AS svc_roles,
   coalesce(svc_model.name, direct_model.name) AS model,
   coalesce(svc_schema.filepath, direct_schema.filepath) AS schema,
   coalesce(svc_ext.url, direct_ext.url) AS external
ORDER BY api, svc, model, schema, external
LIMIT $limit
""")

_BACKEND_ROUTE_QUERY = _schema_cypher("""
MATCH (route:__API_ROUTE__ {project_id:$p})-[:__HANDLED_BY__]->(api:__FILE__ {project_id:$p})
RETURN api.filepath AS api,
       collect(distinct (coalesce(route.method, 'ANY') + ' ' + route.path)) AS routes
ORDER BY api
""")

_BACKEND_IMPORT_FALLBACK_QUERY = _schema_cypher("""
MATCH (api:__FILE__ {project_id:$p})-[:__IMPORTS__]->(dep:__FILE__ {project_id:$p})
RETURN api.filepath AS api,
       api.semantic_file_roles AS api_roles,
       dep.filepath AS dep,
       dep.semantic_file_roles AS dep_roles
UNION
MATCH (api:__FILE__ {project_id:$p})-[:__FILE_GRAPH_LINK__]->(dep:__FILE__ {project_id:$p})
RETURN api.filepath AS api,
       api.semantic_file_roles AS api_roles,
       dep.filepath AS dep,
       dep.semantic_file_roles AS dep_roles
ORDER BY api, dep
""")


async def _load_cargo_crate_roots(session, project_id: str):
    schema_labels = await graph_core._execute_read(
        session,
        """
        CALL db.labels() YIELD label
        RETURN collect(label) AS labels
        """,
        op="backend_flow_cargo_schema_labels",
    )
    labels = set(schema_labels[0].get("labels") or []) if schema_labels else set()
    if CARGO_CRATE_LABEL not in labels:
        return []
    return await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (c:__CARGO_CRATE__ {project_id:$p})-[:__DEFINED_IN_FILE__]->(mf:__FILE__ {project_id:$p})
        RETURN c.name AS crate,
               c.crate_name AS crate_name,
               mf.filepath AS manifest_path
        ORDER BY size(mf.filepath) DESC, c.name
        """),
        p=project_id,
        op="backend_flow_cargo_crates",
    )


def _cargo_manifest_dir(manifest_path: str | None) -> str:
    if not manifest_path:
        return ""
    return manifest_path[:-len("Cargo.toml")] if manifest_path.endswith("Cargo.toml") else manifest_path


def _match_cargo_crate(filepath: str | None, crate_rows) -> tuple[str | None, str | None]:
    if not filepath:
        return None, None
    for row in crate_rows:
        manifest_path = row.get("manifest_path")
        crate_root = _cargo_manifest_dir(manifest_path)
        if crate_root and filepath.startswith(crate_root):
            return row.get("crate"), row.get("crate_name")
    return None, None


def _format_backend_flow_row(api, route, svc, model, schema, external, api_crate=None, svc_crate=None) -> str:
    parts = [value for value in [api, route, svc, model, schema, external] if value]
    flow = " -> ".join(parts)
    crate_bits = []
    if api_crate:
        crate_bits.append(f"api_crate={api_crate}")
    if svc_crate and svc_crate != api_crate:
        crate_bits.append(f"service_crate={svc_crate}")
    if not crate_bits:
        return flow
    return f"[{', '.join(crate_bits)}] {flow}"


def _group_backend_flow_rows(rows: list[dict], limit: int) -> list[str]:
    ordered = sorted(
        rows,
        key=lambda r: (
            r.get("api_crate") or r.get("svc_crate") or "~",
            0 if r.get("api_crate") == r.get("svc_crate") and r.get("api_crate") else 1,
            r.get("api") or "",
            r.get("svc") or "",
            r.get("model") or "",
            r.get("schema") or "",
            r.get("external") or "",
        ),
    )
    groups: dict[str, list[str]] = {}
    for row in ordered:
        group = row.get("api_crate") or row.get("svc_crate") or "(unowned)"
        groups.setdefault(group, [])
        groups[group].append(
            _format_backend_flow_row(
                row["api"],
                row.get("route"),
                row["svc"],
                row["model"],
                row["schema"],
                row["external"],
                api_crate=row["api_crate"],
                svc_crate=row["svc_crate"],
            )
        )
    output: list[str] = []
    emitted = 0
    for group, flows in groups.items():
        unique = list(dict.fromkeys(flows))
        if not unique:
            continue
        output.append(f"Crate: {group}")
        for flow in unique:
            if limit and emitted >= limit:
                break
            output.append(f"- {flow}")
            emitted += 1
        if limit and emitted >= limit:
            break
    return output


def _format_backend_flow_empty_message(crate_rows) -> str:
    if not crate_rows:
        return "No API → Service → DB paths found."
    crate_names = [row.get("crate") or row.get("crate_name") for row in crate_rows]
    crate_names = [name for name in crate_names if name]
    preview = ", ".join(crate_names[:4])
    if len(crate_names) > 4:
        preview += f", +{len(crate_names) - 4} more"
    detail = f" Cargo crates detected: {preview}." if preview else ""
    return (
        "No API → Service → DB paths found. "
        "This workspace looks crate/library-oriented rather than app-backend shaped."
        + detail
        + " Prefer project overview, code importance, related files, communities, and directory snapshots here."
    )


def _app_flow_focus_lines(raw_rows, *, grouped_by_ui: bool) -> list[str]:
    if not raw_rows:
        return []
    ui, js, route, api, svc, model, schema, external = raw_rows[0]
    lines: list[str] = []
    if ui:
        if route or api:
            lines.append(
                f"- start with `{ui}` because it has the clearest UI entrypoint into `{route or api}`"
            )
        else:
            lines.append(f"- start with `{ui}` because it is the strongest surviving UI entrypoint")
    if svc or model or schema:
        deepest = svc or model or schema
        lines.append(f"- inspect `{deepest}` next because it is the deepest concrete backend dependency on this path")
    elif external:
        lines.append(f"- inspect external dependency `{external}` next because this path exits the repo there")
    if grouped_by_ui and js and js != ui:
        lines.append(f"- review `{js}` alongside the UI file because it is the first wiring hop")
    return lines[:3]


def _backend_flow_focus_lines(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    top = rows[0]
    lines = []
    if top.get("route") and top.get("api"):
        lines.append(f"- start with `{top['route']}` in `{top['api']}` because it is the clearest API entrypoint")
    elif top.get("api"):
        lines.append(f"- start with `{top['api']}` because it is the strongest backend entrypoint")
    deepest = top.get("svc") or top.get("model") or top.get("schema") or top.get("external")
    if deepest:
        lines.append(f"- inspect `{deepest}` next because it is the deepest downstream dependency on that path")
    if top.get("api_crate") and top.get("svc_crate") and top.get("api_crate") != top.get("svc_crate"):
        lines.append(
            f"- pay attention to the crate boundary `{top['api_crate']}` → `{top['svc_crate']}` because it is a likely integration seam"
        )
    return lines[:3]


def _is_backend_api_path(filepath: str | None) -> bool:
    if not filepath:
        return False
    normalized = filepath.replace("\\", "/")
    return (
        "/api/" in normalized
        or "/routes/" in normalized
        or normalized.startswith("api/")
        or normalized.startswith("app/api/")
        or normalized.endswith("/app.py")
        or normalized.endswith("/main.py")
        or normalized.endswith("/server.py")
    )


def _classify_backend_dep(filepath: str | None) -> str | None:
    if not filepath:
        return None
    normalized = filepath.replace("\\", "/").lower()
    if any(
        token in normalized
        for token in (
            "/services/",
            "/service/",
            "/retrieval/",
            "/ingestion/",
            "/handlers/",
            "/handler/",
        )
    ) or (
        normalized.endswith("/handlers.py")
        or normalized.endswith("/handler.py")
        or "/handlers_" in normalized
        or normalized.endswith("_handler.py")
    ):
        return "svc"
    if (
        "/models/" in normalized
        or normalized.endswith("/models.py")
        or normalized.endswith("/model.py")
    ):
        return "model"
    if any(token in normalized for token in ("/db/", "/database/", "/repositories/", "/repository/")):
        return "schema"
    return None


def _module_name_from_filepath(filepath: str | None) -> str | None:
    if not filepath:
        return None
    normalized = filepath.replace("\\", "/")
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    if normalized.endswith("/__init__"):
        normalized = normalized[:-9]
    module_name = normalized.replace("/", ".").strip(".")
    return module_name or None


def _extract_python_import_map(source_text: str) -> dict[str, str]:
    symbol_to_module: dict[str, str] = {}
    multiline_pattern = re.compile(
        r"(?ms)^\s*from\s+([A-Za-z0-9_\.]+)\s+import\s*\((.*?)\)\s*$"
    )
    for match in multiline_pattern.finditer(source_text):
        module_name = match.group(1).strip()
        raw_symbols = match.group(2) or ""
        for item in raw_symbols.split(","):
            symbol = item.strip()
            if not symbol:
                continue
            if " as " in symbol:
                symbol = symbol.split(" as ", 1)[-1].strip()
            if symbol:
                symbol_to_module[symbol] = module_name
    for match in re.finditer(
        r"(?m)^\s*from\s+([A-Za-z0-9_\.]+)\s+import\s+([A-Za-z0-9_, ]+)$",
        source_text,
    ):
        module_name = match.group(1).strip()
        raw_symbols = match.group(2)
        for item in raw_symbols.split(","):
            symbol = item.strip()
            if not symbol or " as " in symbol:
                symbol = symbol.split(" as ", 1)[-1].strip()
            if symbol:
                symbol_to_module[symbol] = module_name
    return symbol_to_module


def _extract_fastapi_route_blocks(source_text: str) -> list[dict[str, str | None]]:
    pattern = re.compile(
        r"(?ms)^\s*@([A-Za-z_][A-Za-z0-9_]*)\.(get|post|put|patch|delete|options|head)\(\s*([\"'])(.*?)\3.*?\)\s*"
        r"\n\s*(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\):\s*\n(.*?)(?=^\s*@[A-Za-z_][A-Za-z0-9_]*\.(?:get|post|put|patch|delete|options|head)\(|\Z)"
    )
    routes: list[dict[str, str | None]] = []
    for match in pattern.finditer(source_text):
        method = match.group(2).upper()
        path = match.group(4).strip()
        signature = match.group(6) or ""
        body = match.group(7) or ""
        routes.append(
            {
                "route": f"{method} {path}",
                "signature": signature,
                "body": body,
            }
        )
    return routes


def _select_python_route_dep(
    route_text: str,
    symbol_to_kind: dict[str, tuple[str, str]],
    preferred_kind: str,
) -> str | None:
    for symbol, (kind, dep_path) in symbol_to_kind.items():
        if kind != preferred_kind or not dep_path:
            continue
        if re.search(rf"\b{re.escape(symbol)}\b", route_text):
            return dep_path
    return None


async def _load_backend_import_fallback_rows(session, project_id: str) -> list[dict]:
    rows = await graph_core._execute_read(
        session,
        _BACKEND_IMPORT_FALLBACK_QUERY,
        p=project_id,
        op="get_backend_flow_summary_import_fallback",
    )
    deduped = []
    seen = set()
    for row in rows:
        api = row.get("api")
        dep = row.get("dep")
        if not api or not dep:
            continue
        key = (api, dep)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


async def _build_python_backend_flow_fallback(
    *,
    session,
    project_id: str,
    workspace_id: str,
    include_tests: bool,
) -> list[dict]:
    import_rows = await _load_backend_import_fallback_rows(session, project_id)
    if not import_rows:
        return []

    deps_by_api: dict[str, list[str]] = defaultdict(list)
    api_roles_by_path: dict[str, object] = {}
    for row in import_rows:
        api = row.get("api")
        dep = row.get("dep")
        if not api or not dep:
            continue
        deps_by_api[api].append(dep)
        api_roles_by_path.setdefault(api, row.get("api_roles"))

    workspace_path = get_workspace_path(workspace_id)
    if not workspace_path:
        return []

    fallback_rows: list[dict] = []
    for api_path, deps in sorted(deps_by_api.items()):
        if not include_tests and _is_low_signal_flow_path(api_path, api_roles_by_path.get(api_path)):
            continue
        api_abs = os.path.join(workspace_path, api_path)
        try:
            with open(api_abs, "r", encoding="utf-8") as fh:
                source_text = fh.read()
        except Exception:
            continue
        if "APIRouter" not in source_text and "@router." not in source_text and "FastAPI" not in source_text:
            continue

        symbol_to_module = _extract_python_import_map(source_text)
        module_to_dep = {
            _module_name_from_filepath(dep): dep
            for dep in deps
            if _module_name_from_filepath(dep)
        }
        symbol_to_kind: dict[str, tuple[str, str]] = {}
        for symbol, module_name in symbol_to_module.items():
            dep_path = module_to_dep.get(module_name)
            dep_kind = _classify_backend_dep(dep_path)
            if dep_path and dep_kind:
                symbol_to_kind[symbol] = (dep_kind, dep_path)

        db_dep = None
        for dep in deps:
            if _classify_backend_dep(dep) == "schema":
                db_dep = dep
                break

        for route_block in _extract_fastapi_route_blocks(source_text):
            route_text = f"{route_block.get('signature') or ''}\n{route_block.get('body') or ''}"
            service_dep = _select_python_route_dep(route_text, symbol_to_kind, "svc")
            model_dep = _select_python_route_dep(route_text, symbol_to_kind, "model")
            schema_dep = None
            if db_dep and (
                "Depends(get_db)" in route_text
                or re.search(r"\bdb\s*:\s*Session\b", route_text)
                or "db." in route_text
            ):
                schema_dep = db_dep
            if not any([route_block.get("route"), service_dep, model_dep, schema_dep]):
                continue
            fallback_rows.append(
                {
                    "api": api_path,
                    "route": route_block.get("route"),
                    "svc": service_dep,
                    "model": model_dep,
                    "schema": schema_dep,
                    "external": None,
                }
            )
    return fallback_rows


async def _load_backend_api_routes(session, project_id: str) -> dict[str, list[str]]:
    rows = await graph_core._execute_read(
        session,
        _BACKEND_ROUTE_QUERY,
        p=project_id,
        op="get_backend_flow_summary_routes",
    )
    return {
        row.get("api"): sorted(route for route in (row.get("routes") or []) if route)
        for row in rows
        if row.get("api")
    }


def _expand_backend_rows_by_route_context(rows: list[dict], api_routes: dict[str, list[str]]) -> list[dict]:
    expanded: list[dict] = []
    for row in rows:
        if row.get("route"):
            expanded.append(row)
            continue
        api = row.get("api")
        routes = api_routes.get(api) or []
        if not routes:
            expanded.append({**row, "route": None})
            continue
        if len(routes) == 1:
            expanded.append({**row, "route": routes[0]})
            continue
        for route in routes:
            expanded.append(
                {
                    **row,
                    "route": route,
                    "svc": None,
                    "model": None,
                    "schema": None,
                    "external": None,
                }
            )
    return expanded


async def _resolve_entry_files(session, project_id: str, entry_files, entry_glob):
    if entry_files or not entry_glob:
        return entry_files
    ui_result = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (f:__FILE__ {project_id:$p})
        WHERE f.filepath ENDS WITH '.html' OR f.filepath ENDS WITH '.astro'
        RETURN f.filepath AS ui
        """),
        p=project_id,
        op="get_app_flow_summary_entry_glob",
    )
    ui_paths = [row.get("ui") for row in ui_result if row.get("ui")]
    return [path for path in ui_paths if fnmatch.fnmatch(path, entry_glob)]


def _filtered_app_rows(raw_rows, entry_files, ui_contains, model_contains, service_contains):
    rows = raw_rows
    if entry_files:
        rows = [r for r in rows if r[0] and r[0] in entry_files]
    if ui_contains:
        rows = [r for r in rows if r[0] and ui_contains in r[0]]
    if model_contains:
        rows = [r for r in rows if r[5] and model_contains in r[5]]
    if service_contains:
        rows = [r for r in rows if r[4] and service_contains in r[4]]
    return rows


def _is_test_like_path(filepath: str | None) -> bool:
    if not filepath:
        return False
    return (
        filepath.startswith("tests/")
        or "/tests/" in filepath
        or filepath.startswith("test/")
        or "/test/" in filepath
        or filepath.startswith("examples/")
        or "/examples/" in filepath
        or filepath.startswith("fixtures/")
        or "/fixtures/" in filepath
        or filepath.startswith("benchmark/")
        or "/benchmark/" in filepath
        or filepath.startswith("benchmarks/")
        or "/benchmarks/" in filepath
        or filepath.startswith("docs/")
        or "/docs/" in filepath
        or filepath.startswith("e2e/")
        or "/e2e/" in filepath
        or "__tests__" in filepath
        or ".test." in filepath
        or ".spec." in filepath
        or filepath.endswith("_test.py")
        or filepath.endswith("_spec.rb")
    )


def _file_roles_present(raw_roles) -> bool:
    return isinstance(raw_roles, list)


def _normalize_file_roles(raw_roles) -> set[str]:
    if not _file_roles_present(raw_roles):
        return set()
    return {
        str(role).strip().lower()
        for role in raw_roles
        if str(role).strip()
    }


def _is_low_signal_flow_path(filepath: str | None, raw_roles) -> bool:
    roles = _normalize_file_roles(raw_roles)
    if {"test_surface", "example_surface", "benchmark_surface", "docs_surface", "support_surface"} & roles:
        return True
    if _file_roles_present(raw_roles):
        return False
    return _is_test_like_path(filepath)


def _prefer_concrete_app_rows(raw_rows):
    concrete_keys = {
        (ui, js, api, svc, model, schema, external)
        for ui, js, route, api, svc, model, schema, external in raw_rows
        if route
    }
    filtered = []
    for row in raw_rows:
        ui, js, route, api, svc, model, schema, external = row
        if route is None and (ui, js, api, svc, model, schema, external) in concrete_keys:
            continue
        filtered.append(row)
    return filtered


def _format_app_flow_row(ui, js, route, api, svc, model, schema, external) -> str:
    parts = []
    for value in [ui, js, route, api, svc, model, schema, external]:
        if not value:
            continue
        if parts and parts[-1] == value:
            continue
        parts.append(value)
    return " -> ".join(parts)


def _row_has_app_signal(row) -> bool:
    return any(row[idx] for idx in (2, 3, 4, 5, 6, 7))


def _collapse_ambiguous_app_rows(raw_rows):
    grouped: dict[tuple[str | None, str | None, str | None, str | None, str | None], dict[str, set[str]]] = {}
    for ui, js, route, api, svc, model, schema, external in raw_rows:
        key = (ui, js, route, api, external)
        bucket = grouped.setdefault(key, {"svc": set(), "model": set(), "schema": set()})
        if svc:
            bucket["svc"].add(svc)
        if model:
            bucket["model"].add(model)
        if schema:
            bucket["schema"].add(schema)

    collapsed = []
    for (ui, js, route, api, external), bucket in grouped.items():
        svc_values = sorted(bucket["svc"])
        model_values = sorted(bucket["model"])
        schema_values = sorted(bucket["schema"])

        svc = svc_values[0] if len(svc_values) == 1 else (f"{len(svc_values)} services" if len(svc_values) > 1 else None)
        model = model_values[0] if len(model_values) == 1 else (f"{len(model_values)} models" if len(model_values) > 1 else None)
        schema = schema_values[0] if len(schema_values) == 1 else (f"{len(schema_values)} schemas" if len(schema_values) > 1 else None)
        collapsed.append((ui, js, route, api, svc, model, schema, external))

    collapsed.sort(key=lambda row: tuple("" if value is None else str(value) for value in row))
    return collapsed


async def _load_api_route_counts(session, project_id: str) -> dict[str, int]:
    rows = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (route:__API_ROUTE__ {project_id:$p})-[:__HANDLED_BY__]->(api:__FILE__ {project_id:$p})
        RETURN api.filepath AS api, count(route) AS route_count
        ORDER BY api
        """),
        p=project_id,
        op="get_app_flow_summary_api_route_counts",
    )
    return {
        row.get("api"): int(row.get("route_count") or 0)
        for row in rows
        if row.get("api")
    }


def _suppress_coarse_route_service_rows(raw_rows, api_route_counts: dict[str, int]):
    adjusted = []
    for ui, js, route, api, svc, model, schema, external in raw_rows:
        if route and api and api_route_counts.get(api, 0) > 1:
            adjusted.append((ui, js, route, api, None, None, None, external))
            continue
        adjusted.append((ui, js, route, api, svc, model, schema, external))
    return adjusted


def _normalize_route_literal(path: str | None) -> str | None:
    if not path:
        return None
    normalized = path.split("?", 1)[0].split("#", 1)[0].strip()
    normalized = re.sub(r"\$\{[^}]+\}", "", normalized)
    normalized = re.sub(r"/{2,}", "/", normalized)
    normalized = normalized.rstrip("/")
    return normalized or None


def _extract_literal_api_paths(source_text: str) -> list[str]:
    matches = re.findall(r"([\"'`])(/api/[^\"'`\n]+)\1", source_text)
    seen: list[str] = []
    for _, raw_path in matches:
        normalized = _normalize_route_literal(raw_path)
        if normalized and normalized not in seen:
            seen.append(normalized)
    return seen


async def _load_route_catalog(session, project_id: str):
    result = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (route:__API_ROUTE__ {project_id:$p})
        OPTIONAL MATCH (route)-[:__HANDLED_BY__]->(api:__FILE__ {project_id:$p})
        RETURN route.path AS path,
               route.method AS method,
               api.filepath AS api
        ORDER BY path, method, api
        """),
        p=project_id,
        op="get_app_flow_summary_route_catalog",
    )
    catalog: dict[str, list[tuple[str | None, str | None]]] = {}
    for row in result:
        path = _normalize_route_literal(row.get("path"))
        if not path:
            continue
        catalog.setdefault(path, [])
        candidate = (row.get("method"), row.get("api"))
        if candidate not in catalog[path]:
            catalog[path].append(candidate)
    return catalog


async def _load_asset_js_pairs(session, project_id: str):
    result = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (ui:__FILE__ {project_id:$p})-[:__ASSET_LINKS__]->(js:__FILE__ {project_id:$p})
        WHERE js.filepath ENDS WITH '.js'
           OR js.filepath ENDS WITH '.ts'
           OR js.filepath ENDS WITH '.tsx'
           OR js.filepath ENDS WITH '.jsx'
        RETURN ui.filepath AS ui,
               ui.semantic_file_roles AS ui_roles,
               js.filepath AS js,
               js.semantic_file_roles AS js_roles
        ORDER BY ui, js
        """),
        p=project_id,
        op="get_app_flow_summary_asset_pairs",
    )
    return [
        (row.get("ui"), row.get("ui_roles"), row.get("js"), row.get("js_roles"))
        for row in result
        if row.get("ui") and row.get("js")
    ]


async def _build_app_flow_literal_fallback(session, project_id: str, workspace_id: str, raw_rows):
    js_pairs = sorted(
        {
            (ui, None, js, None)
            for ui, js, route, api, svc, model, schema, external in raw_rows
            if ui and js and js.endswith((".js", ".ts", ".tsx", ".jsx"))
        }
    )
    if not js_pairs:
        js_pairs = await _load_asset_js_pairs(session, project_id)
        js_pairs = [
            (ui, ui_roles, js, js_roles)
            for ui, ui_roles, js, js_roles in js_pairs
            if not _is_low_signal_flow_path(ui, ui_roles)
            and not _is_low_signal_flow_path(js, js_roles)
        ]
    if not js_pairs:
        return []

    route_catalog = await _load_route_catalog(session, project_id)
    if not route_catalog:
        return []

    fallback_rows = []
    workspace_path = get_workspace_path(workspace_id)
    if not workspace_path:
        return []
    for ui, _ui_roles, js, _js_roles in js_pairs:
        js_abs = os.path.join(workspace_path, js)
        try:
            with open(js_abs, "r", encoding="utf-8") as fh:
                source_text = fh.read()
        except Exception:
            continue
        matched_rows = []
        unmatched_rows = []
        for path in _extract_literal_api_paths(source_text):
            targets = route_catalog.get(path) or []
            if not targets:
                unmatched_rows.append((ui, js, f"ANY {path}", None, None, None, None, None))
                continue
            for method, api in targets:
                route = f"{method or 'ANY'} {path}"
                matched_rows.append((ui, js, route, api, None, None, None, None))
        fallback_rows.extend(matched_rows or unmatched_rows)
    return fallback_rows


async def _coverage_lines(session, project_id: str) -> list[str]:
    coverage_result = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (f:__FILE__ {project_id:$p})
        WITH collect(f) AS files
        RETURN
          size([f IN files WHERE f.filepath ENDS WITH '.html' OR f.filepath ENDS WITH '.astro']) AS ui_files,
          size([f IN files WHERE f.filepath ENDS WITH '.js' OR f.filepath ENDS WITH '.ts' OR f.filepath ENDS WITH '.tsx']) AS js_files
        """),
        p=project_id,
        op="get_app_flow_summary_coverage_files",
    )
    edge_result = await graph_core._execute_read(
        session,
        _schema_cypher("MATCH (:__FILE__ {project_id:$p})-[r:__ASSET_LINKS__]->() RETURN count(r) AS asset_links"),
        p=project_id,
        op="get_app_flow_summary_coverage_assets",
    )
    api_result = await graph_core._execute_read(
        session,
        _schema_cypher("MATCH (:__FILE__ {project_id:$p})-[r:__CALLS_API__]->() RETURN count(r) AS api_links"),
        p=project_id,
        op="get_app_flow_summary_coverage_api",
    )
    svc_result = await graph_core._execute_read(
        session,
        _schema_cypher("MATCH (:__FILE__ {project_id:$p})-[r:__CALLS_SERVICE__]->() RETURN count(r) AS service_links"),
        p=project_id,
        op="get_app_flow_summary_coverage_service",
    )
    db_result = await graph_core._execute_read(
        session,
        _schema_cypher("MATCH (:__FILE__ {project_id:$p})-[r:__CALLS_DB__]->() RETURN count(r) AS db_links"),
        p=project_id,
        op="get_app_flow_summary_coverage_db",
    )
    route_result = await graph_core._execute_read(
        session,
        _schema_cypher("MATCH (:__FILE__ {project_id:$p})-[r:__CALLS_API_ROUTE__]->() RETURN count(r) AS api_route_links"),
        p=project_id,
        op="get_app_flow_summary_coverage_api_routes",
    )
    file_graph_result = await graph_core._execute_read(
        session,
        _schema_cypher("MATCH (:__FILE__ {project_id:$p})-[r:__FILE_GRAPH_LINK__]->() RETURN count(r) AS file_graph_links"),
        p=project_id,
        op="get_app_flow_summary_coverage_file_graph",
    )
    ui_files = coverage_result[0].get("ui_files") if coverage_result else 0
    js_files = coverage_result[0].get("js_files") if coverage_result else 0
    asset_links = edge_result[0].get("asset_links") if edge_result else 0
    api_links = api_result[0].get("api_links") if api_result else 0
    service_links = svc_result[0].get("service_links") if svc_result else 0
    db_links = db_result[0].get("db_links") if db_result else 0
    api_route_links = route_result[0].get("api_route_links") if route_result else 0
    file_graph_links = file_graph_result[0].get("file_graph_links") if file_graph_result else 0
    return [
        "Coverage: "
        f"ui_files={ui_files} js_files={js_files} "
        f"asset_links={asset_links} api_links={api_links} "
        f"api_route_links={api_route_links} service_links={service_links} "
        f"db_links={db_links} file_graph_links={file_graph_links}"
    ]


async def get_app_flow_summary_impl(
    *,
    driver,
    neo4j_db: str,
    workspace_id: str,
    ui_contains: str | None = None,
    entry_files: list[str] | None = None,
    entry_glob: str | None = None,
    model_contains: str | None = None,
    service_contains: str | None = None,
    include_tests: bool = False,
    limit: int = 20,
    as_table: bool = False,
    group_by_ui: bool = True,
    include_coverage: bool = True,
    max_per_ui: int = 12,
    expand_api_calls: bool = False,
) -> str:
    project_id = get_project_id(workspace_id)
    query_limit = max(limit, 50)
    if any([ui_contains, entry_files, entry_glob, model_contains, service_contains, expand_api_calls]):
        query_limit = max(limit * 10, 300)

    rows: list[str] = []
    coverage_lines: list[str] = []
    ui_routes: dict[str, list[str]] = {}
    async with driver.session(database=neo4j_db) as session:
        entry_files = await _resolve_entry_files(session, project_id, entry_files, entry_glob)
        if include_coverage:
            coverage_lines = await _coverage_lines(session, project_id)
        api_route_counts = await _load_api_route_counts(session, project_id)

        result = await graph_core._execute_read(
            session,
            _APP_FLOW_QUERY,
            p=project_id,
            include_tests=include_tests,
            limit=query_limit,
            op="get_app_flow_summary",
        )
        app_records = [dict(row) for row in result]
        raw_rows = _filtered_app_rows(
            [
                (
                    row.get("ui"),
                    row.get("js"),
                    row.get("route"),
                    row.get("api"),
                    row.get("svc"),
                    row.get("model"),
                    row.get("schema"),
                    row.get("external"),
                )
                for row in app_records
            ],
            entry_files=entry_files,
            ui_contains=ui_contains,
            model_contains=model_contains,
            service_contains=service_contains,
        )
        if not include_tests:
            raw_rows = []
            for record in app_records:
                row_tuple = (
                    record.get("ui"),
                    record.get("js"),
                    record.get("route"),
                    record.get("api"),
                    record.get("svc"),
                    record.get("model"),
                    record.get("schema"),
                    record.get("external"),
                )
                if row_tuple not in raw_rows and row_tuple in _filtered_app_rows(
                    [row_tuple],
                    entry_files=entry_files,
                    ui_contains=ui_contains,
                    model_contains=model_contains,
                    service_contains=service_contains,
                ):
                    if any(
                        _is_low_signal_flow_path(path, roles)
                        for path, roles in (
                            (record.get("ui"), record.get("ui_roles")),
                            (record.get("js"), record.get("js_roles")),
                            (record.get("api"), record.get("api_roles")),
                            (record.get("svc"), record.get("svc_roles")),
                        )
                    ):
                        continue
                    raw_rows.append(row_tuple)
        if raw_rows and not any(_row_has_app_signal(row) for row in raw_rows):
            raw_rows = []
        if not raw_rows:
            raw_rows = await _build_app_flow_literal_fallback(
                session,
                project_id,
                workspace_id,
                raw_rows,
            )
        raw_rows = _suppress_coarse_route_service_rows(raw_rows, api_route_counts)
        raw_rows = _prefer_concrete_app_rows(raw_rows)
        raw_rows = _collapse_ambiguous_app_rows(raw_rows)

        if expand_api_calls:
            ui_candidates = sorted({row[0] for row in raw_rows if row[0]})
            for ui_path in ui_candidates:
                api_calls_result = await graph_core._execute_read(
                    session,
                    _schema_cypher("""
                    MATCH (ui:__FILE__ {project_id:$p})-[:__ASSET_LINKS__]->(js:__FILE__ {project_id:$p})
                    MATCH (js)-[:__CALLS_API_ROUTE__]->(route:__API_ROUTE__ {project_id:$p})
                    WHERE ui.filepath = $ui_path
                    RETURN collect(distinct (coalesce(route.method, 'ANY') + ' ' + route.path)) AS routes
                    """),
                    p=project_id,
                    ui_path=ui_path,
                    op="get_app_flow_summary_api_routes",
                )
                routes = api_calls_result[0].get("routes") if api_calls_result else []
                ui_routes[ui_path] = sorted(route for route in routes if route)

    if not raw_rows:
        return "No UI → API → Service → DB paths found."

    focus_lines = _app_flow_focus_lines(raw_rows, grouped_by_ui=group_by_ui)

    if as_table:
        rows = [
            "| UI | JS | Route | API | Service | Model | Schema | External |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for ui, js, route, api, svc, model, schema, external in raw_rows:
            rows.append(
                f"| {ui or ''} | {js or ''} | {route or ''} | {api or ''} | {svc or ''} | {model or ''} | {schema or ''} | {external or ''} |"
            )
    elif group_by_ui:
        grouped: dict[str, list[str]] = {}
        for ui, js, route, api, svc, model, schema, external in raw_rows:
            if not ui:
                continue
            flow = _format_app_flow_row(ui, js, route, api, svc, model, schema, external)
            grouped.setdefault(ui, []).append(flow)
        ordered_uis = entry_files or sorted(grouped.keys())
        for ui in ordered_uis:
            flows = grouped.get(ui, [])
            if not flows:
                continue
            rows.append(f"UI: {ui}")
            if expand_api_calls:
                routes = ui_routes.get(ui) or []
                if routes:
                    rows.append(f"API routes: {', '.join(routes)}")
            rows.extend(list(dict.fromkeys(flows))[:max_per_ui])
    else:
        rows = [
            _format_app_flow_row(ui, js, route, api, svc, model, schema, external)
            for ui, js, route, api, svc, model, schema, external in raw_rows
        ]

    if limit and len(rows) > limit:
        rows = rows[:limit]
    output = []
    output.append("Use this to decide which UI entrypoints reach real APIs or services and which path to inspect first.")
    if focus_lines:
        output.append("")
        output.append("Inspect First:")
        output.extend(focus_lines)
    if coverage_lines:
        output.append("")
        output.extend(coverage_lines)
    output.extend(list(dict.fromkeys(rows)))
    return "\n".join(output)


async def get_backend_flow_summary_impl(
    *,
    driver,
    neo4j_db: str,
    workspace_id: str,
    api_contains: str | None = None,
    crate_contains: str | None = None,
    model_contains: str | None = None,
    service_contains: str | None = None,
    include_tests: bool = False,
    limit: int = 20,
    as_table: bool = False,
) -> str:
    project_id = get_project_id(workspace_id)
    query_limit = max(limit, 50)
    if any([api_contains, model_contains, service_contains]):
        query_limit = max(limit * 10, 300)

    async with driver.session(database=neo4j_db) as session:
        result = await graph_core._execute_read(
            session,
            _BACKEND_FLOW_QUERY,
            p=project_id,
            include_tests=include_tests,
            limit=query_limit,
            op="get_backend_flow_summary",
        )
        result = [
            row
            for row in result
            if row.get("route")
            or row.get("svc")
            or row.get("model")
            or row.get("schema")
            or row.get("external")
        ]
        if not include_tests:
            result = [
                row
                for row in result
                if not any(
                    _is_low_signal_flow_path(path, roles)
                    for path, roles in (
                        (row.get("api"), row.get("api_roles")),
                        (row.get("svc"), row.get("svc_roles")),
                    )
                )
            ]
        if not result:
            result = await graph_core._execute_read(
                session,
                _BACKEND_FLOW_FALLBACK_QUERY,
                p=project_id,
                include_tests=include_tests,
                limit=query_limit,
                op="get_backend_flow_summary_fallback",
            )
            result = [
                row
                for row in result
                if row.get("route")
                or row.get("svc")
                or row.get("model")
                or row.get("schema")
                or row.get("external")
            ]
            if not include_tests:
                result = [
                    row
                    for row in result
                    if not any(
                        _is_low_signal_flow_path(path, roles)
                        for path, roles in (
                            (row.get("api"), row.get("api_roles")),
                            (row.get("svc"), row.get("svc_roles")),
                        )
                    )
                ]
        if not result:
            result = await _build_python_backend_flow_fallback(
                session=session,
                project_id=project_id,
                workspace_id=workspace_id,
                include_tests=include_tests,
            )
        api_routes = await _load_backend_api_routes(session, project_id)
        cargo_crate_rows = await _load_cargo_crate_roots(session, project_id)
    rows = [
        {
            "api": row.get("api"),
            "route": row.get("route"),
            "svc": row.get("svc"),
            "model": row.get("model"),
            "schema": row.get("schema"),
            "external": row.get("external"),
        }
        for row in result
    ]
    rows = _expand_backend_rows_by_route_context(rows, api_routes)
    for row in rows:
        row["api_crate"], row["api_crate_name"] = _match_cargo_crate(row["api"], cargo_crate_rows)
        row["svc_crate"], row["svc_crate_name"] = _match_cargo_crate(row["svc"], cargo_crate_rows)

    if api_contains:
        rows = [r for r in rows if r["api"] and api_contains in r["api"]]
    if service_contains:
        rows = [r for r in rows if r["svc"] and service_contains in r["svc"]]
    if model_contains:
        rows = [r for r in rows if r["model"] and model_contains in r["model"]]
    if crate_contains:
        needle = crate_contains.lower()
        rows = [
            r for r in rows
            if (r["api_crate"] and needle in r["api_crate"].lower())
            or (r["api_crate_name"] and needle in r["api_crate_name"].lower())
            or (r["svc_crate"] and needle in r["svc_crate"].lower())
            or (r["svc_crate_name"] and needle in r["svc_crate_name"].lower())
        ]

    rows = [r for r in rows if r.get("route") or r["svc"] or r["model"] or r["schema"] or r["external"]]
    if not rows:
        return _format_backend_flow_empty_message(cargo_crate_rows)

    focus_lines = _backend_flow_focus_lines(rows)

    if as_table:
        output = [
            "| API Crate | Service Crate | API | Route | Service | Model | Schema | External |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows[:limit]:
            output.append(
                f"| {row['api_crate'] or ''} | {row['svc_crate'] or ''} | {row['api'] or ''} | {row.get('route') or ''} | {row['svc'] or ''} | {row['model'] or ''} | {row['schema'] or ''} | {row['external'] or ''} |"
            )
    else:
        if any(row.get("api_crate") or row.get("svc_crate") for row in rows):
            output = _group_backend_flow_rows(rows, limit)
        else:
            output = [
                _format_backend_flow_row(
                    row["api"],
                    row.get("route"),
                    row["svc"],
                    row["model"],
                    row["schema"],
                    row["external"],
                    api_crate=row["api_crate"],
                    svc_crate=row["svc_crate"],
                )
                for row in rows
            ]

    output = list(dict.fromkeys(output))
    if limit and len(output) > limit:
        output = output[:limit]
    prefix = [
        "Use this to decide which API entrypoints reach real services, models, or external systems first."
    ]
    if focus_lines:
        prefix.extend(["", "Inspect First:", *focus_lines, ""])
    return "\n".join(prefix + output)


async def get_apple_build_summary_impl(**kwargs) -> str:
    return await flow_summary_apple.get_apple_build_summary_impl(**kwargs)

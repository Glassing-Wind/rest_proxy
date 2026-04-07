"""Flow summary helpers for UI/API/service graph views."""

from __future__ import annotations

import fnmatch

from _helpers import get_project_id
from tools.brain.graph import core as graph_core
from tools.brain.graph import flow_summary_apple

_APP_FLOW_QUERY = """
CALL {
    MATCH (ui:File {project_id:$p})-[:ASSET_LINKS]->(js:File {project_id:$p})
    MATCH (js)-[:CALLS_API_ROUTE]->(route:ApiRoute {project_id:$p})
    OPTIONAL MATCH (route)-[:HANDLED_BY]->(api:File {project_id:$p})
    OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
    OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
    OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
    OPTIONAL MATCH (js)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
    WHERE $include_tests OR (
        NOT ui.filepath STARTS WITH 'tests/'
        AND NOT ui.filepath CONTAINS '/tests/'
        AND NOT ui.filepath CONTAINS '__tests__'
        AND NOT ui.filepath CONTAINS '.test.'
        AND NOT js.filepath STARTS WITH 'tests/'
        AND NOT js.filepath CONTAINS '/tests/'
        AND NOT js.filepath CONTAINS '__tests__'
        AND NOT js.filepath CONTAINS '.test.'
    )
    RETURN ui.filepath AS ui,
       js.filepath AS js,
       (coalesce(route.method, 'ANY') + ' ' + route.path) AS route,
       api.filepath AS api,
       svc.filepath AS svc,
       model.name AS model,
       schema.filepath AS schema,
       ext.url AS external
    UNION
    MATCH (ui:File {project_id:$p})-[:ASSET_LINKS]->(js:File {project_id:$p})
    WHERE NOT EXISTS {
        MATCH (js)-[:CALLS_API_ROUTE]->(:ApiRoute {project_id:$p})
    }
    MATCH (js)-[:CALLS_API]->(api:File {project_id:$p})
    OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
    OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
    OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
    OPTIONAL MATCH (js)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
    WHERE $include_tests OR (
        NOT ui.filepath STARTS WITH 'tests/'
        AND NOT ui.filepath CONTAINS '/tests/'
        AND NOT ui.filepath CONTAINS '__tests__'
        AND NOT ui.filepath CONTAINS '.test.'
        AND NOT js.filepath STARTS WITH 'tests/'
        AND NOT js.filepath CONTAINS '/tests/'
        AND NOT js.filepath CONTAINS '__tests__'
        AND NOT js.filepath CONTAINS '.test.'
    )
    RETURN ui.filepath AS ui,
       js.filepath AS js,
       NULL AS route,
       api.filepath AS api,
       svc.filepath AS svc,
       model.name AS model,
       schema.filepath AS schema,
       ext.url AS external
    UNION
    MATCH (ui:File {project_id:$p})-[:CALLS_API_ROUTE]->(route:ApiRoute {project_id:$p})
    OPTIONAL MATCH (route)-[:HANDLED_BY]->(api:File {project_id:$p})
    OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
    OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
    OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
    OPTIONAL MATCH (ui)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
    WHERE $include_tests OR (
        NOT ui.filepath STARTS WITH 'tests/'
        AND NOT ui.filepath CONTAINS '/tests/'
        AND NOT ui.filepath CONTAINS '__tests__'
        AND NOT ui.filepath CONTAINS '.test.'
    )
    RETURN ui.filepath AS ui,
       ui.filepath AS js,
       (coalesce(route.method, 'ANY') + ' ' + route.path) AS route,
       api.filepath AS api,
       svc.filepath AS svc,
       model.name AS model,
       schema.filepath AS schema,
       ext.url AS external
    UNION
    MATCH (ui:File {project_id:$p})
    WHERE NOT EXISTS {
        MATCH (ui)-[:CALLS_API_ROUTE]->(:ApiRoute {project_id:$p})
    }
      AND ($include_tests OR (
        NOT ui.filepath STARTS WITH 'tests/'
        AND NOT ui.filepath CONTAINS '/tests/'
        AND NOT ui.filepath CONTAINS '__tests__'
        AND NOT ui.filepath CONTAINS '.test.'
      ))
    MATCH (ui)-[:CALLS_API]->(api:File {project_id:$p})
    OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
    OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
    OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
    OPTIONAL MATCH (ui)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
    RETURN ui.filepath AS ui,
       ui.filepath AS js,
       NULL AS route,
       api.filepath AS api,
       svc.filepath AS svc,
       model.name AS model,
       schema.filepath AS schema,
       ext.url AS external
}
RETURN ui, js, route, api, svc, model, schema, external
ORDER BY ui, js, route, api, svc, model, schema, external
LIMIT $limit
"""


_BACKEND_FLOW_QUERY = """
MATCH (api:File {project_id:$p})
OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
OPTIONAL MATCH (api)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
WHERE (svc IS NOT NULL OR model IS NOT NULL OR schema IS NOT NULL OR ext IS NOT NULL)
  AND ($include_tests OR (
    NOT api.filepath STARTS WITH 'tests/'
    AND NOT api.filepath CONTAINS '/tests/'
    AND NOT api.filepath CONTAINS '__tests__'
    AND NOT api.filepath CONTAINS '.test.'
    AND (svc IS NULL OR (
      NOT svc.filepath STARTS WITH 'tests/'
      AND NOT svc.filepath CONTAINS '/tests/'
      AND NOT svc.filepath CONTAINS '__tests__'
      AND NOT svc.filepath CONTAINS '.test.'
    ))
  ))
RETURN api.filepath AS api,
   svc.filepath AS svc,
   model.name AS model,
   schema.filepath AS schema,
   ext.url AS external
ORDER BY api, svc, model, schema, external
LIMIT $limit
"""


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
    if "CargoCrate" not in labels:
        return []
    return await graph_core._execute_read(
        session,
        """
        MATCH (c:CargoCrate {project_id:$p})-[:DEFINED_IN_FILE]->(mf:File {project_id:$p})
        RETURN c.name AS crate,
               c.crate_name AS crate_name,
               mf.filepath AS manifest_path
        ORDER BY size(mf.filepath) DESC, c.name
        """,
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


def _format_backend_flow_row(api, svc, model, schema, external, api_crate=None, svc_crate=None) -> str:
    parts = [value for value in [api, svc, model, schema, external] if value]
    flow = " -> ".join(parts)
    crate_bits = []
    if api_crate:
        crate_bits.append(f"api_crate={api_crate}")
    if svc_crate and svc_crate != api_crate:
        crate_bits.append(f"service_crate={svc_crate}")
    if not crate_bits:
        return flow
    return f"[{', '.join(crate_bits)}] {flow}"


async def _resolve_entry_files(session, project_id: str, entry_files, entry_glob):
    if entry_files or not entry_glob:
        return entry_files
    ui_result = await graph_core._execute_read(
        session,
        """
        MATCH (f:File {project_id:$p})
        WHERE f.filepath ENDS WITH '.html' OR f.filepath ENDS WITH '.astro'
        RETURN f.filepath AS ui
        """,
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


async def _coverage_lines(session, project_id: str) -> list[str]:
    coverage_result = await graph_core._execute_read(
        session,
        """
        MATCH (f:File {project_id:$p})
        WITH collect(f) AS files
        RETURN
          size([f IN files WHERE f.filepath ENDS WITH '.html' OR f.filepath ENDS WITH '.astro']) AS ui_files,
          size([f IN files WHERE f.filepath ENDS WITH '.js' OR f.filepath ENDS WITH '.ts' OR f.filepath ENDS WITH '.tsx']) AS js_files
        """,
        p=project_id,
        op="get_app_flow_summary_coverage_files",
    )
    edge_result = await graph_core._execute_read(
        session,
        "MATCH (:File {project_id:$p})-[r:ASSET_LINKS]->() RETURN count(r) AS asset_links",
        p=project_id,
        op="get_app_flow_summary_coverage_assets",
    )
    api_result = await graph_core._execute_read(
        session,
        "MATCH (:File {project_id:$p})-[r:CALLS_API]->() RETURN count(r) AS api_links",
        p=project_id,
        op="get_app_flow_summary_coverage_api",
    )
    svc_result = await graph_core._execute_read(
        session,
        "MATCH (:File {project_id:$p})-[r:CALLS_SERVICE]->() RETURN count(r) AS service_links",
        p=project_id,
        op="get_app_flow_summary_coverage_service",
    )
    db_result = await graph_core._execute_read(
        session,
        "MATCH (:File {project_id:$p})-[r:CALLS_DB]->() RETURN count(r) AS db_links",
        p=project_id,
        op="get_app_flow_summary_coverage_db",
    )
    route_result = await graph_core._execute_read(
        session,
        "MATCH (:File {project_id:$p})-[r:CALLS_API_ROUTE]->() RETURN count(r) AS api_route_links",
        p=project_id,
        op="get_app_flow_summary_coverage_api_routes",
    )
    ui_files = coverage_result[0].get("ui_files") if coverage_result else 0
    js_files = coverage_result[0].get("js_files") if coverage_result else 0
    asset_links = edge_result[0].get("asset_links") if edge_result else 0
    api_links = api_result[0].get("api_links") if api_result else 0
    service_links = svc_result[0].get("service_links") if svc_result else 0
    db_links = db_result[0].get("db_links") if db_result else 0
    api_route_links = route_result[0].get("api_route_links") if route_result else 0
    return [
        "Coverage: "
        f"ui_files={ui_files} js_files={js_files} "
        f"asset_links={asset_links} api_links={api_links} "
        f"api_route_links={api_route_links} service_links={service_links} db_links={db_links}"
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

        result = await graph_core._execute_read(
            session,
            _APP_FLOW_QUERY,
            p=project_id,
            include_tests=include_tests,
            limit=query_limit,
            op="get_app_flow_summary",
        )
        raw_rows = [
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
            for row in result
        ]
        raw_rows = _filtered_app_rows(
            raw_rows,
            entry_files=entry_files,
            ui_contains=ui_contains,
            model_contains=model_contains,
            service_contains=service_contains,
        )
        raw_rows = _prefer_concrete_app_rows(raw_rows)

        if expand_api_calls:
            ui_candidates = sorted({row[0] for row in raw_rows if row[0]})
            for ui_path in ui_candidates:
                api_calls_result = await graph_core._execute_read(
                    session,
                    """
                    MATCH (ui:File {project_id:$p})-[:ASSET_LINKS]->(js:File {project_id:$p})
                    MATCH (js)-[:CALLS_API_ROUTE]->(route:ApiRoute {project_id:$p})
                    WHERE ui.filepath = $ui_path
                    RETURN collect(distinct (coalesce(route.method, 'ANY') + ' ' + route.path)) AS routes
                    """,
                    p=project_id,
                    ui_path=ui_path,
                    op="get_app_flow_summary_api_routes",
                )
                routes = api_calls_result[0].get("routes") if api_calls_result else []
                ui_routes[ui_path] = sorted(route for route in routes if route)

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
    if not rows:
        return "No UI → API → Service → DB paths found."
    output = []
    if coverage_lines:
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
        cargo_crate_rows = await _load_cargo_crate_roots(session, project_id)
    rows = [
        {
            "api": row.get("api"),
            "svc": row.get("svc"),
            "model": row.get("model"),
            "schema": row.get("schema"),
            "external": row.get("external"),
        }
        for row in result
    ]
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

    rows = [r for r in rows if r["svc"] or r["model"] or r["schema"] or r["external"]]
    if not rows:
        return "No API → Service → DB paths found."

    if as_table:
        output = [
            "| API Crate | Service Crate | API | Service | Model | Schema | External |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows[:limit]:
            output.append(
                f"| {row['api_crate'] or ''} | {row['svc_crate'] or ''} | {row['api'] or ''} | {row['svc'] or ''} | {row['model'] or ''} | {row['schema'] or ''} | {row['external'] or ''} |"
            )
    else:
        output = [
            _format_backend_flow_row(
                row["api"],
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
    return "\n".join(output)


async def get_apple_build_summary_impl(**kwargs) -> str:
    return await flow_summary_apple.get_apple_build_summary_impl(**kwargs)

"""Flow summary helpers for UI/API/service graph views."""

from __future__ import annotations

import fnmatch
from pathlib import PurePath

from _helpers import get_project_id
from tools.brain.graph import core as graph_core


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


_APPLE_BUILD_QUERY = """
MATCH (res:Resource {project_id:$p})-[:BUNDLED_IN_TARGET]->(target:XcodeTarget {project_id:$p})
OPTIONAL MATCH (src:File {project_id:$p})-[rel:USES_ASSET|USES_COLOR_ASSET|USES_XIB|USES_STORYBOARD]->(res)
OPTIONAL MATCH (res)-[:BACKED_BY_FILE]->(backing:File {project_id:$p})
OPTIONAL MATCH (scheme:XcodeScheme {project_id:$p})-[:BUILDS_TARGET]->(target)
OPTIONAL MATCH (scheme)-[:DEFINED_IN_FILE]->(scheme_file:File {project_id:$p})
OPTIONAL MATCH (workspace:XcodeWorkspace {project_id:$p})-[:REFERENCES_PROJECT]->(project_file:File {project_id:$p})
WHERE project_file.filepath = target.project_file OR project_file IS NULL
RETURN src.filepath AS src,
   type(rel) AS rel,
   res.name AS resource,
   res.kind AS kind,
   backing.filepath AS backing,
   target.name AS target,
   target.project_file AS project_file,
   scheme.name AS scheme,
   scheme_file.filepath AS scheme_file,
   workspace.filepath AS workspace
ORDER BY src, resource, backing, target, scheme, workspace
LIMIT $limit
"""


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
            flow = " -> ".join(
                [value for value in [ui, js, route, api, svc, model, schema, external] if value]
            )
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
            " -> ".join([value for value in [ui, js, route, api, svc, model, schema, external] if value])
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
    rows = [
        (
            row.get("api"),
            row.get("svc"),
            row.get("model"),
            row.get("schema"),
            row.get("external"),
        )
        for row in result
    ]

    if api_contains:
        rows = [r for r in rows if r[0] and api_contains in r[0]]
    if service_contains:
        rows = [r for r in rows if r[1] and service_contains in r[1]]
    if model_contains:
        rows = [r for r in rows if r[2] and model_contains in r[2]]

    rows = [r for r in rows if r[1] or r[2] or r[3] or r[4]]
    if not rows:
        return "No API → Service → DB paths found."

    if as_table:
        output = [
            "| API | Service | Model | Schema | External |",
            "| --- | --- | --- | --- | --- |",
        ]
        for api, svc, model, schema, external in rows[:limit]:
            output.append(
                f"| {api or ''} | {svc or ''} | {model or ''} | {schema or ''} | {external or ''} |"
            )
    else:
        output = [
            " -> ".join([value for value in [api, svc, model, schema, external] if value])
            for api, svc, model, schema, external in rows
        ]

    output = list(dict.fromkeys(output))
    if limit and len(output) > limit:
        output = output[:limit]
    return "\n".join(output)


async def get_apple_build_summary_impl(
    *,
    driver,
    neo4j_db: str,
    workspace_id: str,
    source_contains: str | None = None,
    resource_contains: str | None = None,
    target_contains: str | None = None,
    scheme_contains: str | None = None,
    workspace_contains: str | None = None,
    limit: int = 20,
    as_table: bool = False,
    group_by: str = "target",
) -> str:
    project_id = get_project_id(workspace_id)
    query_limit = max(limit, 50)
    if any([source_contains, resource_contains, target_contains, scheme_contains, workspace_contains]):
        query_limit = max(limit * 10, 300)

    async with driver.session(database=neo4j_db) as session:
        result = await graph_core._execute_read(
            session,
            _APPLE_BUILD_QUERY,
            p=project_id,
            limit=query_limit,
            op="get_apple_build_summary",
        )

    rows = [
        (
            row.get("src"),
            row.get("rel"),
            row.get("resource"),
            row.get("kind"),
            row.get("backing"),
            row.get("target"),
            row.get("project_file"),
            row.get("scheme"),
            row.get("scheme_file"),
            row.get("workspace"),
        )
        for row in result
    ]

    if source_contains:
        rows = [r for r in rows if r[0] and source_contains in r[0]]
    if resource_contains:
        rows = [r for r in rows if r[2] and resource_contains in r[2]]
    if target_contains:
        rows = [r for r in rows if r[5] and target_contains in r[5]]
    if scheme_contains:
        rows = [r for r in rows if r[7] and scheme_contains in r[7]]
    if workspace_contains:
        rows = [r for r in rows if r[9] and workspace_contains in r[9]]

    rows = [r for r in rows if r[2] and r[5]]
    if not rows:
        return "No Apple build graph paths found."

    group_norm = (group_by or "target").strip().lower()
    if group_norm not in {"target", "scheme", "source", "none"}:
        group_norm = "target"

    def _rank_key(row):
        src, rel, resource, kind, backing, target, project_file, scheme, scheme_file, workspace = row
        score = 0
        if target_contains and target and target_contains in target:
            score += 20
        if scheme_contains and scheme and scheme_contains in scheme:
            score += 16
        if workspace_contains and workspace and workspace_contains in workspace:
            score += 12
        if source_contains and src and source_contains in src:
            score += 10
        if resource_contains and resource and resource_contains in resource:
            score += 8
        if src and target and PurePath(src).parts[:2] == PurePath(project_file or "").parts[:2]:
            score += 6
        if backing and target and PurePath(backing).parts[:2] == PurePath(project_file or "").parts[:2]:
            score += 5
        if scheme:
            score += 3
        if workspace:
            score += 2
        if backing:
            score += 1
        return (-score, target or "", scheme or "", src or "", resource or "", backing or "")

    rows = sorted(rows, key=_rank_key)

    if as_table:
        output = [
            "| Source | Link | Resource | Kind | Backing File | Target | Project | Scheme | Scheme File | Workspace |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for src, rel, resource, kind, backing, target, project_file, scheme, scheme_file, workspace in rows[:limit]:
            output.append(
                f"| {src or ''} | {rel or ''} | {resource or ''} | {kind or ''} | {backing or ''} | {target or ''} | {project_file or ''} | {scheme or ''} | {scheme_file or ''} | {workspace or ''} |"
            )
        return "\n".join(output)

    def _format_line(row):
        src, rel, resource, kind, backing, target, project_file, scheme, scheme_file, workspace = row
        parts = []
        if src:
            parts.append(src)
        if rel and resource:
            parts.append(f"{rel}:{resource}")
        elif resource:
            parts.append(resource)
        if backing:
            parts.append(backing)
        if target:
            parts.append(f"target={target}")
        if scheme:
            parts.append(f"scheme={scheme}")
        if workspace:
            parts.append(f"workspace={workspace}")
        return " -> ".join(parts)

    if group_norm == "none":
        output = []
        seen = set()
        for row in rows:
            line = _format_line(row)
            if not line or line in seen:
                continue
            seen.add(line)
            output.append(line)
            if limit and len(output) >= limit:
                break
        return "\n".join(output)

    key_index = {"source": 0, "target": 5, "scheme": 7}[group_norm]
    grouped: dict[str, list[tuple[str | None, ...]]] = {}
    for row in rows:
        group_value = row[key_index]
        if not group_value:
            continue
        grouped.setdefault(group_value, []).append(row)

    output = []
    emitted = 0
    for group_value in grouped:
        output.append(f"{group_norm.title()}: {group_value}")
        seen = set()
        for row in grouped[group_value]:
            line = _format_line(row)
            if not line or line in seen:
                continue
            seen.add(line)
            output.append(line)
            emitted += 1
            if limit and emitted >= limit:
                return "\n".join(output)
    return "\n".join(output)

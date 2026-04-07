"""tools/graph/tools.py — MCP tool registration for graph features."""

from __future__ import annotations

import os
from mcp.server.fastmcp import FastMCP

from _helpers import get_memory_modules, get_project_id, get_workspace_path
from tools.brain.graph import core as graph_core
from tools.brain.graph import runtime as graph_runtime
from .core import _SYMBOL_FILTER_CYPHER


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_directory_snapshot(
        workspace_id: str, directory_path: str, limit: int = 5
    ) -> str:
        """
        Provides an architectural onboarding guide for a specific directory.
        Summarizes importance, exports, and inbound/outbound coupling.

        Args:
            workspace_id: Logical workspace name or absolute project path.
            directory_path: Relative path to director (e.g. 'src/api').
            limit: Max results per section (default 5).
        """
        try:
            import graph_bootstrap

            project_id = get_project_id(workspace_id)
            # Normalize directory path for prefix matching (empty string for root)
            dir_prefix = directory_path.strip("./")
            if dir_prefix:
                dir_prefix += "/"

            driver = await graph_bootstrap.require_driver()
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                # 1. Folder Metrics & Top Files
                r_files = await graph_core._execute_read(
                    session,
                    """
                    MATCH (f:File {{project_id: $p}})
                    WHERE f.filepath STARTS WITH $dir
                    OPTIONAL MATCH (f)-[:CONTAINS]->(s)
                    WHERE {filters}
                    WITH f.filepath AS fp, count(s) AS sym_count, collect(s.name)[..3] AS samples
                    ORDER BY sym_count DESC
                    RETURN fp, sym_count, samples
                    LIMIT $limit
                """.format(filters=_SYMBOL_FILTER_CYPHER),
                    p=project_id,
                    dir=dir_prefix,
                    limit=limit,
                    op="get_directory_snapshot_files",
                )

                # 2. Inbound Context (Consumers)
                r_inbound = await graph_core._execute_read(
                    session,
                    """
                    MATCH (ext:File {project_id: $p})-[:IMPORTS]->(inner:File {project_id: $p})
                    WHERE inner.filepath STARTS WITH $dir
                      AND NOT ext.filepath STARTS WITH $dir
                    RETURN ext.filepath AS caller, count(DISTINCT inner) AS n_imports
                    ORDER BY n_imports DESC
                    LIMIT $limit
                """,
                    p=project_id,
                    dir=dir_prefix,
                    limit=limit,
                    op="get_directory_snapshot_inbound",
                )

                # 3. Outbound Context (Dependencies)
                r_outbound = await graph_core._execute_read(
                    session,
                    """
                    MATCH (inner:File {project_id: $p})-[:IMPORTS]->(ext:File {project_id: $p})
                    WHERE inner.filepath STARTS WITH $dir
                      AND NOT ext.filepath STARTS WITH $dir
                    RETURN ext.filepath AS dependency, count(DISTINCT inner) AS n_usages
                    ORDER BY n_usages DESC
                    LIMIT $limit
                """,
                    p=project_id,
                    dir=dir_prefix,
                    limit=limit,
                    op="get_directory_snapshot_outbound",
                )

                # 4. UI & Assets (New section)
                r_assets = await graph_core._execute_read(
                    session,
                    """
                    MATCH (f:File {project_id: $p})
                    WHERE f.filepath STARTS WITH $dir
                    MATCH (f)-[r:ASSET_LINKS|CALLS_API]->(target:File {project_id: $p})
                    RETURN f.filepath AS source, type(r) AS rel, target.filepath AS target
                    ORDER BY source, rel
                    LIMIT $limit
                """,
                    p=project_id,
                    dir=dir_prefix,
                    limit=limit * 3,
                    op="get_directory_snapshot_assets",
                )

            # Format Report
            lines = [f"# Directory Snapshot: `{directory_path or '.'}/`"]
            if not r_files:
                return f"No indexed files found in `{directory_path}`."

            lines.append(f"\n### 🏆 Top Files (by symbol density)")
            for rec in r_files:
                samples = ", ".join(rec["samples"])
                lines.append(
                    f"- **{rec['fp']}** ({rec['sym_count']} symbols: {samples})"
                )

            if r_assets:
                lines.append(f"\n### 🎨 UI & Assets (Linkages & Wiring)")
                for rec in r_assets:
                    source = rec["source"]
                    target = rec["target"]
                    rel = rec["rel"]
                    if rel == "ASSET_LINKS":
                        lines.append(f"- `{source}` -> 📦 `{target}` (Asset/Style)")
                    elif rel == "CALLS_API":
                        lines.append(f"- `{source}` -> 🔌 `{target}` (API Endpoint)")

            if r_inbound:
                lines.append(f"\n### 📥 Consumers (External files importing from here)")
                for rec in r_inbound:
                    lines.append(f"- `{rec['caller']}` (imports {rec['n_imports']} files)")
            else:
                lines.append("\n### 📥 Consumers: None found.")

            if r_outbound:
                lines.append(f"\n### 📤 Dependencies (External files imported by here)")
                for rec in r_outbound:
                    lines.append(f"- `{rec['dependency']}` (used by {rec['n_usages']} files)")
            else:
                lines.append("\n### 📤 Dependencies: None found.")

            return "\n".join(lines)
        except Exception as e:
            return f"Error building directory snapshot: {str(e)}"


    @mcp.tool()
    async def get_project_overview(workspace_id: str) -> str:
        """
        Single-call project onboarding summary. Combines health, architecture
        clusters, and most important files into one synthesized view.
        Use this as the FIRST tool when starting work on an unfamiliar codebase.

        Args:
            workspace_id: Logical workspace name or absolute project path.
        """
        try:
            project_id = get_project_id(workspace_id)
            project_path = get_workspace_path(workspace_id)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r = await graph_core._execute_read(
                    session,
                    "MATCH (f:File {project_id:$p}) RETURN count(f) AS files",
                    p=project_id,
                    op="get_project_overview_file_count",
                )
                rec = r[0] if r else None
                n_files = rec["files"] if rec else 0

                r2 = await graph_core._execute_read(
                    session,
                    f"MATCH (s {{project_id:$p}}) WHERE {_SYMBOL_FILTER_CYPHER} RETURN count(s) AS syms",
                    p=project_id,
                    op="get_project_overview_symbol_count",
                )
                rec2 = r2[0] if r2 else None
                n_syms = rec2["syms"] if rec2 else 0

                r3 = await graph_core._execute_read(
                    session,
                    """
                    MATCH (f:File {{project_id: $p}})
                    WITH f, CASE WHEN f.filepath CONTAINS '/'
                         THEN split(f.filepath, '/')[0] ELSE '(root)' END AS top_dir
                    OPTIONAL MATCH (f)-[:CONTAINS]->(s)
                    WHERE {filters}
                    WITH top_dir, count(DISTINCT s) AS syms, count(DISTINCT f) AS files
                    ORDER BY syms DESC LIMIT 6
                    RETURN top_dir, files, syms
                """.format(filters=_SYMBOL_FILTER_CYPHER),
                    p=project_id,
                    op="get_project_overview_dirs",
                )
                dirs = []
                for rec in r3:
                    dirs.append(
                        f"  📂 {rec['top_dir']}/  ({rec['files']} files, {rec['syms']} symbols)"
                    )

                r4 = await graph_core._execute_read(
                    session,
                    """
                    MATCH (f:File {{project_id: $p}})-[:CONTAINS]->(s)
                    WHERE ({filters})
                      AND NOT f.filepath CONTAINS 'test'
                      AND NOT f.filepath CONTAINS 'spec'
                    WITH f.filepath AS fp, count(s) AS n, collect(DISTINCT s.name)[..3] AS ex
                    ORDER BY n DESC LIMIT 5
                    RETURN fp, n, ex
                """.format(filters=_SYMBOL_FILTER_CYPHER),
                    p=project_id,
                    op="get_project_overview_key_files",
                )
                key_files = []
                for rec in r4:
                    ex = ", ".join(e for e in rec["ex"] if e)
                    key_files.append(f"  - {rec['fp']}  ({rec['n']} symbols: {ex})")

                memory_store, _, _, _, _ = get_memory_modules()
                await memory_store.open_pool()
                async with memory_store._pg_pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "SELECT count(*) FROM codebase_embeddings WHERE project_id=%s",
                            (project_id,),
                        )
                        row = await cur.fetchone()
                    n_chunks = row[0] if row else 0

            lines = (
                [
                    f"# Project Overview: {project_path.split('/')[-1]}",
                    "",
                    "## Health",
                    f"  Files indexed:   {n_files}",
                    f"  Symbols:         {n_syms}",
                    "  Symbol kinds:    Function, Class, Struct, Trait, Enum",
                    f"  Semantic chunks: {n_chunks}",
                    "",
                    "## Architecture (top-level directories by symbol density)",
                ]
                + dirs
                + [
                    "",
                    "## Key Files (most symbol-dense, non-test)",
                ]
                + key_files
                + [
                    "",
                    "## Next steps",
                    f"  - search_codebase('{project_path}', '<your question>') — semantic search",
                    f"  - describe_file('{project_path}', '<file_path>') — deep-dive a specific file",
                    f"  - get_code_importance('{project_path}') — full ranked file list",
                ]
            )
            return "\n".join(lines)
        except Exception as e:
            return f"Error generating project overview: {str(e)}"

    @mcp.tool()
    async def get_graph_build_metrics(limit: int = 50) -> str:
        """
        Summarize recent graph-build batch timings and last build duration.

        Args:
            limit: Number of recent batches to summarize (default 50).
        """
        try:
            limit = max(1, min(int(limit), 200))
            ib_count, ib_avg, ib_max = graph_core._summarize_batches(
                "import_graph_batch", limit
            )
            si_count, si_avg, si_max = graph_core._summarize_batches(
                "symbol_import_batch", limit
            )
            se_count, se_avg, se_max = graph_core._summarize_batches(
                "symbol_export_batch", limit
            )
            last_build = graph_core.get_last_graph_build_metric()
            recent = graph_runtime.get_recent_metrics(10)

            lines = ["# Graph build metrics"]
            if last_build:
                lines.append(
                    "Last build: "
                    f"project={last_build.get('project_path')} "
                    f"elapsed_ms={last_build.get('elapsed_ms')}"
                )
            else:
                lines.append("Last build: none")

            lines.append(
                f"import_graph_batch: count={ib_count} avg_ms={ib_avg} max_ms={ib_max}"
            )
            lines.append(
                f"symbol_import_batch: count={si_count} avg_ms={si_avg} max_ms={si_max}"
            )
            lines.append(
                f"symbol_export_batch: count={se_count} avg_ms={se_avg} max_ms={se_max}"
            )

            lines.append("Recent events:")
            for entry in recent:
                evt = entry.get("event")
                elapsed = entry.get("elapsed_ms")
                proj = entry.get("project_path") or entry.get("project_id")
                if elapsed is not None:
                    lines.append(f"- {evt} {proj} elapsed_ms={elapsed}")
                else:
                    lines.append(f"- {evt} {proj}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Error reading metrics: {str(exc)}"

    @mcp.tool()
    async def get_language_pack_status() -> str:
        """
        Show available vs manifest languages for tree-sitter-language-pack.
        """
        try:
            import tree_sitter_language_pack as ts_pack

            auto_dl = os.getenv("LM_PROXY_TS_PACK_AUTO_DOWNLOAD", "1")
            cache_dir = os.getenv("LM_PROXY_TS_PACK_CACHE_DIR")
            if cache_dir:
                try:
                    ts_pack.init({"cache_dir": cache_dir})
                except Exception:
                    pass
            available = sorted(ts_pack.available_languages())
            try:
                manifest = sorted(ts_pack.manifest_languages())
            except Exception:
                manifest = []

            missing = [lang for lang in manifest if lang not in available]
            lines = ["# Language pack status"]
            lines.append(f"Auto-download: {auto_dl}")
            if cache_dir:
                lines.append(f"Cache dir: {cache_dir}")
            lines.append(f"Available languages: {len(available)}")
            lines.append(f"Manifest languages: {len(manifest)}")
            if missing:
                lines.append(f"Missing languages: {len(missing)}")
                lines.append("Missing sample: " + ", ".join(missing[:20]))
            else:
                lines.append("Missing languages: none")
            return "\n".join(lines)
        except Exception as e:
            return f"Error reading language pack status: {str(e)}"

    @mcp.tool()
    async def build_import_graph(workspace_id: str) -> str:
        """
        Build file-level IMPORTS edges in Neo4j by resolving Import nodes.

        Reads every Import node already in the graph, attempts to match the
        import source to a File node in the same project, and writes
        (source_file)-[:IMPORTS]->(target_file) relationships.

        Supports: Python (from X import Y / import X),
                  JavaScript/TypeScript (import from './X'),
                  Rust (use crate::X / mod X).

        Run this once after index_workspace to enable real PageRank and
        Louvain community detection via get_code_importance and
        get_code_communities.

        Args:
            workspace_id: Logical workspace name or absolute project path.
        """
        return await graph_core._build_import_graph_impl(workspace_id)


    async def _rebuild_subgraph(fn, label: str, workspace_id: str) -> str:
        """Helper to resolve path, time execution, and record metrics for graph builds."""
        import time
        from tools.brain.graph import runtime as graph_runtime

        project_path = get_workspace_path(workspace_id)
        start_time = time.time()
        
        graph_runtime.record_metric(f"rebuild_{label}_start", project_path=project_path)
        try:
            result = await fn(project_path)
            elapsed = (time.time() - start_time) * 1000
            graph_runtime.record_metric(f"rebuild_{label}_done", project_path=project_path, elapsed_ms=elapsed)
            return result
        except Exception as e:
            return f"Error rebuilding {label} graph: {str(e)}"

    @mcp.tool()
    async def rebuild_symbol_graph(workspace_id: str) -> str:
        """
        Rebuild symbol-level IMPORTS/EXPORTS graph for a project.
        """
        return await _rebuild_subgraph(
            graph_core._build_symbol_import_export_graph_impl, "symbols", workspace_id
        )

    @mcp.tool()
    async def rebuild_asset_graph(workspace_id: str) -> str:
        """
        Rebuild asset linkage edges (UI -> JS, JS -> API, API -> Service, Service -> DB).
        Used for App Flow visualization.
        """
        return await _rebuild_subgraph(
            graph_core._build_asset_graph_impl, "assets", workspace_id
        )

    @mcp.tool()
    async def get_app_flow_summary(
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
        """
        Summarize UI → API → Service → DB paths for a project.
        """
        try:
            import graph_bootstrap

            project_id = get_project_id(workspace_id)
            query_limit = limit
            if model_contains:
                query_limit = max(limit * 10, 200)
            driver = await graph_bootstrap.require_driver()
            rows: list[str] = []
            coverage_lines: list[str] = []
            ui_routes: dict[str, list[str]] = {}
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                if entry_glob and not entry_files:
                    import fnmatch
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
                    entry_files = [
                        path for path in ui_paths if fnmatch.fnmatch(path, entry_glob)
                    ]

                if include_coverage:
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
                        """
                        MATCH (:File {project_id:$p})-[r:ASSET_LINKS]->() RETURN count(r) AS asset_links
                        """,
                        p=project_id,
                        op="get_app_flow_summary_coverage_assets",
                    )
                    api_result = await graph_core._execute_read(
                        session,
                        """
                        MATCH (:File {project_id:$p})-[r:CALLS_API]->() RETURN count(r) AS api_links
                        """,
                        p=project_id,
                        op="get_app_flow_summary_coverage_api",
                    )
                    svc_result = await graph_core._execute_read(
                        session,
                        """
                        MATCH (:File {project_id:$p})-[r:CALLS_SERVICE]->() RETURN count(r) AS service_links
                        """,
                        p=project_id,
                        op="get_app_flow_summary_coverage_service",
                    )
                    db_result = await graph_core._execute_read(
                        session,
                        """
                        MATCH (:File {project_id:$p})-[r:CALLS_DB]->() RETURN count(r) AS db_links
                        """,
                        p=project_id,
                        op="get_app_flow_summary_coverage_db",
                    )
                    ui_files = coverage_result[0].get("ui_files") if coverage_result else 0
                    js_files = coverage_result[0].get("js_files") if coverage_result else 0
                    asset_links = edge_result[0].get("asset_links") if edge_result else 0
                    api_links = api_result[0].get("api_links") if api_result else 0
                    service_links = svc_result[0].get("service_links") if svc_result else 0
                    db_links = db_result[0].get("db_links") if db_result else 0
                    route_result = await graph_core._execute_read(
                        session,
                        """
                        MATCH (:File {project_id:$p})-[r:CALLS_API_ROUTE]->() RETURN count(r) AS api_route_links
                        """,
                        p=project_id,
                        op="get_app_flow_summary_coverage_api_routes",
                    )
                    api_route_links = route_result[0].get("api_route_links") if route_result else 0
                    coverage_lines.append(
                        "Coverage: "
                        f"ui_files={ui_files} js_files={js_files} "
                        f"asset_links={asset_links} api_links={api_links} "
                        f"api_route_links={api_route_links} service_links={service_links} db_links={db_links}"
                    )

                result = await graph_core._execute_read(
                    session,
                    """
                    CALL {
                        MATCH (ui:File {project_id:$p})-[:ASSET_LINKS]->(js:File {project_id:$p})
                        MATCH (js)-[:CALLS_API]->(api:File {project_id:$p})
                        OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
                        OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
                        OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
                        OPTIONAL MATCH (js)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
                        WHERE ($ui_filter IS NULL OR ui.filepath CONTAINS $ui_filter)
                          AND ($ui_list IS NULL OR ui.filepath IN $ui_list)
                          AND ($include_tests OR (
                            NOT ui.filepath STARTS WITH 'tests/'
                            AND NOT ui.filepath CONTAINS '/tests/'
                            AND NOT ui.filepath CONTAINS '__tests__'
                            AND NOT ui.filepath CONTAINS '.test.'
                            AND NOT js.filepath STARTS WITH 'tests/'
                            AND NOT js.filepath CONTAINS '/tests/'
                            AND NOT js.filepath CONTAINS '__tests__'
                            AND NOT js.filepath CONTAINS '.test.'
                          ))
                        RETURN ui.filepath AS ui,
                           js.filepath AS js,
                           api.filepath AS api,
                           svc.filepath AS svc,
                           model.name AS model,
                           schema.filepath AS schema,
                           ext.url AS external
                        UNION
                        MATCH (ui:File {project_id:$p})-[:CALLS_API]->(api:File {project_id:$p})
                        OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
                        OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
                        OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
                        OPTIONAL MATCH (ui)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
                        WHERE ($ui_filter IS NULL OR ui.filepath CONTAINS $ui_filter)
                          AND ($ui_list IS NULL OR ui.filepath IN $ui_list)
                          AND ($include_tests OR (
                            NOT ui.filepath STARTS WITH 'tests/'
                            AND NOT ui.filepath CONTAINS '/tests/'
                            AND NOT ui.filepath CONTAINS '__tests__'
                            AND NOT ui.filepath CONTAINS '.test.'
                          ))
                        RETURN ui.filepath AS ui,
                           ui.filepath AS js,
                           api.filepath AS api,
                           svc.filepath AS svc,
                           model.name AS model,
                           schema.filepath AS schema,
                           ext.url AS external
                    }
                    RETURN ui, js, api, svc, model, schema, external
                    LIMIT $limit
                    """,
                    p=project_id,
                    ui_filter=ui_contains,
                    ui_list=entry_files,
                    include_tests=include_tests,
                    limit=query_limit,
                    op="get_app_flow_summary",
                )
                raw_rows = []
                for row in result:
                    raw_rows.append(
                        (
                            row.get("ui"),
                            row.get("js"),
                            row.get("api"),
                            row.get("svc"),
                            row.get("model"),
                            row.get("schema"),
                            row.get("external"),
                        )
                    )

                if expand_api_calls:
                    ui_candidates = sorted({row[0] for row in raw_rows if row[0]})
                    if entry_files:
                        ui_candidates = [ui for ui in entry_files if ui in ui_candidates]
                    if ui_contains:
                        ui_candidates = [ui for ui in ui_candidates if ui_contains in ui]
                    for ui_path in ui_candidates:
                        api_calls_result = await graph_core._execute_read(
                            session,
                            """
                            MATCH (ui:File {project_id:$p})-[:ASSET_LINKS]->(js:File {project_id:$p})
                            MATCH (js)-[:CALLS_API_ROUTE]->(route:ApiRoute {project_id:$p})
                            WHERE ui.filepath = $ui_path
                            RETURN collect(distinct route.path) AS routes
                            """,
                            p=project_id,
                            ui_path=ui_path,
                            op="get_app_flow_summary_api_routes",
                        )
                        routes = api_calls_result[0].get("routes") if api_calls_result else []
                        ui_routes[ui_path] = sorted(routes)

            if entry_files:
                raw_rows = [r for r in raw_rows if r[0] and r[0] in entry_files]
            if ui_contains:
                raw_rows = [r for r in raw_rows if r[0] and ui_contains in r[0]]
            if model_contains:
                raw_rows = [r for r in raw_rows if r[4] and model_contains in r[4]]
            if service_contains:
                raw_rows = [r for r in raw_rows if r[3] and service_contains in r[3]]

            if as_table:
                rows = [
                    "| UI | JS | API | Service | Model | Schema | External |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                ]
                for ui, js, api, svc, model, schema, external in raw_rows:
                    rows.append(
                        f"| {ui or ''} | {js or ''} | {api or ''} | {svc or ''} | {model or ''} | {schema or ''} | {external or ''} |"
                    )
            else:
                if group_by_ui:
                    grouped: dict[str, list[str]] = {}
                    for ui, js, api, svc, model, schema, external in raw_rows:
                        if not ui:
                            continue
                        flow = " -> ".join(
                            [v for v in [ui, js, api, svc, model, schema, external] if v]
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
                        deduped = list(dict.fromkeys(flows))
                        rows.extend(deduped[:max_per_ui])
                else:
                    rows = [
                        " -> ".join(
                            [v for v in [ui, js, api, svc, model, schema, external] if v]
                        )
                        for ui, js, api, svc, model, schema, external in raw_rows
                    ]
            if limit and len(rows) > limit:
                rows = rows[:limit]
            if not rows:
                return "No UI → API → Service → DB paths found."
            rows = list(dict.fromkeys(rows))
            output = []
            if coverage_lines:
                output.extend(coverage_lines)
            output.extend(rows)
            return "\n".join(output)
        except Exception as exc:
            return f"Error building flow summary: {str(exc)}"

    @mcp.tool()
    async def get_backend_flow_summary(
        workspace_id: str,
        api_contains: str | None = None,
        model_contains: str | None = None,
        service_contains: str | None = None,
        include_tests: bool = False,
        limit: int = 20,
        as_table: bool = False,
    ) -> str:
        """
        Summarize API → Service → DB paths for a project.
        """
        try:
            import graph_bootstrap

            project_id = get_project_id(workspace_id)
            query_limit = limit
            if model_contains:
                query_limit = max(limit * 10, 200)
            driver = await graph_bootstrap.require_driver()
            rows = []
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                result = await graph_core._execute_read(
                    session,
                    """
                    MATCH (api:File {project_id:$p})
                    OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
                    OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
                    OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
                    OPTIONAL MATCH (api)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
                    WHERE ($api_filter IS NULL OR api.filepath CONTAINS $api_filter)
                      AND ($service_filter IS NULL OR svc.filepath CONTAINS $service_filter)
                      AND ($model_filter IS NULL OR model.name CONTAINS $model_filter)
                      AND (svc IS NOT NULL OR model IS NOT NULL OR schema IS NOT NULL OR ext IS NOT NULL)
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
                    LIMIT $limit
                    """,
                    p=project_id,
                    api_filter=api_contains,
                    service_filter=service_contains,
                    model_filter=model_contains,
                    include_tests=include_tests,
                    limit=query_limit,
                    op="get_backend_flow_summary",
                )
                for row in result:
                    rows.append(
                        (
                            row.get("api"),
                            row.get("svc"),
                            row.get("model"),
                            row.get("schema"),
                            row.get("external"),
                        )
                    )

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
                    " -> ".join([v for v in [api, svc, model, schema, external] if v])
                    for api, svc, model, schema, external in rows
                ]

            output = list(dict.fromkeys(output))
            if limit and len(output) > limit:
                output = output[:limit]
            return "\n".join(output)
        except Exception as exc:
            return f"Error building backend flow summary: {str(exc)}"

    @mcp.tool()
    async def get_flow_summary(
        workspace_id: str,
        mode: str = "auto",
        ui_contains: str | None = None,
        api_contains: str | None = None,
        model_contains: str | None = None,
        service_contains: str | None = None,
        include_tests: bool = False,
        limit: int = 20,
        as_table: bool = False,
    ) -> str:
        """
        Summarize UI → API → Service → DB paths or API → Service → DB paths.

        Args:
            workspace_id: Logical workspace name or absolute project path.
            mode: 'auto', 'ui', 'backend', or 'cli'.
            ui_contains: Filter UI files (ui mode only).
            api_contains: Filter API files (backend mode only).
            model_contains: Filter model names.
            service_contains: Filter service files.
            include_tests: Include test files.
            limit: Max rows.
            as_table: Render as table when supported.
        """
        mode_norm = (mode or "auto").strip().lower()
        if mode_norm not in {"auto", "ui", "backend", "cli"}:
            return "Invalid mode. Use 'auto', 'ui', 'backend', or 'cli'."

        if mode_norm in {"auto", "ui"}:
            ui_result = await get_app_flow_summary(
                workspace_id,
                ui_contains=ui_contains,
                model_contains=model_contains,
                service_contains=service_contains,
                include_tests=include_tests,
                limit=limit,
                as_table=as_table,
            )
            if mode_norm == "ui":
                return ui_result
            if not ui_result.startswith("No UI → API → Service → DB paths found"):
                return ui_result

        backend_result = await get_backend_flow_summary(
            workspace_id,
            api_contains=api_contains,
            model_contains=model_contains,
            service_contains=service_contains,
            include_tests=include_tests,
            limit=limit,
            as_table=as_table,
        )
        if mode_norm == "backend" or not backend_result.startswith(
            "No API → Service → DB paths found"
        ):
            return backend_result

        if mode_norm in {"auto", "cli"}:
            cli_result = await graph_core._get_cli_flow_summary(
                workspace_id,
                include_tests=include_tests,
                limit=limit,
                as_table=as_table,
            )
            if mode_norm == "cli" or not cli_result.startswith("No CLI"):
                return cli_result

        # Step 2: Heuristic Fallback
        heuristic_result = await get_heuristic_flow_summary(
            workspace_id, limit=limit, as_table=as_table
        )
        if not heuristic_result.startswith("No heuristic"):
            return f"### Heuristic Flow Summary\n{heuristic_result}"

        # Step 3: Topology Summary (Last Resort)
        return await get_topology_summary(workspace_id, limit=limit)

    async def get_heuristic_flow_summary(
        workspace_id: str, limit: int = 20, as_table: bool = False
    ) -> str:
        """Heuristic flow based on directory patterns and IMPORTS edges."""
        try:
            import graph_bootstrap

            project_id = get_project_id(workspace_id)
            driver = await graph_bootstrap.require_driver()
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                # Optimized heuristic query: look for UI -> API -> Service -> Model chains
                result = await graph_core._execute_read(
                    session,
                    """
                    MATCH (f1:File {project_id: $p})
                    WHERE (f1.filepath CONTAINS 'ui' OR f1.filepath CONTAINS 'view' OR f1.filepath CONTAINS 'component' OR f1.filepath CONTAINS 'pages')
                      AND NOT (f1.filepath CONTAINS 'test' OR f1.filepath CONTAINS 'spec')
                    MATCH (f1)-[:IMPORTS]->(f2:File {project_id: $p})
                    WHERE (f2.filepath CONTAINS 'api' OR f2.filepath CONTAINS 'client' OR f2.filepath CONTAINS 'controller' OR f2.filepath CONTAINS 'routes')
                    OPTIONAL MATCH (f2)-[:IMPORTS]->(f3:File {project_id: $p})
                    WHERE (f3.filepath CONTAINS 'service' OR f3.filepath CONTAINS 'domain' OR f3.filepath CONTAINS 'provider' OR f3.filepath CONTAINS 'usecase')
                    OPTIONAL MATCH (f3)-[:IMPORTS]->(f4:File {project_id: $p})
                    WHERE (f4.filepath CONTAINS 'model' OR f4.filepath CONTAINS 'db' OR f4.filepath CONTAINS 'entity' OR f4.filepath CONTAINS 'schema')
                    RETURN f1.filepath AS ui, f2.filepath AS api, f3.filepath AS svc, f4.filepath AS model
                    ORDER BY ui, api
                    LIMIT $limit
                    """,
                    p=project_id,
                    limit=limit * 2,
                    op="get_heuristic_flow_summary",
                )
                if not result:
                    return "No heuristic paths found."

                rows = []
                for row in result:
                    path = [v for v in [row.get("ui"), row.get("api"), row.get("svc"), row.get("model")] if v]
                    if len(path) >= 2:
                        rows.append(path)

                if not rows:
                    return "No heuristic paths found."

                if as_table:
                    out = ["| Origin | Endpoint | Secondary | Data |", "| --- | --- | --- | --- |"]
                    for row in rows[:limit]:
                        padded = row + [""] * (4 - len(row))
                        out.append(f"| {' | '.join(padded)} |")
                    return "\n".join(out)
                else:
                    return "\n".join([" -> ".join(r) for r in rows[:limit]])
        except Exception as e:
            return f"Error in heuristic flow: {str(e)}"

    async def get_topology_summary(workspace_id: str, limit: int = 10) -> str:
        """High-level summary of the most connected files/directories."""
        try:
            import graph_bootstrap

            project_id = get_project_id(workspace_id)
            driver = await graph_bootstrap.require_driver()
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                result = await graph_core._execute_read(
                    session,
                    """
                    MATCH (f:File {project_id: $p})
                    OPTIONAL MATCH (f)-[:IMPORTS]->(out:File {project_id: $p})
                    OPTIONAL MATCH (in:File {project_id: $p})-[:IMPORTS]->(f)
                    WITH f, count(DISTINCT out) AS outbound, count(DISTINCT in) AS inbound
                    WHERE inbound + outbound > 0
                    RETURN f.filepath AS fp, inbound, outbound
                    ORDER BY inbound + outbound DESC
                    LIMIT $limit
                    """,
                    p=project_id,
                    limit=limit,
                    op="get_topology_summary",
                )
                if not result:
                    return "No architectural topology found (index might be empty)."

                out = ["### Architectural Topology (Most Connected Files)\n"]
                for rec in result:
                    out.append(f"- `{rec['fp']}`: {rec['inbound']} incoming, {rec['outbound']} outgoing imports")
                return "\n".join(out)
        except Exception as e:
            return f"Error in topology summary: {str(e)}"

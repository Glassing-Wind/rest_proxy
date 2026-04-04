"""tools/graph/tools.py — MCP tool registration for graph features."""

from __future__ import annotations

import hashlib
import os
from mcp.server.fastmcp import FastMCP

from _helpers import get_memory_modules
from tools.graph import core as graph_core
from tools.graph import runtime as graph_runtime


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_project_overview(project_path: str) -> str:
        """
        Single-call project onboarding summary. Combines health, architecture
        clusters, and most important files into one synthesized view.
        Use this as the FIRST tool when starting work on an unfamiliar codebase.

        Args:
            project_path: Absolute path to the project root.
        """
        try:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
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
                    "MATCH (s {project_id:$p}) WHERE s:Function OR s:Class OR s:Struct "
                    "OR s:Trait OR s:Enum RETURN count(s) AS syms",
                    p=project_id,
                    op="get_project_overview_symbol_count",
                )
                rec2 = r2[0] if r2 else None
                n_syms = rec2["syms"] if rec2 else 0

                r3 = await graph_core._execute_read(
                    session,
                    """
                    MATCH (f:File {project_id: $p})
                    WITH f, CASE WHEN f.filepath CONTAINS '/'
                         THEN split(f.filepath, '/')[0] ELSE '(root)' END AS top_dir
                    OPTIONAL MATCH (f)-[:CONTAINS]->(s)
                    WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
                    WITH top_dir, count(DISTINCT s) AS syms, count(DISTINCT f) AS files
                    ORDER BY syms DESC LIMIT 6
                    RETURN top_dir, files, syms
                """,
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
                    MATCH (f:File {project_id: $p})-[:CONTAINS]->(s)
                    WHERE (s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum)
                      AND NOT f.filepath CONTAINS 'test'
                      AND NOT f.filepath CONTAINS 'spec'
                    WITH f.filepath AS fp, count(s) AS n, collect(DISTINCT s.name)[..3] AS ex
                    ORDER BY n DESC LIMIT 5
                    RETURN fp, n, ex
                """,
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
    async def build_import_graph(project_path: str) -> str:
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
            project_path: Absolute path to the project root.
        """
        return await graph_core._build_import_graph_impl(project_path)

    @mcp.tool()
    async def rebuild_symbol_graph(project_path: str) -> str:
        """
        Rebuild symbol-level IMPORTS/EXPORTS graph for a project.
        """
        return await graph_core._run_graph_build_with_retry(
            graph_core._build_symbol_import_export_graph_impl, "symbols", project_path
        )

    @mcp.tool()
    async def get_app_flow_summary(
        project_path: str,
        ui_contains: str | None = None,
        model_contains: str | None = None,
        service_contains: str | None = None,
        include_tests: bool = False,
        limit: int = 20,
        as_table: bool = False,
    ) -> str:
        """
        Summarize UI → API → Service → DB paths for a project.
        """
        try:
            import graph_bootstrap

            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            query_limit = limit
            if model_contains:
                query_limit = max(limit * 10, 200)
            driver = await graph_bootstrap.require_driver()
            rows = []
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
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
            return "\n".join(rows)
        except Exception as exc:
            return f"Error building flow summary: {str(exc)}"

    @mcp.tool()
    async def get_backend_flow_summary(
        project_path: str,
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

            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
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
        project_path: str,
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
                project_path,
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
            project_path,
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
                project_path,
                include_tests=include_tests,
                limit=limit,
                as_table=as_table,
            )
            return cli_result

        return backend_result

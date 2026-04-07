"""tools/graph/tools.py — MCP tool registration for graph features."""

from __future__ import annotations

import os
from mcp.server.fastmcp import FastMCP

from _helpers import get_project_id, get_workspace_path
from tools.brain.graph import core as graph_core
from tools.brain.graph import flow_summary as graph_flow_summary
from tools.brain.graph import overview as graph_overview
from tools.brain.graph import runtime as graph_runtime


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

            driver = await graph_bootstrap.require_driver()
            return await graph_overview.get_directory_snapshot_impl(
                driver=driver,
                neo4j_db=graph_bootstrap._NEO4J_DB,
                workspace_id=workspace_id,
                directory_path=directory_path,
                limit=limit,
            )
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
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            return await graph_overview.get_project_overview_impl(
                driver=driver,
                neo4j_db=graph_bootstrap._NEO4J_DB,
                workspace_id=workspace_id,
            )
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

            driver = await graph_bootstrap.require_driver()
            return await graph_flow_summary.get_app_flow_summary_impl(
                driver=driver,
                neo4j_db=graph_bootstrap._NEO4J_DB,
                workspace_id=workspace_id,
                ui_contains=ui_contains,
                entry_files=entry_files,
                entry_glob=entry_glob,
                model_contains=model_contains,
                service_contains=service_contains,
                include_tests=include_tests,
                limit=limit,
                as_table=as_table,
                group_by_ui=group_by_ui,
                include_coverage=include_coverage,
                max_per_ui=max_per_ui,
                expand_api_calls=expand_api_calls,
            )
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

            driver = await graph_bootstrap.require_driver()
            return await graph_flow_summary.get_backend_flow_summary_impl(
                driver=driver,
                neo4j_db=graph_bootstrap._NEO4J_DB,
                workspace_id=workspace_id,
                api_contains=api_contains,
                model_contains=model_contains,
                service_contains=service_contains,
                include_tests=include_tests,
                limit=limit,
                as_table=as_table,
            )
        except Exception as exc:
            return f"Error building backend flow summary: {str(exc)}"

    @mcp.tool()
    async def get_apple_build_summary(
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
        """
        Summarize Apple build graph paths from source file → resource → target → scheme → workspace.
        """
        try:
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            return await graph_flow_summary.get_apple_build_summary_impl(
                driver=driver,
                neo4j_db=graph_bootstrap._NEO4J_DB,
                workspace_id=workspace_id,
                source_contains=source_contains,
                resource_contains=resource_contains,
                target_contains=target_contains,
                scheme_contains=scheme_contains,
                workspace_contains=workspace_contains,
                limit=limit,
                as_table=as_table,
                group_by=group_by,
            )
        except Exception as exc:
            return f"Error building Apple build summary: {str(exc)}"

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
        mode: 'auto', 'ui', 'backend', 'apple', or 'cli'.
            ui_contains: Filter UI files (ui mode only).
            api_contains: Filter API files (backend mode only).
            model_contains: Filter model names.
            service_contains: Filter service files.
            include_tests: Include test files.
            limit: Max rows.
            as_table: Render as table when supported.
        """
        mode_norm = (mode or "auto").strip().lower()
        if mode_norm not in {"auto", "ui", "backend", "apple", "cli"}:
            return "Invalid mode. Use 'auto', 'ui', 'backend', 'apple', or 'cli'."

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

        apple_result = await get_apple_build_summary(
            workspace_id,
            source_contains=ui_contains,
            resource_contains=model_contains,
            target_contains=service_contains,
            group_by="target",
            limit=limit,
            as_table=as_table,
        )
        if mode_norm == "apple" or not apple_result.startswith("No Apple build graph paths found"):
            return apple_result

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

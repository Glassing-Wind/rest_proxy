"""tools/graph/tools.py — MCP tool registration for graph features."""

from __future__ import annotations

import os
from mcp.server.fastmcp import FastMCP

from _helpers import get_project_id, get_workspace_path
from tools.brain.graph import core as graph_core
from tools.brain.graph import flow_summary as graph_flow_summary
from tools.brain.graph import overview as graph_overview
from tools.brain.graph import utility as graph_utility


def register(mcp: FastMCP) -> None:

    def _is_missing_or_error_result(result: str, missing_prefixes: tuple[str, ...]) -> bool:
        if not result:
            return True
        if result.startswith("Error "):
            return True
        return any(result.startswith(prefix) for prefix in missing_prefixes)

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
    async def get_repo_dependency_summary(workspace_id: str) -> str:
        """
        Summarize repo-linked editable/path dependencies and where they are used.

        Useful for surfacing cross-repo boundaries such as local bindings,
        sibling workspaces, or editable Git dependencies that should not regress.

        Args:
            workspace_id: Logical workspace name or absolute project path.
        """
        try:
            return await graph_overview.get_repo_dependency_summary_impl(
                workspace_id=workspace_id,
            )
        except Exception as e:
            return f"Error generating repo dependency summary: {str(e)}"

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
        crate_contains: str | None = None,
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
                crate_contains=crate_contains,
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
        crate_contains: str | None = None,
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
            crate_contains: Filter backend rows by Cargo crate name (Rust workspaces).
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
                return f"### Flow Type: UI -> API -> Service -> DB\n{ui_result}"
            if not _is_missing_or_error_result(
                ui_result,
                ("No UI → API → Service → DB paths found",),
            ):
                return f"### Flow Type: UI -> API -> Service -> DB\n{ui_result}"

        if mode_norm == "apple":
            apple_result = await get_apple_build_summary(
                workspace_id,
                source_contains=ui_contains,
                resource_contains=model_contains,
                target_contains=service_contains,
                group_by="target",
                limit=limit,
                as_table=as_table,
            )
            return f"### Flow Type: Apple Build Graph\n{apple_result}"

        backend_result = await get_backend_flow_summary(
            workspace_id,
            api_contains=api_contains,
            crate_contains=crate_contains,
            model_contains=model_contains,
            service_contains=service_contains,
            include_tests=include_tests,
            limit=limit,
            as_table=as_table,
        )
        if mode_norm == "backend" or not _is_missing_or_error_result(
            backend_result,
            ("No API → Service → DB paths found",),
        ):
            return f"### Flow Type: API -> Service -> DB\n{backend_result}"

        apple_result = await get_apple_build_summary(
            workspace_id,
            source_contains=ui_contains,
            resource_contains=model_contains,
            target_contains=service_contains,
            group_by="target",
            limit=limit,
            as_table=as_table,
        )
        if mode_norm == "apple" or not _is_missing_or_error_result(
            apple_result,
            ("No Apple build graph paths found",),
        ):
            return f"### Flow Type: Apple Build Graph\n{apple_result}"

        if mode_norm in {"auto", "cli"}:
            cli_result = await graph_core._get_cli_flow_summary(
                workspace_id,
                include_tests=include_tests,
                limit=limit,
                as_table=as_table,
            )
            if mode_norm == "cli" or not _is_missing_or_error_result(
                cli_result,
                ("No CLI",),
            ):
                return f"### Flow Type: CLI\n{cli_result}"

        # Step 2: Heuristic Fallback
        heuristic_result = await get_heuristic_flow_summary(
            workspace_id, limit=limit, as_table=as_table
        )
        if not _is_missing_or_error_result(heuristic_result, ("No heuristic",)):
            return f"### Heuristic Flow Summary\n{heuristic_result}"

        # Step 3: Topology Summary (Last Resort)
        return await get_topology_summary(workspace_id, limit=limit)

    async def get_heuristic_flow_summary(
        workspace_id: str, limit: int = 20, as_table: bool = False
    ) -> str:
        """Heuristic flow based on directory patterns and IMPORTS edges."""
        try:
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            return await graph_utility.get_heuristic_flow_summary_impl(
                driver=driver,
                neo4j_db=graph_bootstrap._NEO4J_DB,
                workspace_id=workspace_id,
                limit=limit,
                as_table=as_table,
            )
        except Exception as e:
            return f"Error in heuristic flow: {str(e)}"

    async def get_topology_summary(workspace_id: str, limit: int = 10) -> str:
        """High-level summary of the most connected files/directories."""
        try:
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            return await graph_utility.get_topology_summary_impl(
                driver=driver,
                neo4j_db=graph_bootstrap._NEO4J_DB,
                workspace_id=workspace_id,
                limit=limit,
            )
        except Exception as e:
            return f"Error in topology summary: {str(e)}"

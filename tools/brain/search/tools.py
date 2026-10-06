"""tools/search/tools.py — graph-backed search summaries."""

from mcp.server.fastmcp import FastMCP

from tools.brain.search import summaries as search_summaries


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_symbol_imports_overview(
        project_path: str, limit: int = 20, include_implicit: bool = False
    ) -> str:
        """
        Summarize explicit (IMPORTS_SYMBOL) and implicit (IMPLICIT_IMPORTS_SYMBOL)
        symbol import edges for a project.

        Embedded mode returns cited declared-import observations as JSON; it does
        not claim resolved symbol bindings or implicit import edges.

        Args:
            project_path: Absolute path to the project root.
            limit: Max rows to return per section (default 20).
            include_implicit: Include heuristic IMPLICIT_IMPORTS_SYMBOL edges when true.
        """
        try:
            from memory.storage_config import embedded_graph_selected
            if embedded_graph_selected():
                import json
                from memory.embedded_runtime import get_embedded_runtime
                result = await get_embedded_runtime().workspace_import_overview(
                    project_path, limit=limit, include_implicit=include_implicit,
                )
                return json.dumps(result, ensure_ascii=False)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            return await search_summaries.get_symbol_imports_overview_impl(
                driver=driver,
                neo4j_db=graph_bootstrap._NEO4J_DB,
                project_path=project_path,
                limit=limit,
                include_implicit=include_implicit,
            )
        except Exception as e:
            return f"Error summarizing symbol imports overview: {str(e)}"

    @mcp.tool()
    async def get_symbol_exports_summary(
        project_path: str,
        limit: int = 20,
        include_paths: list | None = None,
        exclude_paths: list | None = None,
        symbol_prefix: str = "",
    ) -> str:
        """
        Summarize symbol-level export edges (EXPORTS_SYMBOL) for a project.

        Args:
            project_path: Absolute path to the project root.
            limit: Max rows to return per section (default 20).
            include_paths: Optional list of glob patterns to include (file_path).
            exclude_paths: Optional list of glob patterns to exclude (file_path).
            symbol_prefix: Optional prefix to filter exported symbols.
        """
        try:
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            return await search_summaries.get_symbol_exports_summary_impl(
                driver=driver,
                neo4j_db=graph_bootstrap._NEO4J_DB,
                project_path=project_path,
                limit=limit,
                include_paths=include_paths,
                exclude_paths=exclude_paths,
                symbol_prefix=symbol_prefix,
            )
        except Exception as e:
            return f"Error summarizing symbol exports: {str(e)}"

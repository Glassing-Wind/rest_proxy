"""Compact discovery profile with an explicitly scoped analysis dispatcher."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

PRIMARY_TOOL_NAMES: frozenset[str] = frozenset({
    "search_codebase", "get_symbol_context", "get_call_chain", "find_references",
    "describe_file", "get_project_overview", "search_documentation",
    "get_indexing_health", "resolve_graph_project", "get_mcp_tool_catalog",
    "trace_graph_provenance", "dispatch_deep_analysis",
})

OPTIONAL_PRIMARY_TOOL_NAMES = frozenset({
    "search_embedded_repository", "describe_embedded_file", "get_embedded_overview",
    "list_embedded_projects", "get_embedded_file_facts", "get_embedded_relationships",
})

# Keep write/admin tools and the dispatcher itself outside this route.
DEEP_ANALYSIS_TOOL_NAMES: frozenset[str] = frozenset({
    "get_app_flow_summary", "get_backend_flow_summary", "trace_symbol_cross_project",
    "find_code_duplication", "get_code_communities", "get_code_importance",
    "get_related_files", "visualize_subgraph", "extract_function_body",
    "extract_class_interface", "get_directory_snapshot", "get_repo_dependency_summary",
    "get_symbol_exports_summary", "get_symbol_imports_overview", "find_definitions",
    "list_symbol_matches", "grep_codebase", "get_test_coverage_for",
})


def register_primary_dispatcher(mcp: FastMCP) -> None:
    """Register access to the explicitly allowed secondary analysis tools."""

    @mcp.tool()
    async def dispatch_deep_analysis(
        tool_name: str,
        ctx: Context,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Run a secondary read analysis. Get its argument schema with
        get_mcp_tool_catalog(tool_name=...). Write/admin tools are excluded.
        """
        if tool_name not in DEEP_ANALYSIS_TOOL_NAMES:
            raise ValueError(f"Tool '{tool_name}' is not allowed for deep analysis")
        tool = mcp._tool_manager.get_tool(tool_name)
        if tool is None:
            raise ValueError(f"Tool '{tool_name}' is not registered")
        # Preserve dictionaries, content blocks, and SDK error semantics.
        return await tool.run(arguments or {}, context=ctx)


def apply_primary_tool_filter(mcp: FastMCP) -> None:
    """Limit discovery only; this profile is not an authorization boundary."""
    manager = mcp._tool_manager
    if getattr(manager, "_primary_filter_applied", False):
        return
    original_list_tools = manager.list_tools

    def filtered_list_tools() -> list:
        return [tool for tool in original_list_tools() if tool.name in PRIMARY_TOOL_NAMES | OPTIONAL_PRIMARY_TOOL_NAMES]

    manager.list_tools = filtered_list_tools
    manager._primary_filter_applied = True

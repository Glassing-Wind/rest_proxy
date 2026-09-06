"""tools/search/cross_project.py — MCP wrapper for cross-project symbol tracing."""

from mcp.server.fastmcp import FastMCP

from memory import cross_project_trace
from tools.brain.search import core as search_core


# Compatibility exports for focused helper tests and callers that imported the
# old tool-layer helpers directly.
_definition_rank = cross_project_trace._definition_rank
_is_test_like_cross_project_hit = cross_project_trace._is_test_like_cross_project_hit
_semantic_usage_rank = cross_project_trace._semantic_usage_rank
_symbol_centered_preview = cross_project_trace._symbol_centered_preview


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def trace_symbol_cross_project(
        symbol_name: str,
        source_workspace: str,
        target_workspace: str,
    ) -> str:
        """
        Trace a symbol from its definition in one project to its usages in another.

        Ideal for gRPC-style workflows: find where a service, message, or function
        is *defined* server-side and how it is *called or referenced* client-side.

        Combines two sources per side:
        - Neo4j structural graph (definition location, call-edges)
        - Postgres semantic search (text occurrences in chunks)

        Args:
            symbol_name: Exact name of the symbol to trace (e.g. 'GenerateImageRequest').
            source_workspace: Logical workspace ID or absolute path where the symbol is defined.
            target_workspace: Logical workspace ID or absolute path that consumes/calls the symbol.
        """
        try:
            return await cross_project_trace.trace_symbol_cross_project_core(
                symbol_name=symbol_name,
                source_workspace=source_workspace,
                target_workspace=target_workspace,
                execute_read=search_core._execute_read,
            )
        except Exception as e:
            return f"Error tracing cross-project symbol: {str(e)}"

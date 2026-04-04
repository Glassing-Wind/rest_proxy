"""Compatibility wrapper for legacy tools.project imports."""

from mcp.server.fastmcp import FastMCP

from tools.graph_tools import enqueue_graph_build, get_last_graph_build_metric


def register(mcp: FastMCP) -> None:
    from tools import docs_tools, graph_tools, search_tools

    docs_tools.register(mcp)
    graph_tools.register(mcp)
    search_tools.register(mcp)

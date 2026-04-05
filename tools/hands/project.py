"""Compatibility wrapper for legacy tools.project imports."""

from mcp.server.fastmcp import FastMCP

from tools.brain.graph.core import enqueue_graph_build, get_last_graph_build_metric


def register(mcp: FastMCP) -> None:
    from tools.brain.docs import core as docs_tools
    from tools.brain.graph import core as graph_tools
    from tools.brain.search import tools as search_tools

    docs_tools.register(mcp)
    graph_tools.register(mcp)
    search_tools.register(mcp)

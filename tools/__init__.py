"""
tools/__init__.py  —  Register all tool groups with a FastMCP instance.

Import order matters: indexing must be registered before watchers start.
"""

from mcp.server.fastmcp import FastMCP


def register_all(mcp: FastMCP) -> None:
    from tools import memory, code_search, code_intel, dev, documentation, indexing
    from tools.docs import core as docs_tools
    from tools.graph import core as graph_tools
    from tools.search import tools as search_tools

    memory.register(mcp)
    code_search.register(mcp)
    code_intel.register(mcp)
    dev.register(mcp)
    documentation.register(mcp)
    indexing.register(mcp)
    docs_tools.register(mcp)
    graph_tools.register(mcp)
    search_tools.register(mcp)

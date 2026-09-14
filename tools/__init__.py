"""
tools/__init__.py  —  Register all tool groups with a FastMCP instance.

Phase 2: Partitioned into brain/ (reasoning) and hands/ (filesystem).
"""

from mcp.server.fastmcp import FastMCP


def register_all(mcp: FastMCP) -> None:
    from tools.brain import memory, code_search, code_intel, documentation, tool_catalog
    from tools.brain.graph import core as graph_tools
    from tools.brain.search import tools as search_tools
    from tools.hands import dev, indexing, project

    memory.register(mcp)
    tool_catalog.register(mcp)
    code_search.register(mcp)
    code_intel.register(mcp)
    dev.register(mcp)
    documentation.register(mcp)
    indexing.register(mcp)
    graph_tools.register(mcp)
    search_tools.register(mcp)
    project.register(mcp)

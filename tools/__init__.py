"""
tools/__init__.py  —  Register all tool groups with a FastMCP instance.

Phase 2: Partitioned into brain/ (reasoning) and hands/ (filesystem).
"""

import sys
from mcp.server.fastmcp import FastMCP
import proxy.config as config

def register_all(mcp: FastMCP) -> None:
    from tools.brain import memory, code_search, code_intel, documentation
    from tools.brain.docs import core as docs_tools
    from tools.brain.graph import core as graph_tools
    from tools.brain.search import tools as search_tools
    from tools.hands import dev, indexing, project

    # ---------------------------------------------------------------------------
    # Interlink (Multi-Agent Messaging)
    # ---------------------------------------------------------------------------
    if config._INTERLINK_ENABLED:
        try:
            from tools import interlink
            interlink.register_interlink_tools(mcp)
        except Exception as e:
            print(f"[lm-proxy] Failed to register interlink tools: {e}", file=sys.stderr)

    memory.register(mcp)
    code_search.register(mcp)
    code_intel.register(mcp)
    dev.register(mcp)
    documentation.register(mcp)
    indexing.register(mcp)
    docs_tools.register(mcp)
    graph_tools.register(mcp)
    search_tools.register(mcp)
    project.register(mcp)


"""
_mcp.py — Shared GraphRAG MCP instance and tool registration.

This module provides a singleton 'mcp' instance that is used by both
mcp_server.py (STDIO) and brain_server.py (Streamable HTTP).
"""

import sys
from mcp.server.fastmcp import FastMCP
from graphrag_core.config import load_env
from tools import register_all

# Ensure environment variables are loaded
load_env()

# Initialize the shared FastMCP instance
mcp = FastMCP("graphrag-brain")

# Register all tool groups (partitioned into tools/brain and tools/hands)
register_all(mcp)

def get_mcp() -> FastMCP:
    """Returns the shared FastMCP instance."""
    return mcp

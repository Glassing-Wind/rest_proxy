"""
_mcp.py — Shared GraphRAG MCP instance and tool registration.

This module provides a singleton 'mcp' instance that is used by both
mcp_server.py (STDIO) and brain_server.py (Streamable HTTP).
"""

from mcp.server.fastmcp import FastMCP
from graphrag_core.config import load_env
from tools import register_all

# Ensure environment variables are loaded
load_env()

# Initialize the shared FastMCP instance.
# stateless_http=True: disables per-client session management for the
# Streamable HTTP transport. Clients don't need to track Mcp-Session-Id.
# brain_server.py mounts the MCP Starlette app at "/" (root) so Starlette
# passes the full path "/mcp" to the sub-app, which matches its internal
# route at "/mcp" (the default streamable_http_path). No path stripping issues.
mcp = FastMCP("graphrag-brain", stateless_http=True)

# Register all tool groups (partitioned into tools/brain and tools/hands)
register_all(mcp)

def get_mcp() -> FastMCP:
    """Returns the shared FastMCP instance."""
    return mcp

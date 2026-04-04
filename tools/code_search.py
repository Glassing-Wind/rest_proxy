"""Compatibility wrapper for legacy tools.code_search imports."""

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    from tools import search_core

    search_core.register(mcp)

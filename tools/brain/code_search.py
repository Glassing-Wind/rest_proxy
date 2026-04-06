"""Compatibility wrapper for legacy tools.code_search imports."""

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    from tools.brain.search import core as search_core

    search_core.register(mcp)

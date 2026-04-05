"""Compatibility wrapper for legacy tools.code_intel imports."""

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    from tools.brain.code_intel import core as code_intel_core

    code_intel_core.register(mcp)

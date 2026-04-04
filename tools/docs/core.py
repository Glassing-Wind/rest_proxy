"""tools/docs/core.py — documentation tool registration."""

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    from tools.docs import usage_guide as docs_usage

    docs_usage.register(mcp)

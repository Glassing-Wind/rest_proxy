"""tools/documentation.py — documentation tool registration."""

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    from tools.brain.docs import research as docs_research
    from tools.brain.docs import index_jobs as docs_index_jobs
    from tools.brain.docs import search as docs_search
    from tools.brain.docs import admin as docs_admin

    docs_research.register(mcp)
    docs_index_jobs.register(mcp)
    docs_search.register(mcp)
    docs_admin.register(mcp)

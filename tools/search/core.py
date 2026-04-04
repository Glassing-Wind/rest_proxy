"""tools/search/core.py — shared Neo4j helpers and tool registration."""

import os
from neo4j import unit_of_work
from mcp.server.fastmcp import FastMCP


_TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
_TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "code_search"}


async def _execute_read(session, cypher: str, op: str | None = None, **params):
    metadata = dict(_TX_METADATA_BASE)
    op_value = op or "read"
    if _TX_OP_PREFIX:
        op_value = f"{_TX_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    @unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)
    async def _tx(tx):
        result = await tx.run(cypher, **params)
        return await result.data()

    if hasattr(session, "execute_read"):
        return await session.execute_read(_tx)
    return await _tx(session)


def register(mcp: FastMCP) -> None:
    from tools.search import semantic as search_semantic
    from tools.search import cross_project as search_cross_project
    from tools.search import duplication as search_duplication
    from tools.search import graph_query as search_graph_query

    search_semantic.register(mcp)
    search_cross_project.register(mcp)
    search_duplication.register(mcp)
    search_graph_query.register(mcp)

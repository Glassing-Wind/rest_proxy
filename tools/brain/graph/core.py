"""Core helpers for graph-backed tools."""

import os
import asyncio
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules, get_project_id
from graphrag_core import neo4j as neo4j_utils

_GRAPH_WRITE_CONCURRENCY = max(
    1, int(os.getenv("LM_PROXY_GRAPH_WRITE_CONCURRENCY", "2"))
)
_NEO4J_READ_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_READ_TIMEOUT", "30.0"))
_NEO4J_WRITE_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_WRITE_TIMEOUT", "120.0"))
_TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "project"}
_NEO4J_GRAPH_BUILD_BATCH = max(50, int(os.getenv("LM_PROXY_GRAPH_BUILD_BATCH", "500")))

# Standard symbol labels and kinds for architectural queries.
# Keep `Macro` as a kind match but not a label probe to avoid Neo4j warnings
# on projects whose graph schema never materializes a `:Macro` label.
_SYMBOL_LABELS = ["Function", "Class", "Struct", "Trait", "Enum", "Method", "Protocol", "Interface"]
_SYMBOL_KINDS = _SYMBOL_LABELS + ["Macro"]
_SYMBOL_FILTER_CYPHER = "(" + " OR ".join([f"s:{l}" for l in _SYMBOL_LABELS]) + " OR s.kind IN " + str(_SYMBOL_KINDS) + ")"



def _debug_log(message: str, **fields: object) -> None:
    try:
        _, _, _, _, proxy = get_memory_modules()
        proxy.debug_log(message, **fields)
    except Exception:
        return


def _record_metric(event: str, **fields: object) -> None:
    return None


def _is_deadlock_error(exc: Exception) -> bool:
    code = getattr(exc, "code", "") or getattr(exc, "gql_status", "")
    if isinstance(code, str) and "DeadlockDetected" in code:
        return True
    msg = str(exc)
    return "DeadlockDetected" in msg or "deadlock" in msg.lower()


async def _execute_write(session, cypher: str, operation: str | None = None, **params) -> None:
    # Avoid conflict if 'op' is also in params
    params.pop("op", None)
    await neo4j_utils.execute_write(
        session,
        cypher,
        op=operation or "write",
        op_prefix=_TX_OP_PREFIX,
        timeout_s=_NEO4J_WRITE_TIMEOUT_S,
        base_metadata=_TX_METADATA_BASE,
        **params,
    )


async def _execute_read(session, cypher: str, operation: str | None = None, **params):
    # Avoid conflict if 'op' is also in params
    params.pop("op", None)
    return await neo4j_utils.execute_read(
        session,
        cypher,
        op=operation or "read",
        op_prefix=_TX_OP_PREFIX,
        timeout_s=_NEO4J_READ_TIMEOUT_S,
        base_metadata=_TX_METADATA_BASE,
        **params,
    )


async def _run_graph_build_with_retry(fn, label: str, project_path: str) -> str:
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            result = await fn(project_path)
        except Exception as exc:
            if _is_deadlock_error(exc) and attempt < attempts:
                _debug_log(
                    "graph_build_retry",
                    project_path=project_path,
                    phase=label,
                    attempt=attempt,
                )
                await asyncio.sleep(0.2 * attempt)
                continue
            raise
        if isinstance(result, str) and "deadlock" in result.lower():
            if attempt < attempts:
                _debug_log(
                    "graph_build_retry",
                    project_path=project_path,
                    phase=label,
                    attempt=attempt,
                )
                await asyncio.sleep(0.2 * attempt)
                continue
        if isinstance(result, str):
            return result
        return f"{label} graph build completed"
    return f"{label} graph build completed"


def _summarize_batches(event: str, limit: int = 50) -> tuple[int, int, int]:
    return (0, 0, 0)


def get_last_graph_build_metric() -> dict[str, object] | None:
    return None


async def run_post_index_graph_build(project_path: str) -> str:
    """The structural indexer now owns deterministic graph derivation and writes."""
    _debug_log("graph_build_inline_skipped", project_path=project_path, reason="rust_owned")
    return "Graph derivation completed during structural indexing."


def register(mcp: FastMCP) -> None:
    from tools.brain.graph import tools as graph_tools

    graph_tools.register(mcp)


async def _get_cli_flow_summary(
    project_path: str,
    include_tests: bool = False,
    limit: int = 20,
    as_table: bool = False,
) -> str:
    from tools.brain.graph import cli as graph_cli

    return await graph_cli.get_cli_flow_summary(
        project_path,
        execute_read=_execute_read,
        include_tests=include_tests,
        limit=limit,
        as_table=as_table,
    )

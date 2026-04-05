"""tools/graph/core.py — project health, overview, import graph, and usage guide tools."""

import os
import threading
import asyncio
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules, get_project_id
from graphrag_core import neo4j as neo4j_utils


_SYMBOL_GRAPH_LOCK = threading.Lock()

_GRAPH_WRITE_CONCURRENCY = max(
    1, int(os.getenv("LM_PROXY_GRAPH_WRITE_CONCURRENCY", "2"))
)
_NEO4J_READ_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_READ_TIMEOUT", "3.0"))
_NEO4J_WRITE_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_WRITE_TIMEOUT", "15.0"))
_TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "project"}
_NEO4J_GRAPH_BUILD_BATCH = max(50, int(os.getenv("LM_PROXY_GRAPH_BUILD_BATCH", "500")))

_WRITE_SEM = asyncio.Semaphore(_GRAPH_WRITE_CONCURRENCY)
_GRAPH_RUNTIME_CONFIGURED = False

# Standard symbol labels for architectural queries
_SYMBOL_LABELS = ["Function", "Class", "Struct", "Trait", "Enum", "Method", "Protocol"]
_SYMBOL_FILTER_CYPHER = " OR ".join([f"s:{l}" for l in _SYMBOL_LABELS])



def _debug_log(message: str, **fields: object) -> None:
    try:
        _, _, _, _, proxy = get_memory_modules()
        proxy.debug_log(message, **fields)
    except Exception:
        return


def _record_metric(event: str, **fields: object) -> None:
    from tools.graph import runtime as graph_runtime

    graph_runtime.record_metric(event, **fields)


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
    from tools.graph import runtime as graph_runtime

    return graph_runtime.summarize_batches(event, limit)


def get_last_graph_build_metric() -> dict[str, object] | None:
    from tools.graph import runtime as graph_runtime

    return graph_runtime.get_last_graph_build_metric()


def _ensure_graph_runtime_configured() -> None:
    global _GRAPH_RUNTIME_CONFIGURED
    if _GRAPH_RUNTIME_CONFIGURED:
        return
    from tools.graph import runtime as graph_runtime

    graph_runtime.configure(
        debug_log=_debug_log,
        run_build_with_retry=_run_graph_build_with_retry,
        build_import_graph=_build_import_graph_impl,
        build_symbol_graph=_build_symbol_import_export_graph_impl,
        build_asset_graph=_build_asset_graph_impl,
    )
    _GRAPH_RUNTIME_CONFIGURED = True


async def enqueue_graph_build(
    project_path: str, run_imports: bool = True, run_symbols: bool = True
) -> None:
    _ensure_graph_runtime_configured()
    from tools.graph import runtime as graph_runtime

    await graph_runtime.enqueue_graph_build(
        project_path, run_imports=run_imports, run_symbols=run_symbols
    )


def register(mcp: FastMCP) -> None:
    from tools.graph import tools as graph_tools

    graph_tools.register(mcp)


async def _get_cli_flow_summary(
    project_path: str,
    include_tests: bool = False,
    limit: int = 20,
    as_table: bool = False,
) -> str:
    from tools.graph import cli as graph_cli

    return await graph_cli.get_cli_flow_summary(
        project_path,
        execute_read=_execute_read,
        include_tests=include_tests,
        limit=limit,
        as_table=as_table,
    )


async def _build_import_graph_impl(project_path: str) -> str:
    """Module-level implementation callable from _jobs.py post-index hook."""
    from tools.graph import import_graph

    return await import_graph.build_import_graph(
        project_path,
        execute_read=_execute_read,
        execute_write=_execute_write,
        debug_log=_debug_log,
        record_metric=_record_metric,
        write_semaphore=_WRITE_SEM,
        batch_size=_NEO4J_GRAPH_BUILD_BATCH,
        write_timeout_s=_NEO4J_WRITE_TIMEOUT_S,
    )


async def _build_asset_graph_impl(project_path: str) -> str:
    """Build asset linkage edges (HTML → assets, JS/TS → API spec/routes)."""
    from tools.graph import asset_graph

    return await asset_graph.build_asset_graph(
        project_path,
        execute_read=_execute_read,
        execute_write=_execute_write,
        debug_log=_debug_log,
        write_semaphore=_WRITE_SEM,
        batch_size=_NEO4J_GRAPH_BUILD_BATCH,
        write_timeout_s=_NEO4J_WRITE_TIMEOUT_S,
    )


async def _build_symbol_import_export_graph_impl(project_path: str) -> str:
    """Build symbol-level IMPORTS/EXPORTS edges using Import nodes and chunk metadata."""
    from tools.graph import symbol_graph

    return await symbol_graph.build_symbol_graph(
        project_path,
        execute_read=_execute_read,
        execute_write=_execute_write,
        debug_log=_debug_log,
        record_metric=_record_metric,
        write_semaphore=_WRITE_SEM,
        batch_size=_NEO4J_GRAPH_BUILD_BATCH,
        write_timeout_s=_NEO4J_WRITE_TIMEOUT_S,
        symbol_graph_lock=_SYMBOL_GRAPH_LOCK,
    )

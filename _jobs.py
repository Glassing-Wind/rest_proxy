"""
Background index-job registry.

Shared between tools/indexing.py and tools/documentation.py.
Each entry: {status, struct_rc, sem_rc, logs[], started_at, finished_at}
"""

import os
import sys
import threading
import asyncio
import time
from typing import Dict, Any
from contextvars import ContextVar

# Context for session-scoped operations in multi-client Brain server
client_session_id: ContextVar[str | None] = ContextVar("client_session_id", default=None)

_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_MAX_LOG_LINES = 200  # ring-buffer size per job

# The main asyncio event loop, captured at server startup.
# _finalize_job runs in a worker thread and must schedule async work
# (import graph build) back onto this loop — not create a new one —
# because the Neo4j async driver is bound to it.
_MAIN_LOOP = None


async def _execute_read(
    session, cypher: str, timeout: float | None = None, op: str | None = None, **params
):
    from neo4j import unit_of_work

    tx_timeout = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
    op_prefix = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
    metadata = {"source": "lm_proxy", "tool": "jobs"}
    op_value = op or "read"
    if op_prefix:
        op_value = f"{op_prefix}.{op_value}"
    metadata["op"] = op_value

    @unit_of_work(timeout=timeout or tx_timeout, metadata=metadata)
    async def _tx(tx):
        result = await tx.run(cypher, **params)
        return await result.data()

    if hasattr(session, "execute_read"):
        return await session.execute_read(_tx)
    return await _tx(session)


def register_main_loop(loop) -> None:
    """Called once at server startup to store the running event loop."""
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def _drain_proc_output(proc, job_id: str, prefix: str, rc_key: str) -> None:
    """Drain stdout+stderr of *proc* into the job log ring-buffer.

    Runs in a daemon thread. When the process exits, stores its return code.
    """
    import time as _time

    assert proc.stderr is not None
    for raw_line in proc.stderr:
        line = f"{prefix} {raw_line.rstrip()}"
        print(line, file=sys.stderr, flush=True)
        with _JOBS_LOCK:
            if job_id in _JOBS:
                logs = _JOBS[job_id]["logs"]
                logs.append(line)
                if len(logs) > _MAX_LOG_LINES:
                    del logs[0]
    proc.wait()
    cancel_requested = False
    project_path = ""
    project_id = ""
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id][rc_key] = proc.returncode


def _finalize_job(job_id: str, manifest_path: str) -> None:
    """Watch for both phases to complete, then set status and clean up."""
    import time as _time

    while True:
        _time.sleep(0.5)
        with _JOBS_LOCK:
            job = _JOBS.get(job_id, {})
            struct_rc = job.get("struct_rc")
            sem_rc = job.get("sem_rc")
        if struct_rc is not None and sem_rc is not None:
            break
    try:
        if os.path.exists(manifest_path):
            os.remove(manifest_path)
    except OSError:
        pass
    import time as _t

    with _JOBS_LOCK:
        if job_id in _JOBS:
            cancel_requested = bool(_JOBS[job_id].get("cancel_requested"))
            ok = struct_rc == 0 and sem_rc == 0
            if cancel_requested:
                _JOBS[job_id]["status"] = "cancelled"
            else:
                _JOBS[job_id]["status"] = "done" if ok else "failed"
            _JOBS[job_id]["finished_at"] = _t.time()
            project_path = _JOBS[job_id].get("project_path", "")
            project_id = _JOBS[job_id].get("project_id", "")

    # Refresh structural metadata whenever the structural phase succeeded,
    # even if semantic indexing failed. Graph-backed tools remain useful.
    if (
        struct_rc == 0
        and project_path
        and project_id
        and not project_path.startswith("docs://")
        and not cancel_requested
    ):
        try:
            import asyncio
            import graph_bootstrap
            from tools.brain.graph.core import enqueue_graph_build

            async def _post_index_maintenance() -> str | None:
                from neo4j import unit_of_work

                driver = await graph_bootstrap.require_driver()
                async with driver.session(
                    database=graph_bootstrap._NEO4J_DB
                ) as session:
                    tx_timeout = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
                    metadata = {
                        "source": "lm_proxy",
                        "tool": "jobs",
                        "op": "post_index_refresh",
                    }

                    @unit_of_work(timeout=tx_timeout, metadata=metadata)
                    async def _tx(tx):
                        # Construct a multi-SET query based on success of phases
                        # If structural indexing succeeded, refresh 'indexed_at'
                        # If semantic indexing succeeded, refresh 'vector_indexed_at'
                        set_bits = []
                        if struct_rc == 0:
                            set_bits.append("f.indexed_at = timestamp()")
                        if sem_rc == 0:
                            set_bits.append("f.vector_indexed_at = timestamp()")
                        
                        if not set_bits:
                            return None

                        cypher = f"MATCH (f:File {{project_id: $pid}}) SET {', '.join(set_bits)}"
                        res = await tx.run(cypher, pid=project_id)
                        await res.consume()

                    if hasattr(session, "execute_write"):
                        await session.execute_write(_tx)
                    else:
                        await _tx(session)
                await enqueue_graph_build(
                    project_path, run_imports=True, run_symbols=True
                )

            if _MAIN_LOOP is not None and _MAIN_LOOP.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    _post_index_maintenance(),
                    _MAIN_LOOP,
                )
                try:
                    future.result(timeout=10)
                except Exception:
                    pass
                queued = "enqueued + timestamps refreshed"
            else:
                queued = "skipped: main loop not available"
            with _JOBS_LOCK:
                if job_id in _JOBS:
                    _JOBS[job_id].pop("clone_enrich_status", None)
                    _JOBS[job_id].pop("clone_enrich_msg", None)
                    if _JOBS[job_id].get("logs"):
                        _JOBS[job_id]["logs"] = [
                            line
                            for line in _JOBS[job_id]["logs"]
                            if "[clone-enrich]" not in line
                        ]
                    _JOBS[job_id]["logs"].append(f"[graph-build] {queued}")
        except Exception as e:
            with _JOBS_LOCK:
                if job_id in _JOBS:
                    _JOBS[job_id]["logs"].append(f"[graph-build] enqueue failed: {e}")

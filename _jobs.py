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


def _append_job_log(job_id: str, line: str) -> None:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            logs = _JOBS[job_id]["logs"]
            logs.append(line)
            if len(logs) > _MAX_LOG_LINES:
                del logs[0]


def _drain_stream(stream, job_id: str, prefix: str, sink) -> None:
    if stream is None:
        return
    for raw_line in stream:
        line = f"{prefix} {raw_line.rstrip()}"
        print(line, file=sink, flush=True)
        _append_job_log(job_id, line)


def _drain_proc_output(proc, job_id: str, prefix: str, rc_key: str) -> None:
    """Drain child stdout+stderr into the job log ring-buffer.

    Runs in a daemon thread. When the process exits, stores its return code.
    """
    stdout_thread = threading.Thread(
        target=_drain_stream,
        args=(proc.stdout, job_id, prefix, sys.stdout),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(proc.stderr, job_id, prefix, sys.stderr),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    proc.wait()
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
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

    project_path = ""
    project_id = ""
    cancel_requested = False
    with _JOBS_LOCK:
        if job_id in _JOBS:
            cancel_requested = bool(_JOBS[job_id].get("cancel_requested"))
            ok = struct_rc == 0 and sem_rc == 0
            project_path = _JOBS[job_id].get("project_path", "")
            project_id = _JOBS[job_id].get("project_id", "")
            if cancel_requested:
                _JOBS[job_id]["status"] = "cancelled"
            else:
                _JOBS[job_id]["status"] = "done" if ok else "failed"
                _JOBS[job_id]["finished_at"] = _t.time()

    # Refresh structural metadata whenever the structural phase succeeded,
    # even if semantic indexing failed. Graph-backed tools remain useful.
    graph_build_error = None
    if (
        struct_rc == 0
        and project_path
        and project_id
        and not project_path.startswith("docs://")
        and not cancel_requested
    ):
        try:
            import graph_bootstrap
            run_summary = None

            async def _post_index_maintenance() -> None:
                from neo4j import unit_of_work

                nonlocal run_summary
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

                        if struct_rc == 0 and sem_rc == 0:
                            promote_semantic = await tx.run(
                                """
                                MATCH (p:Project {id:$pid})
                                WITH p, coalesce(p.struct_active_run_id, p.struct_last_successful_run_id) AS struct_run_id
                                WHERE struct_run_id IS NOT NULL AND p.semantic_active_run_id IS NOT NULL
                                SET p.semantic_target_struct_run_id = struct_run_id,
                                    p.semantic_active_struct_run_id = struct_run_id,
                                    p.semantic_index_status = 'done',
                                    p.semantic_index_finished_at = timestamp()
                                WITH p, struct_run_id
                                MATCH (r:IndexRun {id:p.semantic_active_run_id})
                                SET r.target_struct_run_id = struct_run_id,
                                    r.status = 'done',
                                    r.finished_at = timestamp(),
                                    r.promoted_at = timestamp()
                                RETURN struct_run_id AS struct_run_id, p.semantic_active_run_id AS semantic_run_id
                                """,
                                pid=project_id,
                            )
                            await promote_semantic.consume()

                    async def _read_summary(tx):
                        result = await tx.run(
                            """
                            MATCH (p:Project {id:$pid})
                            RETURN
                              p.struct_active_run_id AS struct_active_run_id,
                              p.semantic_active_run_id AS semantic_active_run_id,
                              p.semantic_active_struct_run_id AS semantic_active_struct_run_id,
                              p.struct_index_status AS struct_index_status,
                              p.semantic_index_status AS semantic_index_status
                            """,
                            pid=project_id,
                        )
                        return await result.data()

                    if hasattr(session, "execute_write"):
                        await session.execute_write(_tx)
                    else:
                        await _tx(session)
                    if hasattr(session, "execute_read"):
                        rows = await session.execute_read(_read_summary)
                    else:
                        rows = await _read_summary(session)
                    if rows:
                        run_summary = rows[0]
            if _MAIN_LOOP is not None and _MAIN_LOOP.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    _post_index_maintenance(),
                    _MAIN_LOOP,
                )
                try:
                    future.result(timeout=180)
                except Exception as exc:
                    graph_build_error = str(exc)
                queued = "timestamps refreshed" if graph_build_error is None else f"failed: {graph_build_error}"
            else:
                graph_build_error = "main loop not available"
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
                    if run_summary:
                        _JOBS[job_id]["run_summary"] = run_summary
                    _JOBS[job_id]["logs"].append(f"[struct-index] {queued}")
        except Exception as e:
            graph_build_error = str(e)
            with _JOBS_LOCK:
                if job_id in _JOBS:
                    _JOBS[job_id]["logs"].append(f"[struct-index] post-index refresh failed: {e}")

    with _JOBS_LOCK:
        if job_id in _JOBS and _JOBS[job_id].get("finished_at") is None:
            if cancel_requested:
                _JOBS[job_id]["status"] = "cancelled"
            elif graph_build_error:
                _JOBS[job_id]["status"] = "failed"
            else:
                _JOBS[job_id]["status"] = "done" if (struct_rc == 0 and sem_rc == 0) else "failed"
            _JOBS[job_id]["finished_at"] = _t.time()

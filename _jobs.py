"""
Background index-job registry.

Shared between tools/indexing.py and tools/documentation.py.
Each entry: {status, struct_rc, sem_rc, logs[], started_at, finished_at}
"""

import os
import re
import sys
import threading
import asyncio
import time
import json
from typing import Dict, Any
from contextvars import ContextVar
from pathlib import Path

# Context for session-scoped operations in multi-client Brain server
client_session_id: ContextVar[str | None] = ContextVar("client_session_id", default=None)

_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_MAX_LOG_LINES = 200  # ring-buffer size per job
_RUNTIME_JOBS_DIR = Path(__file__).resolve().parent / ".runtime" / "jobs"
_PROJECT_LOCKS_DIR = Path(__file__).resolve().parent / ".runtime" / "project_locks"
_PROJECT_LOCK_STALE_S = 6 * 60 * 60

# The main asyncio event loop, captured at server startup.
# _finalize_job runs in a worker thread and must schedule async work
# (import graph build) back onto this loop — not create a new one —
# because the Neo4j async driver is bound to it.
_MAIN_LOOP = None


_STRUCT_DONE_RE = re.compile(
    r"\[ts-pack-index\] Done — (?P<files>\d+) files \| "
    r"parse=(?P<parse>[0-9.]+)s nodes=(?P<nodes>[0-9.]+)s imports=(?P<imports>[0-9.]+)s "
    r"rels=(?P<rels>[0-9.]+)s calls=(?P<calls>[0-9.]+)s total=(?P<total>[0-9.]+)s"
)
_SEM_DONE_RE = re.compile(
    r"\[lm-proxy:indexer\] Done — (?P<new>\d+) new / (?P<skipped>\d+) skipped / "
    r"(?P<files>\d+) files in (?P<total>[0-9.]+)s \(parsed=(?P<parsed>\d+) skipped_files=(?P<skipped_files>\d+)\)"
)
_GDS_OK_RE = re.compile(r"\[ts-pack:(?P<label>leiden|betweenness|wcc)\] Done — (?P<detail>.+)")
_GDS_SKIP_RE = re.compile(r"\[ts-pack:(?P<label>leiden|betweenness|wcc)\] Skipped — (?P<detail>.+)")


def _ensure_jobs_runtime_dir() -> Path:
    _RUNTIME_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return _RUNTIME_JOBS_DIR


def _ensure_project_locks_dir() -> Path:
    _PROJECT_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    return _PROJECT_LOCKS_DIR


def _job_dir(job_id: str) -> Path:
    return _ensure_jobs_runtime_dir() / job_id


def _job_state_path(job_id: str) -> Path:
    return _job_dir(job_id) / "state.json"


def _job_log_path(job_id: str, phase: str) -> Path:
    return _job_dir(job_id) / f"{phase}.log"


def _job_control_paths(job_id: str) -> dict[str, str]:
    job_path = _job_dir(job_id)
    job_path.mkdir(parents=True, exist_ok=True)
    return {
        "job_dir": str(job_path),
        "struct_log_path": str(_job_log_path(job_id, "struct")),
        "semantic_log_path": str(_job_log_path(job_id, "semantic")),
    }


def _project_lock_path(project_id: str) -> Path:
    safe = str(project_id or "").strip() or "unknown"
    return _ensure_project_locks_dir() / f"{safe}.json"


def _load_project_lock(project_id: str) -> dict[str, Any] | None:
    path = _project_lock_path(project_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _job_runtime_fields(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": job.get("status"),
        "session_id": job.get("session_id"),
        "project_id": job.get("project_id"),
        "project_path": job.get("project_path"),
        "file_count": job.get("file_count"),
        "struct_rc": job.get("struct_rc"),
        "sem_rc": job.get("sem_rc"),
        "started_at": job.get("started_at"),
        "last_log_at": job.get("last_log_at"),
        "finished_at": job.get("finished_at"),
        "cancel_requested": job.get("cancel_requested"),
        "runtime_python": job.get("runtime_python"),
        "runtime_source": job.get("runtime_source"),
        "runtime_conda_env": job.get("runtime_conda_env"),
        "run_summary": job.get("run_summary"),
        "metrics": job.get("metrics"),
        "logs": list(job.get("logs") or []),
        "struct_pid": job.get("struct_pid"),
        "sem_pid": job.get("sem_pid"),
        "struct_log_path": job.get("struct_log_path"),
        "semantic_log_path": job.get("semantic_log_path"),
        "manifest_path": job.get("manifest_path"),
        "post_index_maintenance_done": job.get("post_index_maintenance_done"),
        "post_index_maintenance_pending": job.get("post_index_maintenance_pending"),
        "post_index_maintenance_error": job.get("post_index_maintenance_error"),
    }


def _persist_job_state(job_id: str) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        payload = _job_runtime_fields(job)
    state_path = _job_state_path(job_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_name(
        f"{state_path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}"
    )
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp_path.replace(state_path)


def _load_persisted_job(job_id: str) -> dict[str, Any] | None:
    state_path = _job_state_path(job_id)
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_job_record(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job:
            return dict(job)
    return _load_persisted_job(job_id)


def _job_processes_alive(job: dict[str, Any] | None) -> bool:
    if not isinstance(job, dict):
        return False
    for key in ("struct_pid", "sem_pid"):
        if _process_alive(job.get(key)):
            return True
    return False


def _release_project_job_lock(project_id: str, job_id: str) -> None:
    if not project_id or not job_id:
        return
    path = _project_lock_path(project_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = None
    if isinstance(payload, dict) and payload.get("job_id") != job_id:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def claim_project_job_lock(
    project_id: str,
    job_id: str,
    *,
    project_path: str,
) -> tuple[bool, dict[str, Any] | None]:
    path = _project_lock_path(project_id)
    payload = {
        "job_id": job_id,
        "project_id": project_id,
        "project_path": project_path,
        "created_at": time.time(),
        "pid": os.getpid(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")

    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            existing = _load_project_lock(project_id) or {}
            existing_job_id = str(existing.get("job_id") or "").strip()
            existing_job = load_job_record(existing_job_id) if existing_job_id else None
            if existing_job_id and existing_job:
                alive = _job_processes_alive(existing_job)
                status = str(existing_job.get("status") or "")
                if alive or status == "running":
                    return False, existing_job
            created_at = float(existing.get("created_at") or 0.0)
            if created_at and (time.time() - created_at) < _PROJECT_LOCK_STALE_S:
                return False, existing_job or existing
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                return False, existing_job
            continue
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return True, None


def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _infer_return_code_from_log(log_path: str | None, done_re: re.Pattern[str]) -> int | None:
    if not log_path or not os.path.exists(log_path):
        return None
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if done_re.search(text):
        return 0
    if "ERROR" in text or "Traceback" in text:
        return 1
    return None


def _tail_log_file(path: str | None, limit: int = 20) -> list[str]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return [line.rstrip() for line in fh.readlines()[-limit:]]
    except Exception:
        return []


def _render_job_logs(job: dict[str, Any]) -> list[str]:
    logs = list(job.get("logs") or [])
    struct_lines = _tail_log_file(job.get("struct_log_path"), 10)
    sem_lines = _tail_log_file(job.get("semantic_log_path"), 10)
    rendered = logs[-5:]
    rendered.extend(f"[struct] {line}" for line in struct_lines)
    rendered.extend(f"[semantic] {line}" for line in sem_lines)
    return rendered[-20:]


def _extract_job_metrics(logs: list[str]) -> dict[str, object]:
    metrics: dict[str, object] = {}
    for line in logs:
        struct_match = _STRUCT_DONE_RE.search(line)
        if struct_match:
            metrics["struct"] = {
                "files": int(struct_match.group("files")),
                "parse_s": float(struct_match.group("parse")),
                "nodes_s": float(struct_match.group("nodes")),
                "imports_s": float(struct_match.group("imports")),
                "rels_s": float(struct_match.group("rels")),
                "calls_s": float(struct_match.group("calls")),
                "total_s": float(struct_match.group("total")),
            }
            continue
        sem_match = _SEM_DONE_RE.search(line)
        if sem_match:
            metrics["semantic"] = {
                "new_chunks": int(sem_match.group("new")),
                "skipped_chunks": int(sem_match.group("skipped")),
                "files": int(sem_match.group("files")),
                "parsed_files": int(sem_match.group("parsed")),
                "skipped_files": int(sem_match.group("skipped_files")),
                "total_s": float(sem_match.group("total")),
            }
            continue
        gds_match = _GDS_OK_RE.search(line) or _GDS_SKIP_RE.search(line)
        if gds_match:
            gds = metrics.setdefault("gds", {})
            gds[gds_match.group("label")] = gds_match.group("detail")
    return metrics


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
            _JOBS[job_id]["last_log_at"] = time.time()
            if len(logs) > _MAX_LOG_LINES:
                del logs[0]
    _persist_job_state(job_id)


async def _post_index_maintenance_async(project_id: str, sem_rc: int | None) -> dict[str, Any] | None:
    import graph_bootstrap
    from neo4j import unit_of_work

    run_summary = None
    driver = await graph_bootstrap.require_driver()
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        tx_timeout = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
        metadata = {
            "source": "lm_proxy",
            "tool": "jobs",
            "op": "post_index_refresh",
        }

        @unit_of_work(timeout=tx_timeout, metadata=metadata)
        async def _tx(tx):
            set_bits = ["f.indexed_at = timestamp()"]
            if sem_rc == 0:
                set_bits.append("f.vector_indexed_at = timestamp()")

            cypher = f"MATCH (f:File {{project_id: $pid}}) SET {', '.join(set_bits)}"
            res = await tx.run(cypher, pid=project_id)
            await res.consume()

            if sem_rc == 0:
                promote_semantic = await tx.run(
                    """
                    MATCH (p:Project {id:$pid})
                    OPTIONAL MATCH (sr:IndexRun {project_id:$pid, phase:'struct'})
                    WHERE sr.status = 'done'
                    WITH p, sr
                    ORDER BY coalesce(sr.finished_at, sr.started_at, 0) DESC, sr.id DESC
                    WITH p, head(collect(sr.id)) AS struct_run_id
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

    return run_summary


def _run_coro_blocking(coro: Any, *, job_id: str, timeout: int = 180) -> Any:
    import asyncio as _asyncio
    import threading

    current_loop = None
    try:
        current_loop = _asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _MAIN_LOOP is not None and _MAIN_LOOP.is_running() and _MAIN_LOOP is not current_loop:
        future = _asyncio.run_coroutine_threadsafe(coro, _MAIN_LOOP)
        return future.result(timeout=timeout)

    if current_loop is not None:
        outcome: dict[str, object] = {}

        def _worker() -> None:
            try:
                outcome["result"] = _asyncio.run(coro)
            except Exception as exc:
                outcome["error"] = exc

        thread = threading.Thread(
            target=_worker,
            name=f"post-index-maintenance-{job_id}",
            daemon=True,
        )
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise TimeoutError("post-index maintenance timed out")
        if outcome.get("error") is not None:
            raise outcome["error"]  # type: ignore[misc]
        return outcome.get("result")

    return _asyncio.run(coro)


def _complete_post_index_maintenance(
    job_id: str,
    *,
    run_summary: dict[str, Any] | None = None,
    graph_build_error: str | None = None,
) -> None:
    queued = "timestamps refreshed" if not graph_build_error else f"failed: {graph_build_error}"
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].pop("clone_enrich_status", None)
            _JOBS[job_id].pop("clone_enrich_msg", None)
            _JOBS[job_id].pop("post_index_maintenance_pending", None)
            if _JOBS[job_id].get("logs"):
                _JOBS[job_id]["logs"] = [
                    line for line in _JOBS[job_id]["logs"] if "[clone-enrich]" not in line
                ]
            if run_summary:
                _JOBS[job_id]["run_summary"] = run_summary
            _JOBS[job_id]["metrics"] = _extract_job_metrics(_JOBS[job_id].get("logs", []))
            _JOBS[job_id]["logs"].append(f"[struct-index] {queued}")
            _JOBS[job_id]["post_index_maintenance_done"] = time.time()
            if graph_build_error:
                _JOBS[job_id]["post_index_maintenance_error"] = graph_build_error
            else:
                _JOBS[job_id].pop("post_index_maintenance_error", None)
    _persist_job_state(job_id)


def _defer_post_index_maintenance(job_id: str) -> None:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].pop("post_index_maintenance_pending", None)
            _JOBS[job_id].pop("post_index_maintenance_error", None)
    _persist_job_state(job_id)


def _run_post_index_maintenance(job_id: str) -> None:
    import asyncio as _asyncio

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        pending_started_at = job.get("post_index_maintenance_pending")
        if job.get("post_index_maintenance_done"):
            return
        if pending_started_at and (time.time() - float(pending_started_at)) < 30:
            return
        if pending_started_at:
            job.pop("post_index_maintenance_pending", None)
        struct_rc = job.get("struct_rc")
        sem_rc = job.get("sem_rc")
        project_path = job.get("project_path", "")
        project_id = job.get("project_id", "")
        cancel_requested = bool(job.get("cancel_requested"))
    _persist_job_state(job_id)

    if (
        struct_rc != 0
        or not project_path
        or not project_id
        or project_path.startswith("docs://")
        or cancel_requested
    ):
        with _JOBS_LOCK:
            if job_id in _JOBS and not _JOBS[job_id].get("post_index_maintenance_done"):
                _JOBS[job_id].pop("post_index_maintenance_pending", None)
                _JOBS[job_id]["post_index_maintenance_done"] = time.time()
                _JOBS[job_id].pop("post_index_maintenance_error", None)
        _persist_job_state(job_id)
        return

    try:
        current_loop = _asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is not None and _MAIN_LOOP is current_loop:
        with _JOBS_LOCK:
            if job_id in _JOBS:
                _JOBS[job_id]["post_index_maintenance_pending"] = time.time()
        _persist_job_state(job_id)

        task = current_loop.create_task(_post_index_maintenance_async(project_id, sem_rc))

        def _on_done(fut: _asyncio.Future) -> None:
            try:
                run_summary = fut.result()
                graph_build_error = None
            except _asyncio.CancelledError:
                _defer_post_index_maintenance(job_id)
                return
            except Exception as exc:  # pragma: no cover - callback path depends on loop scheduling
                run_summary = None
                graph_build_error = str(exc)
            _complete_post_index_maintenance(
                job_id,
                run_summary=run_summary,
                graph_build_error=graph_build_error,
            )

        task.add_done_callback(_on_done)
        return

    graph_build_error = None
    run_summary = None
    try:
        run_summary = _run_coro_blocking(
            _post_index_maintenance_async(project_id, sem_rc),
            job_id=job_id,
            timeout=180,
        )
    except Exception as exc:
        graph_build_error = str(exc)
    _complete_post_index_maintenance(
        job_id,
        run_summary=run_summary,
        graph_build_error=graph_build_error,
    )


def _reconcile_job_process_state(job_id: str) -> dict[str, Any] | None:
    """
    Refresh cached return codes from child Popen objects.

    The normal path is that `_drain_proc_output` waits on each child and writes
    `struct_rc` / `sem_rc`. This helper is a defensive fallback for status reads
    so a dead child cannot leave a job stuck in RUNNING forever if the drain
    thread or parent bookkeeping stops short.
    """
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            persisted = _load_persisted_job(job_id)
            if not persisted:
                return None
            _JOBS[job_id] = persisted
            job = _JOBS[job_id]

        for proc_key, rc_key, pid_key, done_re, log_key in (
            ("struct_proc", "struct_rc", "struct_pid", _STRUCT_DONE_RE, "struct_log_path"),
            ("sem_proc", "sem_rc", "sem_pid", _SEM_DONE_RE, "semantic_log_path"),
        ):
            if job.get(rc_key) is not None:
                continue
            proc = job.get(proc_key)
            polled = None
            if proc is not None:
                try:
                    polled = proc.poll()
                except Exception:
                    polled = None
            if polled is not None:
                job[rc_key] = polled
                continue
            pid = job.get(pid_key)
            if pid and not _process_alive(pid):
                inferred = _infer_return_code_from_log(job.get(log_key), done_re)
                job[rc_key] = inferred if inferred is not None else 1

        both_finished = job.get("struct_rc") is not None and job.get("sem_rc") is not None
        if both_finished and job.get("finished_at") is None:
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
            else:
                ok = job.get("struct_rc") == 0 and job.get("sem_rc") == 0
                job["status"] = "done" if ok else "failed"
            job["finished_at"] = time.time()
            manifest_path = job.get("manifest_path")
            if manifest_path and os.path.exists(manifest_path):
                try:
                    os.remove(manifest_path)
                except OSError:
                    pass
            _release_project_job_lock(
                str(job.get("project_id") or ""),
                job_id,
            )
        should_run_post_index_maintenance = bool(
            both_finished and not job.get("post_index_maintenance_done")
        )
        persisted = dict(job)
    _persist_job_state(job_id)
    if should_run_post_index_maintenance:
        _run_post_index_maintenance(job_id)
        with _JOBS_LOCK:
            persisted = dict(_JOBS.get(job_id) or persisted)
    return persisted


def _drain_proc_output(proc, job_id: str, prefix: str, rc_key: str) -> None:
    """Backward-compatible watcher for callers still using pipe-based subprocesses."""
    try:
        proc.wait()
    finally:
        with _JOBS_LOCK:
            if job_id in _JOBS:
                _JOBS[job_id][rc_key] = proc.returncode
        _persist_job_state(job_id)


def _finalize_job(job_id: str, manifest_path: str) -> None:
    """Watch for both phases to complete, then set status and clean up."""
    import time as _time

    while True:
        _time.sleep(0.5)
        job = _reconcile_job_process_state(job_id) or {}
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
    _release_project_job_lock(project_id, job_id)
    _persist_job_state(job_id)

    _run_post_index_maintenance(job_id)
    graph_build_error = None
    with _JOBS_LOCK:
        if job_id in _JOBS:
            graph_build_error = _JOBS[job_id].get("post_index_maintenance_error")

    with _JOBS_LOCK:
        if job_id in _JOBS and _JOBS[job_id].get("finished_at") is None:
            if cancel_requested:
                _JOBS[job_id]["status"] = "cancelled"
            elif graph_build_error:
                _JOBS[job_id]["status"] = "failed"
            else:
                _JOBS[job_id]["status"] = "done" if (struct_rc == 0 and sem_rc == 0) else "failed"
            _JOBS[job_id]["finished_at"] = _t.time()
    _persist_job_state(job_id)

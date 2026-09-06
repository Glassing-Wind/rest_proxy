"""Synchronous operator wrapper for the asynchronous workspace index tool."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Callable


_JOB_ID_RE = re.compile(r"job_id:\s*([A-Za-z0-9_-]+)")
_TERMINAL_STATUSES = {"done", "failed", "cancelled"}


def _post_index_maintenance_complete(job: dict | None) -> bool:
    if not isinstance(job, dict) or "struct_rc" not in job:
        return True
    if job.get("struct_rc") != 0 or job.get("sem_rc") != 0:
        return True
    if str(job.get("project_path") or "").startswith("docs://"):
        return True
    return bool(
        job.get("post_index_maintenance_done")
        or job.get("post_index_maintenance_error")
    )


async def run_index_workspace_cli(
    project_path: str,
    *,
    emit: Callable[[str], None] = print,
    poll_interval: float = 0.5,
    timeout_seconds: float | None = None,
) -> int:
    """Run indexing and wait until persisted job state reaches a terminal status."""
    if not os.path.isabs(project_path):
        emit("index_workspace CLI requires an absolute project path")
        return 1

    from _jobs import load_job_record
    from tools.hands.indexing import get_index_status, index_workspace

    started = await index_workspace(project_path)
    emit(started)
    if "Another indexing job is already running." in started:
        return 1
    match = _JOB_ID_RE.search(started)
    if not match or match.group(1).lower() == "unknown":
        return 1

    job_id = match.group(1)
    if timeout_seconds is None:
        try:
            timeout_seconds = float(os.getenv("LM_PROXY_INDEX_CLI_TIMEOUT", "21600"))
        except ValueError:
            emit("LM_PROXY_INDEX_CLI_TIMEOUT must be a number of seconds")
            return 1
    deadline = time.monotonic() + max(1.0, timeout_seconds)

    while True:
        job = load_job_record(job_id)
        status = str((job or {}).get("status") or "").lower()
        if status in _TERMINAL_STATUSES and _post_index_maintenance_complete(job):
            emit(await get_index_status(job_id))
            return (
                0
                if status == "done"
                and not (job or {}).get("post_index_maintenance_error")
                else 1
            )
        if time.monotonic() >= deadline:
            emit(
                f"Indexing CLI wait timed out for job {job_id}. "
                f"The background job may still be running; use get_index_status('{job_id}')."
            )
            return 1
        await asyncio.sleep(max(0.01, poll_interval))

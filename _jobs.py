"""
Background index-job registry.

Shared between tools/indexing.py and tools/documentation.py.
Each entry: {status, struct_rc, sem_rc, logs[], started_at, finished_at}
"""
import os
import sys
import threading
from typing import Dict, Any

_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_MAX_LOG_LINES = 200  # ring-buffer size per job


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
            sem_rc    = job.get("sem_rc")
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
            ok = (struct_rc == 0 and sem_rc == 0)
            _JOBS[job_id]["status"]      = "done" if ok else "failed"
            _JOBS[job_id]["finished_at"] = _t.time()

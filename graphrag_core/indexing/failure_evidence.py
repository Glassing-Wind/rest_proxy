"""Best-effort local failure evidence without credential or exception-text logging."""

import json
import os
from pathlib import Path
import socket
import time


def record_index_failure(project_id: str, run_id: str, phase: str, exc: BaseException,
                         root: Path | None = None) -> Path | None:
    """Preserve bounded, redacted diagnostics even when graph status writes fail."""
    try:
        folder = root or Path(__file__).resolve().parents[2] / ".runtime/index-failures"
        folder = folder.resolve()
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        text = str(exc).lower()
        category = "other"
        if "authenticationratelimit" in text or "incorrect authentication details too many" in text:
            category = "authentication_rate_limit"
        elif "unauthorized" in text or "authentication" in text:
            category = "authentication_failure"
        payload = {"project_id": project_id, "run_id": run_id, "phase": phase,
                   "recorded_at": time.time(), "host": socket.gethostname(), "pid": os.getpid(),
                   "client": "rest-proxy/struct-wrapper", "error_class": type(exc).__name__,
                   "category": category}
        path = folder / f"{os.getpid()}-{time.time_ns()}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(payload, file, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        return path
    except Exception:
        return None

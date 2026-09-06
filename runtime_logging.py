"""Low-level structured debug logging without application-layer imports."""

import json
import os
import sys
from pathlib import Path
from typing import Any


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _debug_enabled() -> bool:
    return os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _debug_log_path() -> Path:
    configured = os.getenv("LM_PROXY_DEBUG_LOG")
    if configured:
        return Path(configured)
    local_runtime = Path(".runtime")
    try:
        local_runtime.mkdir(parents=True, exist_ok=True)
        test_file = local_runtime / ".write_test"
        test_file.touch()
        test_file.unlink()
        return local_runtime / "proxy_debug.log"
    except (OSError, PermissionError):
        tmp_runtime = Path("/tmp/lm-proxy/.runtime")
        try:
            tmp_runtime.mkdir(parents=True, exist_ok=True)
            return tmp_runtime / "proxy_debug.log"
        except (OSError, PermissionError):
            return Path("/tmp/proxy_debug.log")


def debug_log(message: str, **fields: Any) -> None:
    if not _debug_enabled():
        return

    payload = {"message": message}
    payload.update(fields)
    try:
        log_str = f"[lm-proxy] {stable_json(payload)}"
        print(log_str, file=sys.stderr, flush=True)
        try:
            path = _debug_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(log_str + "\n")
        except (OSError, PermissionError):
            pass
    except Exception as exc:
        print(
            f"[lm-proxy] {message} {fields} - Exception: {exc}",
            file=sys.stderr,
            flush=True,
        )

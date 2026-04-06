"""proxy_logging.py — debug logging helpers."""

import json
import sys
from typing import Any

from proxy.config import ENABLE_DEBUG_LOGGING, DEBUG_LOG_PATH


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def debug_log(message: str, **fields: Any) -> None:
    if not ENABLE_DEBUG_LOGGING:
        return

    payload = {"message": message}
    payload.update(fields)
    try:
        log_str = f"[lm-proxy] {stable_json(payload)}"
        print(log_str, file=sys.stderr, flush=True)

        # Only attempt to write to file if DEBUG_LOG_PATH is set and writable
        if DEBUG_LOG_PATH:
            try:
                DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(DEBUG_LOG_PATH, "a") as f:
                    f.write(log_str + "\n")
            except (OSError, PermissionError):
                # Fallback: don't crash if filesystem is read-only
                pass
    except Exception as e:
        log_str = f"[lm-proxy] {message} {fields} - Exception: {e}"
        print(log_str, file=sys.stderr, flush=True)

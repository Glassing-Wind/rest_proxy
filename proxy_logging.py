"""proxy_logging.py — debug logging helpers."""

import json
import sys
from typing import Any

from proxy_config import ENABLE_DEBUG_LOGGING


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
        with open("proxy_debug.log", "a") as f:
            f.write(log_str + "\n")
    except Exception as e:
        log_str = f"[lm-proxy] {message} {fields} - Exception: {e}"
        print(log_str, file=sys.stderr, flush=True)
        with open("proxy_debug.log", "a") as f:
            f.write(log_str + "\n")

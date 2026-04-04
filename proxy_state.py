"""proxy_state.py — persistence for response IDs."""

import json
from typing import Dict

from proxy_config import STATE_FILE
from proxy_logging import debug_log

# history-hash -> LM Studio response id
STATE: Dict[str, str] = {}


def load_state() -> None:
    global STATE
    if STATE_FILE.exists():
        try:
            STATE = json.loads(STATE_FILE.read_text())
            debug_log("state_loaded", entries=len(STATE), state_file=str(STATE_FILE))
        except Exception:
            STATE = {}
            debug_log("state_load_failed", state_file=str(STATE_FILE))


def save_state() -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE, indent=2, sort_keys=True))
    tmp.replace(STATE_FILE)
    debug_log("state_saved", entries=len(STATE), state_file=str(STATE_FILE))

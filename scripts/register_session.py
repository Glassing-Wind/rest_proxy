#!/usr/bin/env python3
import os
import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graphrag_core.app_state import (  # noqa: E402
    get_active_sessions_path,
    get_legacy_sessions_registry_path,
    get_sessions_registry_path,
)


def _is_entry_alive(key: str) -> bool:
    """Return True if this sessions.json entry should be kept.

    - Numeric keys are PIDs — kept only if the process is still running.
    - Path keys (IPC socket paths) — kept only if the file exists on disk.
    - Anything else is kept (unknown format, don't discard).
    """
    if key.isdigit():
        try:
            os.kill(int(key), 0)  # signal 0 = existence check, no actual signal
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists but we can't signal it
    if key.startswith("/"):
        return os.path.exists(key)
    return True


def register_session(workspace_path: str):
    """Register the current IDE session's workspace path and prune stale entries."""
    v_pid = os.getenv("VSCODE_PID")
    if not v_pid:
        print("Error: No VSCODE_PID found.")
        sys.exit(1)

    sessions_file = get_sessions_registry_path()
    sessions_file.parent.mkdir(parents=True, exist_ok=True)

    sessions = {}
    source_file = sessions_file
    if not sessions_file.exists() and get_legacy_sessions_registry_path().exists():
        source_file = get_legacy_sessions_registry_path()
    if source_file.exists():
        try:
            with source_file.open("r", encoding="utf-8") as f:
                sessions = json.load(f)
        except Exception:
            pass

    # Prune stale entries before adding new ones.
    before = len(sessions)
    sessions = {k: v for k, v in sessions.items() if _is_entry_alive(k)}
    pruned = before - len(sessions)

    # Register current session (PID and IPC hook).
    abs_path = os.path.abspath(workspace_path)
    if v_pid:
        sessions[v_pid] = abs_path

    v_ipc = os.getenv("VSCODE_IPC_HOOK")
    if v_ipc:
        sessions[v_ipc] = abs_path

    with sessions_file.open("w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2)

    active_sessions_file = get_active_sessions_path()
    active_sessions_file.parent.mkdir(parents=True, exist_ok=True)
    active_sessions = {}
    if active_sessions_file.exists():
        try:
            with active_sessions_file.open("r", encoding="utf-8") as f:
                active_sessions = json.load(f)
        except Exception:
            active_sessions = {}

    session_key = v_ipc or f"pid:{v_pid}"
    active_sessions[session_key] = {
        "workspace_path": abs_path,
        "updated_at": time.time(),
        "reason": "register_session",
        "client_id": os.getenv("MCP_ID", "").strip() or "default",
    }
    with active_sessions_file.open("w", encoding="utf-8") as f:
        json.dump(active_sessions, f, indent=2)

    print(f"Registered session: {v_pid} / {v_ipc} -> {workspace_path}"
          + (f" (pruned {pruned} stale entries)" if pruned else ""))
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python register_session.py <workspace_path>")
        sys.exit(1)

    register_session(sys.argv[1])

#!/usr/bin/env python3
import os
import sys
import json
from pathlib import Path


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

    sessions_dir = os.path.expanduser("~/.gemini/antigravity")
    os.makedirs(sessions_dir, exist_ok=True)
    sessions_file = os.path.join(sessions_dir, "sessions.json")

    sessions = {}
    if os.path.exists(sessions_file):
        try:
            with open(sessions_file, "r") as f:
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

    with open(sessions_file, "w") as f:
        json.dump(sessions, f, indent=2)

    print(f"Registered session: {v_pid} / {v_ipc} -> {workspace_path}"
          + (f" (pruned {pruned} stale entries)" if pruned else ""))
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 register_session.py <workspace_path>")
        sys.exit(1)

    register_session(sys.argv[1])


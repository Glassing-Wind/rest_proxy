#!/usr/bin/env python3
"""
graphrag_mcp_supervisor.py — resilient MCP child-process supervisor.

One instance per MCP_ID (e.g. "antigravity", "codex") is enforced via an
exclusive fcntl lock on a per-ID PID file.  If the IDE restarts and spawns a
second copy while the first is still alive, the duplicate exits immediately so
the original stdio pipe stays connected.

Hot-reload: send SIGUSR1 to kill+respawn the child mcp_server.py.
Shutdown:   send SIGTERM/SIGINT.
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graphrag_core.app_state import (  # noqa: E402
    get_legacy_sessions_registry_path,
    get_sessions_registry_path,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RUNTIME_DIR     = ROOT / ".runtime"
SESSIONS_FILE   = get_sessions_registry_path()
LEGACY_SESSIONS_FILE = get_legacy_sessions_registry_path()

os.makedirs(RUNTIME_DIR, exist_ok=True)

# MCP_ID is set by the IDE config (e.g. env = { MCP_ID = "codex" }).
# Falls back to "default" so a bare invocation still works.
MCP_ID = os.environ.get("MCP_ID", "default")

SUPERVISOR_PID_FILE = RUNTIME_DIR / f"graphrag_mcp_supervisor_{MCP_ID}.pid"
CHILD_PID_FILE      = RUNTIME_DIR / f"mcp_server_{MCP_ID}.pid"

# ---------------------------------------------------------------------------
# Debug logging (to /tmp, never to stdout which belongs to the JSON-RPC pipe)
# ---------------------------------------------------------------------------
DEBUG_LOG = f"/tmp/graphrag_mcp_supervisor_{MCP_ID}.log"

def _log(msg: str) -> None:
    with open(DEBUG_LOG, "a") as f:
        f.write(f"[{time.ctime()}] [pid={os.getpid()}] {msg}\n")


# ---------------------------------------------------------------------------
# Singleton enforcement — exclusive lock on the PID file
# ---------------------------------------------------------------------------
_LOCK_FH = None  # keep reference so GC doesn't close it

def _acquire_singleton_lock() -> bool:
    """
    Try to acquire an exclusive lock on SUPERVISOR_PID_FILE.
    Returns True if we are now the sole owner, False if another live instance
    already holds the lock (caller should exit).
    """
    global _LOCK_FH
    fh = open(SUPERVISOR_PID_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        _log("Singleton lock held by another process — exiting duplicate.")
        return False
    # We won the lock — write our PID and keep the file handle open.
    fh.write(str(os.getpid()))
    fh.flush()
    _LOCK_FH = fh
    _log(f"Singleton lock acquired (MCP_ID={MCP_ID}).")
    return True


def _release_singleton_lock() -> None:
    global _LOCK_FH
    if _LOCK_FH is not None:
        try:
            fcntl.flock(_LOCK_FH, fcntl.LOCK_UN)
            _LOCK_FH.close()
        except OSError:
            pass
        _LOCK_FH = None
    try:
        SUPERVISOR_PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Workspace discovery
# ---------------------------------------------------------------------------
def discover_workspace() -> str:
    """Return the CWD that mcp_server.py should run in."""
    # If the IDE config sets LM_PROXY_WORKSPACE directly, use it.
    explicit = os.environ.get("LM_PROXY_WORKSPACE")
    if explicit and os.path.exists(explicit):
        _log(f"Discovery: LM_PROXY_WORKSPACE override → {explicit}")
        return explicit

    ipc_hook = os.environ.get("VSCODE_IPC_HOOK")
    if not ipc_hook:
        _log("Discovery: No VSCODE_IPC_HOOK — using ROOT.")
        return str(ROOT)

    for candidate in (SESSIONS_FILE, LEGACY_SESSIONS_FILE):
        if not candidate.exists():
            continue
        try:
            with open(candidate) as f:
                sessions = json.load(f)
            if ipc_hook in sessions:
                entry = sessions[ipc_hook]
                # Support both flat string format {"hook": "/path"} and
                # dict format {"hook": {"workspace_path": "/path"}}.
                ws_path = entry if isinstance(entry, str) else entry.get("workspace_path", "")
                if ws_path and os.path.exists(ws_path):
                    _log(f"Discovery: sessions.json match {ipc_hook} → {ws_path}")
                    return ws_path
        except Exception as e:
            _log(f"Discovery: error reading sessions.json: {e}")


    _log("Discovery: no match for IPC hook — using ROOT.")
    return str(ROOT)


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------
RESTART_REQUESTED  = False
SHUTDOWN_REQUESTED = False

def _handle_signal(signum, _frame) -> None:
    global RESTART_REQUESTED, SHUTDOWN_REQUESTED
    if signum == signal.SIGUSR1:
        RESTART_REQUESTED = True
        _log("SIGUSR1 received — restart requested.")
    else:
        SHUTDOWN_REQUESTED = True
        _log(f"Signal {signum} received — shutdown requested.")


# ---------------------------------------------------------------------------
# Child process management
# ---------------------------------------------------------------------------
def _start_child(cwd: str) -> subprocess.Popen:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "mcp_server.py")],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=env,
        cwd=cwd,
    )
    _log(f"Started child mcp_server.py pid={proc.pid} cwd={cwd}")
    return proc


def _write_child_pid(pid: int) -> None:
    try:
        CHILD_PID_FILE.write_text(str(pid))
    except OSError as e:
        _log(f"Could not write child PID file: {e}")


def _remove_child_pid() -> None:
    try:
        CHILD_PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    global RESTART_REQUESTED, SHUTDOWN_REQUESTED

    # --- Singleton guard ---
    if not _acquire_singleton_lock():
        # Another live supervisor owns this MCP_ID. Exit silently so the IDE's
        # existing stdio pipe (and therefore its MCP session) is undisturbed.
        sys.exit(0)

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGUSR1, _handle_signal)

    cwd = discover_workspace()
    _log(f"Supervisor loop starting. MCP_ID={MCP_ID} cwd={cwd}")

    child = None
    try:
        while not SHUTDOWN_REQUESTED:
            RESTART_REQUESTED = False

            child = _start_child(cwd)
            _write_child_pid(child.pid)

            # Wait for child exit or incoming signal
            while child.poll() is None:
                if RESTART_REQUESTED or SHUTDOWN_REQUESTED:
                    break
                time.sleep(0.5)

            if SHUTDOWN_REQUESTED:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                break

            if RESTART_REQUESTED:
                if child.poll() is None:
                    child.terminate()
                    child.wait()
                _log("Child terminated for hot-reload — respawning.")
            else:
                # Unexpected exit — throttle tight restart loops
                exit_code = child.returncode
                _log(f"Child exited unexpectedly (code={exit_code}) — respawning in 2s.")
                if exit_code != 0:
                    time.sleep(2)

    finally:
        _remove_child_pid()
        _release_singleton_lock()
        _log("Supervisor exiting.")


if __name__ == "__main__":
    main()

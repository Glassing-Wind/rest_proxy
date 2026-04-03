#!/usr/bin/env python3
"""Supervisor for the GraphRAG MCP server.

Launch this script from the Codex app instead of `mcp_server.py` directly.
It starts `mcp_server.py` with the current interpreter, records a PID file,
and supports in-place child restarts via SIGUSR1.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".runtime"
PID_FILE = RUNTIME_DIR / "graphrag_mcp_supervisor.pid"

RESTART_REQUESTED = False
SHUTDOWN_REQUESTED = False


def _write_pid() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _clear_pid() -> None:
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except OSError:
        pass


def _handle_signal(signum, _frame) -> None:
    global RESTART_REQUESTED, SHUTDOWN_REQUESTED
    if signum == signal.SIGUSR1:
        RESTART_REQUESTED = True
    else:
        SHUTDOWN_REQUESTED = True


def _start_child() -> subprocess.Popen:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    return subprocess.Popen(
        [sys.executable, str(ROOT / "mcp_server.py")],
        cwd=str(ROOT),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=env,
    )


def _stop_child(child: subprocess.Popen, sig: int = signal.SIGTERM) -> None:
    if child.poll() is not None:
        return
    child.send_signal(sig)
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def main() -> int:
    global RESTART_REQUESTED, SHUTDOWN_REQUESTED
    signal.signal(signal.SIGUSR1, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _write_pid()
    child = _start_child()

    try:
        while True:
            exit_code = child.poll()

            if SHUTDOWN_REQUESTED:
                _stop_child(child)
                return 0

            if RESTART_REQUESTED:
                RESTART_REQUESTED = False
                _stop_child(child)
                time.sleep(0.2)
                child = _start_child()
                continue

            if exit_code is not None:
                return exit_code

            time.sleep(0.2)
    finally:
        if child.poll() is None:
            _stop_child(child)
        _clear_pid()


if __name__ == "__main__":
    raise SystemExit(main())

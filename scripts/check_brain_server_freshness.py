#!/usr/bin/env python3
"""Verify that the running brain_server matches the local registered tool set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _request_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _local_tool_fingerprint() -> tuple[str, int]:
    from _mcp import get_mcp

    mcp = get_mcp()
    tool_names = sorted(t.name for t in mcp._tool_manager.list_tools())
    fingerprint = hashlib.sha256(json.dumps(tool_names).encode()).hexdigest()[:12]
    return fingerprint, len(tool_names)


def _brain_server_url(path: str) -> str:
    port = os.environ.get("BRAIN_SERVER_PORT", "8001")
    base = os.environ.get("BRAIN_SERVER_BASE_URL", f"http://127.0.0.1:{port}")
    return f"{base}{path}"


def _server_fingerprint() -> dict | None:
    try:
        return _request_json(_brain_server_url("/fingerprint"))
    except Exception:
        return None


def _run_script(name: str) -> None:
    script = ROOT / "scripts" / name
    subprocess.run([str(script)], cwd=ROOT, check=True)


def _wait_for_match(expected_fingerprint: str, timeout_s: float = 60.0) -> dict | None:
    deadline = time.time() + timeout_s
    last_payload = None
    while time.time() < deadline:
        payload = _server_fingerprint()
        if payload:
            last_payload = payload
            if payload.get("fingerprint") == expected_fingerprint:
                return payload
        time.sleep(0.5)
    return last_payload


def _print_status(message: str, *, quiet: bool) -> None:
    if not quiet:
        print(message)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether the running brain_server fingerprint matches the local repo tools."
    )
    parser.add_argument(
        "--restart-if-stale",
        action="store_true",
        help="Restart the daemon if it is missing or advertises a stale fingerprint.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress normal status output; only print failures.",
    )
    args = parser.parse_args()

    expected_fingerprint, expected_tools = _local_tool_fingerprint()
    payload = _server_fingerprint()

    if payload and payload.get("fingerprint") == expected_fingerprint:
        _print_status(
            f"brain_server fresh boot={payload.get('boot_id')} fp={payload.get('fingerprint')} tools={payload.get('tools')}",
            quiet=args.quiet,
        )
        return 0

    if not args.restart_if_stale:
        if payload is None:
            print("brain_server freshness check failed: server is not reachable", file=sys.stderr)
        else:
            print(
                "brain_server freshness check failed: "
                f"expected fp={expected_fingerprint} tools={expected_tools}, "
                f"got fp={payload.get('fingerprint')} tools={payload.get('tools')} boot={payload.get('boot_id')}",
                file=sys.stderr,
            )
        return 1

    if payload is None:
        _print_status("brain_server not reachable; starting daemon", quiet=args.quiet)
        _run_script("start_brain_server_daemon.sh")
    else:
        _print_status(
            "brain_server stale; restarting daemon "
            f"(expected fp={expected_fingerprint}, got fp={payload.get('fingerprint')})",
            quiet=args.quiet,
        )
        _run_script("restart_brain_server.sh")

    refreshed = _wait_for_match(expected_fingerprint)
    if refreshed and refreshed.get("fingerprint") == expected_fingerprint:
        _print_status(
            f"brain_server fresh boot={refreshed.get('boot_id')} fp={refreshed.get('fingerprint')} tools={refreshed.get('tools')}",
            quiet=args.quiet,
        )
        return 0

    got = refreshed.get("fingerprint") if refreshed else "<unreachable>"
    boot = refreshed.get("boot_id") if refreshed else "<none>"
    print(
        "brain_server freshness check failed after restart: "
        f"expected fp={expected_fingerprint}, got fp={got}, boot={boot}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

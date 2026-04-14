#!/usr/bin/env python3
"""Run retrieval duplicate eval plus live graph goldens in one place."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_duplicate_eval():
    module_path = ROOT / "tools" / "brain" / "search" / "duplicate_eval.py"
    spec = importlib.util.spec_from_file_location("enterprise_duplicate_eval", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_retrieval_eval() -> dict:
    duplicate_eval = _load_duplicate_eval()
    return duplicate_eval.evaluate_benchmarks()


def run_live_graph_goldens(workspaces: list[str], python_bin: str) -> dict:
    if not workspaces:
        return {"skipped": True, "reason": "no_workspaces"}
    cmd = [python_bin, str(ROOT / "test_live_graph_tools.py"), *workspaces]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "workspaces": workspaces,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        action="append",
        dest="workspaces",
        default=[],
        help="Workspace path to include in live graph goldens. Repeatable.",
    )
    parser.add_argument(
        "--python-bin",
        default=sys.executable,
        help="Python executable for running live graph tests.",
    )
    parser.add_argument(
        "--skip-graph",
        action="store_true",
        help="Skip live graph goldens and run only retrieval eval.",
    )
    args = parser.parse_args()

    retrieval = run_retrieval_eval()
    graph = (
        {"skipped": True, "reason": "skip_graph"}
        if args.skip_graph
        else run_live_graph_goldens(args.workspaces, args.python_bin)
    )
    payload = {
        "retrieval_eval": retrieval,
        "live_graph_goldens": graph,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))

    retrieval_ok = True
    graph_ok = bool(graph.get("ok", False)) if not graph.get("skipped") else True
    return 0 if retrieval_ok and graph_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

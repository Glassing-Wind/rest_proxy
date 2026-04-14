#!/usr/bin/env python3
"""Run retrieval duplicate eval plus live graph goldens in one place."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = ROOT / ".runtime" / "enterprise_eval"


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


def _best_retrieval_config(summary: dict) -> dict:
    best_name = ""
    best_metrics: dict = {}
    best_mrr = float("-inf")
    for name, metrics in (summary or {}).items():
        try:
            mrr = float((metrics or {}).get("mrr", 0.0))
        except (TypeError, ValueError):
            mrr = 0.0
        if mrr > best_mrr:
            best_mrr = mrr
            best_name = name
            best_metrics = metrics or {}
    return {
        "name": best_name,
        "metrics": best_metrics,
    }


def build_enterprise_summary(payload: dict) -> dict:
    retrieval = payload.get("retrieval_eval") or {}
    live_graph = payload.get("live_graph_goldens") or {}
    retrieval_summary = retrieval.get("summary") or {}
    best = _best_retrieval_config(retrieval_summary)

    regressions: list[dict] = []
    for case in retrieval.get("cases") or []:
        case_id = case.get("id")
        for config_name, metrics in (case.get("configs") or {}).items():
            alerts = list((metrics or {}).get("promotion_alerts") or [])
            if alerts:
                regressions.append(
                    {
                        "case_id": case_id,
                        "config": config_name,
                        "alerts": alerts,
                    }
                )

    return {
        "live_graph_ok": bool(live_graph.get("ok", False)) if not live_graph.get("skipped") else True,
        "live_graph_skipped": bool(live_graph.get("skipped", False)),
        "live_graph_workspaces": list(live_graph.get("workspaces") or []),
        "best_retrieval_config": best,
        "retrieval_query_class_counts": retrieval.get("query_class_counts") or {},
        "retrieval_alerts": retrieval.get("alerts") or {},
        "retrieval_regressions": regressions,
    }


def _artifact_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_enterprise_artifacts(payload: dict, artifact_dir: str | Path) -> dict:
    target_dir = Path(artifact_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _artifact_timestamp()
    payload_with_meta = dict(payload)
    payload_with_meta["artifact_meta"] = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "artifact_dir": str(target_dir),
        "timestamp": timestamp,
    }

    latest_path = target_dir / "latest.json"
    history_path = target_dir / f"{timestamp}.json"
    latest_path.write_text(json.dumps(payload_with_meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    history_path.write_text(json.dumps(payload_with_meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "artifact_dir": str(target_dir),
        "latest_path": str(latest_path),
        "history_path": str(history_path),
        "timestamp": timestamp,
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
    parser.add_argument(
        "--artifact-dir",
        default=str(DEFAULT_ARTIFACT_DIR),
        help="Directory to write latest and historical enterprise eval JSON artifacts.",
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
    payload["enterprise_summary"] = build_enterprise_summary(payload)
    payload["artifacts"] = write_enterprise_artifacts(payload, args.artifact_dir)
    print(json.dumps(payload, indent=2, sort_keys=True))

    retrieval_ok = True
    graph_ok = bool(graph.get("ok", False)) if not graph.get("skipped") else True
    return 0 if retrieval_ok and graph_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

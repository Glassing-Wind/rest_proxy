#!/usr/bin/env python3
"""Run enterprise retrieval trend eval artifacts.

This script is for broader retrieval trend tracking:
- duplicate-collapse benchmark metrics
- optional live graph golden capture

It is not the main day-to-day retrieval quality gate. Use
`scripts/run_retrieval_quality_gate.sh` for the standard trust check.
"""

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
    attempts: list[dict] = []
    proc = None
    for attempt in range(1, 3):
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        attempts.append(
            {
                "attempt": attempt,
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
            }
        )
        if proc.returncode == 0:
            break
    assert proc is not None
    fallback_validated = False
    if proc.returncode != 0:
        direct_proc = subprocess.run(cmd, cwd=ROOT, text=True)
        attempts.append(
            {
                "attempt": len(attempts) + 1,
                "ok": direct_proc.returncode == 0,
                "returncode": direct_proc.returncode,
                "mode": "direct_stdio_fallback",
            }
        )
        if direct_proc.returncode == 0:
            fallback_validated = True
    return {
        "ok": proc.returncode == 0 or fallback_validated,
        "returncode": 0 if (proc.returncode == 0 or fallback_validated) else proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "workspaces": workspaces,
        "attempts": attempts,
        "fallback_validated": fallback_validated,
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


def _safe_read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _metric_status(metric_name: str, delta: float) -> str:
    if abs(delta) < 1e-9:
        return "flat"
    lower_is_better = metric_name in {"topk_redundancy_rate", "false_collapse_rate", "false_separation_rate"}
    if lower_is_better:
        return "improved" if delta < 0.0 else "regressed"
    return "improved" if delta > 0.0 else "regressed"


def _metric_regression_threshold(metric_name: str) -> float:
    thresholds = {
        "mrr": 0.01,
        "ndcg": 0.01,
        "hit_at_k": 0.001,
        "topk_redundancy_rate": 0.02,
        "false_collapse_rate": 0.01,
        "false_separation_rate": 0.01,
    }
    return thresholds.get(metric_name, 0.01)


def _classify_enterprise_trend(previous_summary: dict, current_summary: dict, metric_deltas: dict, metric_statuses: dict) -> dict:
    attention_needed: list[str] = []
    hard_failures: list[str] = []
    warnings: list[str] = []

    if current_summary and not current_summary.get("live_graph_ok", True):
        hard_failures.append("live_graph_failed")

    if current_summary.get("retrieval_alerts"):
        hard_failures.append("retrieval_alerts_present")

    if current_summary.get("retrieval_regressions"):
        hard_failures.append("retrieval_regressions_present")

    previous_best = (previous_summary.get("best_retrieval_config") or {}).get("name")
    current_best = (current_summary.get("best_retrieval_config") or {}).get("name")
    if previous_best and current_best and previous_best != current_best:
        warnings.append("best_config_changed")

    for metric_name, status in metric_statuses.items():
        if status != "regressed":
            continue
        delta = abs(float(metric_deltas.get(metric_name, 0.0)))
        if delta < _metric_regression_threshold(metric_name):
            continue
        if metric_name in {"mrr", "ndcg", "hit_at_k"}:
            hard_failures.append(f"{metric_name}_regressed")
        else:
            warnings.append(f"{metric_name}_regressed")

    for item in hard_failures + warnings:
        if item not in attention_needed:
            attention_needed.append(item)

    if hard_failures:
        overall_status = "regressed"
    elif warnings:
        overall_status = "warning"
    else:
        overall_status = "healthy"

    return {
        "overall_status": overall_status,
        "attention_needed": attention_needed,
    }


def build_trend_summary(previous_payload: dict | None, current_payload: dict) -> dict:
    previous_summary = ((previous_payload or {}).get("enterprise_summary") or {}) if isinstance(previous_payload, dict) else {}
    current_summary = current_payload.get("enterprise_summary") or {}

    prev_best = (previous_summary.get("best_retrieval_config") or {}).get("metrics") or {}
    curr_best = (current_summary.get("best_retrieval_config") or {}).get("metrics") or {}

    metric_deltas: dict[str, float] = {}
    metric_statuses: dict[str, str] = {}
    for key in ("mrr", "ndcg", "hit_at_k", "topk_redundancy_rate", "false_collapse_rate", "false_separation_rate"):
        prev_val = prev_best.get(key)
        curr_val = curr_best.get(key)
        if isinstance(prev_val, (int, float)) and isinstance(curr_val, (int, float)):
            delta = float(curr_val) - float(prev_val)
            metric_deltas[key] = delta
            metric_statuses[key] = _metric_status(key, delta)

    previous_alerts = previous_summary.get("retrieval_alerts") or {}
    current_alerts = current_summary.get("retrieval_alerts") or {}

    status = _classify_enterprise_trend(previous_summary, current_summary, metric_deltas, metric_statuses)

    return {
        "has_previous": bool(previous_summary),
        "previous_live_graph_ok": previous_summary.get("live_graph_ok"),
        "current_live_graph_ok": current_summary.get("live_graph_ok"),
        "previous_best_config": (previous_summary.get("best_retrieval_config") or {}).get("name"),
        "current_best_config": (current_summary.get("best_retrieval_config") or {}).get("name"),
        "metric_deltas": metric_deltas,
        "metric_statuses": metric_statuses,
        "previous_alert_configs": sorted(previous_alerts.keys()),
        "current_alert_configs": sorted(current_alerts.keys()),
        "overall_status": status["overall_status"],
        "attention_needed": status["attention_needed"],
    }


def write_enterprise_artifacts(payload: dict, artifact_dir: str | Path) -> dict:
    target_dir = Path(artifact_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _artifact_timestamp()
    latest_path = target_dir / "latest.json"
    previous_payload = _safe_read_json(latest_path) if latest_path.exists() else None

    payload_with_meta = dict(payload)
    payload_with_meta["artifact_meta"] = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "artifact_dir": str(target_dir),
        "timestamp": timestamp,
    }
    payload_with_meta["trend_summary"] = build_trend_summary(previous_payload, payload_with_meta)

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
    parser = argparse.ArgumentParser(
        description=(
            "Run enterprise retrieval trend evaluation and write artifacts. "
            "Use scripts/run_retrieval_quality_gate.sh for the standard "
            "tool-trust gate."
        )
    )
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

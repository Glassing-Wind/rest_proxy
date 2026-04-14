"""Offline evaluation helpers for duplicate-aware retrieval rollout."""

from __future__ import annotations

import json
import importlib.util
from pathlib import Path

try:
    from tools.brain.search import semantic_helpers as sem_helpers
except Exception:
    _SEM_HELPERS_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/semantic_helpers.py"
    _spec = importlib.util.spec_from_file_location("duplicate_eval_semantic_helpers", _SEM_HELPERS_PATH)
    sem_helpers = importlib.util.module_from_spec(_spec)
    assert _spec is not None and _spec.loader is not None
    _spec.loader.exec_module(sem_helpers)


BENCHMARK_PATH = Path("/Users/michaelmarler/Projects/rest_proxy/benchmarks/retrieval_duplicate_goldens.json")
NDCG_REGRESSION_ALERT_DELTA = 0.05


def load_benchmarks(path: str | None = None) -> list[dict]:
    raw = Path(path) if path else BENCHMARK_PATH
    with raw.open() as handle:
        payload = json.load(handle)
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    return cases if isinstance(cases, list) else []


def _result_diversity(results: list[dict], order: list[int], k: int) -> dict:
    seen_files: set[str] = set()
    seen_roles: set[str] = set()
    seen_symbols: set[str] = set()
    top = [idx for idx in order[:k] if 0 <= idx < len(results)]
    for idx in top:
        row = results[idx]
        meta = sem_helpers.coerce_meta(row)
        fp = row.get("file_path")
        if isinstance(fp, str) and fp:
            seen_files.add(fp)
        for symbol in meta.get("file_symbols") or []:
            if isinstance(symbol, str):
                seen_symbols.add(symbol)
        role = _infer_role(row, meta)
        if role:
            seen_roles.add(role)
    return {
        "unique_files": len(seen_files),
        "unique_roles": len(seen_roles),
        "unique_symbols": len(seen_symbols),
    }


def _infer_role(row: dict, meta: dict) -> str:
    file_path = str(row.get("file_path") or "").lower()
    if "test" in file_path:
        return "test"
    ctx = meta.get("context_path") or []
    if any(isinstance(part, str) and part.lower() == "api" for part in ctx):
        return "api"
    if "helper" in file_path or "util" in file_path:
        return "helper"
    if "guide" in file_path or "tutorial" in file_path:
        return "tutorial"
    if "reference" in file_path or "manual" in file_path:
        return "reference"
    return "definition"


def _mrr(order: list[int], relevant: set[int]) -> float:
    for rank, idx in enumerate(order, start=1):
        if idx in relevant:
            return 1.0 / rank
    return 0.0


def _ndcg(order: list[int], labels: dict[int, float], k: int) -> float:
    top = order[:k]
    dcg = 0.0
    for rank, idx in enumerate(top, start=1):
        gain = labels.get(idx, 0.0)
        if gain <= 0.0:
            continue
        dcg += gain / (1.0 if rank == 1 else __import__("math").log2(rank + 1))
    ideal = sorted(labels.values(), reverse=True)[:k]
    idcg = 0.0
    for rank, gain in enumerate(ideal, start=1):
        if gain <= 0.0:
            continue
        idcg += gain / (1.0 if rank == 1 else __import__("math").log2(rank + 1))
    return 0.0 if idcg == 0.0 else dcg / idcg


def _topk_redundancy_from_groups(order: list[int], group_map: dict[int, int], k: int) -> float:
    top = [idx for idx in order[:k] if idx in group_map]
    if len(top) < 2:
        return 0.0
    same = 0
    total = 0
    for left in range(len(top)):
        for right in range(left + 1, len(top)):
            total += 1
            if group_map[top[left]] == group_map[top[right]]:
                same += 1
    return 0.0 if total == 0 else same / total


def _false_collapse_rate(order: list[int], must_survive: list[int], k: int) -> float:
    if not must_survive:
        return 0.0
    top = set(order[:k])
    missing = sum(1 for idx in must_survive if idx not in top)
    return missing / len(must_survive)


def _false_separation_rate(order: list[int], cluster_pairs: list[list[int]], group_map: dict[int, int], k: int) -> float:
    if not cluster_pairs:
        return 0.0
    top = set(order[:k])
    misses = 0
    for pair in cluster_pairs:
        if len(pair) != 2:
            continue
        left, right = pair
        if left not in top or right not in top:
            continue
        if group_map.get(left) != group_map.get(right):
            misses += 1
    return misses / len(cluster_pairs)


def _order_from_trace(trace: dict, config_name: str) -> list[int]:
    selection = trace.get("selection", {})
    keep = selection.get("keep_indices") or []
    exact_suppressed = set(selection.get("exact_suppressed_indices") or [])
    reps = selection.get("representative_indices") or []
    if config_name == "baseline":
        size = len(trace.get("candidates") or keep)
        return list(range(size))
    if config_name == "exact_only":
        size = len(trace.get("candidates") or keep)
        return [idx for idx in range(size) if idx not in exact_suppressed]
    if config_name == "group_representatives":
        rep_set = list(reps)
        remainder = [idx for idx in keep if idx not in rep_set]
        return rep_set + remainder
    return list(keep)


def _promotion_alerts(config: dict, baseline: dict) -> list[str]:
    alerts: list[str] = []
    if config["hit_at_k"] < baseline["hit_at_k"]:
        alerts.append("hit_at_k_regressed")
    if config["best_answer_retained"] is False and baseline["best_answer_retained"] is True:
        alerts.append("best_answer_retention_regressed")
    if config["mrr"] + 1e-9 < baseline["mrr"]:
        alerts.append("mrr_regressed")
    ndcg_drop = float(baseline["ndcg"]) - float(config["ndcg"])
    redundancy_improved = float(config["topk_redundancy_rate"]) + 1e-9 < float(
        baseline["topk_redundancy_rate"]
    )
    grouping_improved = float(config["false_separation_rate"]) + 1e-9 < float(
        baseline["false_separation_rate"]
    )
    canonical_docs_preserved = (
        config.get("canonical_doc_preference_success") is True
        and config.get("version_sensitive_doc_retention") is True
    )
    if ndcg_drop > NDCG_REGRESSION_ALERT_DELTA and not (
        redundancy_improved or grouping_improved or canonical_docs_preserved
    ):
        alerts.append("ndcg_regressed")
    if config["topk_redundancy_rate"] > baseline["topk_redundancy_rate"]:
        alerts.append("no_redundancy_gain")
    if config["false_collapse_rate"] > baseline["false_collapse_rate"]:
        alerts.append("false_collapse_regressed")
    return alerts


def evaluate_case(case: dict) -> dict:
    results = case.get("results") or []
    query = str(case.get("query") or "")
    mode = str(case.get("mode") or "code")
    query_class = str(case.get("query_class") or "")
    expected = case.get("expected") or {}
    trace_default = sem_helpers.trace_diverse_results(results, query=query, mode=mode, experiments={})
    trace_experimental = sem_helpers.trace_diverse_results(
        results,
        query=query,
        mode=mode,
        experiments=case.get("experiments") or {},
    )
    configs = {
        "baseline": trace_default,
        "exact_only": trace_default,
        "group_representatives": trace_default,
        "query_aware": trace_default,
        "conservative_penalties": trace_default,
        "narrow_non_exact_experiments": trace_experimental,
        "promoted_non_exact": trace_experimental,
    }
    labels = {
        int(idx): float(score)
        for idx, score in (expected.get("relevance_labels") or {}).items()
    }
    relevant = {int(idx) for idx in (expected.get("best_answer_indices") or [])}
    must_survive = [int(idx) for idx in (expected.get("must_survive") or [])]
    cluster_pairs = [[int(p[0]), int(p[1])] for p in (expected.get("cluster_pairs") or []) if len(p) == 2]
    canonical_index = expected.get("canonical_index")
    version_indices = {int(idx) for idx in (expected.get("version_sensitive_indices") or [])}
    out_configs: dict[str, dict] = {}
    for name, trace in configs.items():
        order = _order_from_trace(trace, name)
        telemetry = trace.get("telemetry") or {}
        selection = trace.get("selection") or {}
        group_map: dict[int, int] = {}
        for candidate in trace.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            group_id = candidate.get("group_id")
            idx = candidate.get("idx")
            if isinstance(group_id, int) and isinstance(idx, int):
                group_map[idx] = group_id
        diversity = _result_diversity(results, order, 5)
        top = order[:5]
        out_configs[name] = {
            "order": order,
            "top_k": top,
            "hit_at_k": 1.0 if any(idx in relevant for idx in top) else 0.0,
            "best_answer_retained": (top[0] in relevant) if top and relevant else False,
            "mrr": _mrr(top, relevant),
            "ndcg": _ndcg(top, labels, 5),
            "topk_redundancy_rate": _topk_redundancy_from_groups(top, group_map, 5),
            "duplicate_group_repetition": telemetry.get("kept_group_multi_member_count", 0),
            "false_collapse_rate": _false_collapse_rate(top, must_survive, 5),
            "false_separation_rate": _false_separation_rate(top, cluster_pairs, group_map, 5),
            "canonical_doc_preference_success": (top[0] == canonical_index) if canonical_index is not None and top else None,
            "version_sensitive_doc_retention": all(idx in top for idx in version_indices) if version_indices else None,
            "diversity": diversity,
            "suppressed_indices": selection.get("suppressed_indices") or [],
            "before_after_diff": {
                "before": list(range(min(5, len(results)))),
                "after": top,
            },
        }
    baseline_cfg = out_configs["baseline"]
    for name, cfg in out_configs.items():
        cfg["promotion_alerts"] = [] if name == "baseline" else _promotion_alerts(cfg, baseline_cfg)
    return {
        "id": case.get("id"),
        "mode": mode,
        "query_class": query_class or None,
        "query": query,
        "configs": out_configs,
    }


def evaluate_benchmarks(path: str | None = None) -> dict:
    cases = load_benchmarks(path)
    reports = [evaluate_case(case) for case in cases]
    summary: dict[str, dict[str, float]] = {}
    for config_name in [
        "baseline",
        "exact_only",
        "group_representatives",
        "query_aware",
        "conservative_penalties",
        "narrow_non_exact_experiments",
        "promoted_non_exact",
    ]:
        bucket = {
            "hit_at_k": 0.0,
            "mrr": 0.0,
            "ndcg": 0.0,
            "topk_redundancy_rate": 0.0,
            "false_collapse_rate": 0.0,
            "false_separation_rate": 0.0,
        }
        count = max(1, len(reports))
        for report in reports:
            cfg = report["configs"][config_name]
            for key in bucket:
                bucket[key] += float(cfg.get(key, 0.0))
        summary[config_name] = {key: value / count for key, value in bucket.items()}
    by_mode: dict[str, dict[str, dict[str, float]]] = {}
    alerts: dict[str, list[str]] = {}
    for report in reports:
        mode = report["mode"]
        mode_bucket = by_mode.setdefault(mode, {})
        for config_name, cfg in report["configs"].items():
            bucket = mode_bucket.setdefault(
                config_name,
                {"hit_at_k": 0.0, "topk_redundancy_rate": 0.0, "false_collapse_rate": 0.0, "_count": 0.0},
            )
            bucket["hit_at_k"] += float(cfg.get("hit_at_k", 0.0))
            bucket["topk_redundancy_rate"] += float(cfg.get("topk_redundancy_rate", 0.0))
            bucket["false_collapse_rate"] += float(cfg.get("false_collapse_rate", 0.0))
            bucket["_count"] += 1.0
            for alert in cfg.get("promotion_alerts", []):
                alerts.setdefault(config_name, [])
                if alert not in alerts[config_name]:
                    alerts[config_name].append(alert)
    for mode_bucket in by_mode.values():
        for config_name, bucket in mode_bucket.items():
            count = max(1.0, bucket.pop("_count", 1.0))
            for key in list(bucket.keys()):
                bucket[key] = bucket[key] / count
    by_query_class: dict[str, int] = {}
    for report in reports:
        qclass = report.get("query_class") or "unspecified"
        by_query_class[qclass] = by_query_class.get(qclass, 0) + 1
    return {
        "cases": reports,
        "summary": summary,
        "by_mode": by_mode,
        "query_class_counts": by_query_class,
        "alerts": alerts,
    }

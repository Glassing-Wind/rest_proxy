"""Summarize live dispatcher telemetry emitted by search_codebase."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TELEMETRY_PATH = ROOT / ".runtime" / "dispatcher_telemetry.ndjson"


def load_events(path: str | None = None) -> list[dict]:
    target = Path(path) if path else DEFAULT_TELEMETRY_PATH
    if not target.exists():
        return []
    events: list[dict] = []
    try:
        with target.open(encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    events.append(payload)
    except Exception:
        return []
    return events


def _safe_bool_rate(true_count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return true_count / float(total)


def _telemetry(event: dict) -> dict:
    payload = event.get("telemetry")
    return payload if isinstance(payload, dict) else {}


def _count_signal(telemetry: dict, key: str) -> int:
    try:
        return int(telemetry.get(key, 0) or 0)
    except Exception:
        return 0


def _is_contract_eligible(telemetry: dict) -> bool:
    if not isinstance(telemetry, dict) or not telemetry:
        return False
    diagnosis = str(telemetry.get("diagnosis") or "").strip()
    if diagnosis and diagnosis != "no_exact_dispatcher_signal":
        return True
    signal_keys = (
        "semantic_exact_match_count",
        "semantic_contract_match_count",
        "ranked_exact_match_count",
        "ranked_contract_match_count",
        "final_exact_match_count",
        "final_contract_match_count",
    )
    if any(_count_signal(telemetry, key) > 0 for key in signal_keys):
        return True
    if bool(telemetry.get("rescue_applied")):
        return True
    return False


def summarize_events(events: list[dict]) -> dict:
    diagnosis_counts: Counter[str] = Counter()
    query_class_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    overall_diagnosis_counts: Counter[str] = Counter()

    rescue_applied = 0
    semantic_contract_top_hits = 0
    ranked_contract_top_hits = 0
    final_contract_top_hits = 0
    semantic_exact_hits = 0
    final_exact_hits = 0
    eligible_events = 0

    for event in events:
        if not isinstance(event, dict):
            continue
        telemetry = _telemetry(event)
        tool = str(event.get("tool") or "").strip()
        if tool:
            tool_counts[tool] += 1
        diagnosis = str(telemetry.get("diagnosis") or "").strip()
        if diagnosis:
            overall_diagnosis_counts[diagnosis] += 1
        if not _is_contract_eligible(telemetry):
            continue
        eligible_events += 1
        if diagnosis:
            diagnosis_counts[diagnosis] += 1
        query_class = str(telemetry.get("query_class") or "").strip()
        if query_class:
            query_class_counts[query_class] += 1
        if bool(telemetry.get("rescue_applied")):
            rescue_applied += 1
        semantic_top = telemetry.get("semantic_top") if isinstance(telemetry.get("semantic_top"), dict) else {}
        ranked_top = telemetry.get("ranked_top") if isinstance(telemetry.get("ranked_top"), dict) else {}
        final_top = telemetry.get("final_top") if isinstance(telemetry.get("final_top"), dict) else {}
        if bool(semantic_top.get("contract_hit")):
            semantic_contract_top_hits += 1
        if bool(ranked_top.get("contract_hit")):
            ranked_contract_top_hits += 1
        if bool(final_top.get("contract_hit")):
            final_contract_top_hits += 1
        if bool(semantic_top.get("exact_hit")):
            semantic_exact_hits += 1
        if bool(final_top.get("exact_hit")):
            final_exact_hits += 1

    total = len(events)
    ignored_events = max(total - eligible_events, 0)
    return {
        "total_events": total,
        "contract_eligible_events": eligible_events,
        "ignored_no_signal_events": ignored_events,
        "tool_counts": dict(sorted(tool_counts.items())),
        "overall_diagnosis_counts": dict(sorted(overall_diagnosis_counts.items())),
        "query_class_counts": dict(sorted(query_class_counts.items())),
        "diagnosis_counts": dict(sorted(diagnosis_counts.items())),
        "rescue_applied_count": rescue_applied,
        "rescue_applied_rate": _safe_bool_rate(rescue_applied, eligible_events),
        "semantic_top_exact_hit_rate": _safe_bool_rate(semantic_exact_hits, eligible_events),
        "final_top_exact_hit_rate": _safe_bool_rate(final_exact_hits, eligible_events),
        "semantic_top_contract_hit_rate": _safe_bool_rate(semantic_contract_top_hits, eligible_events),
        "implementation_ranking_top_contract_hit_rate": _safe_bool_rate(
            ranked_contract_top_hits, eligible_events
        ),
        "final_top_contract_hit_rate": _safe_bool_rate(final_contract_top_hits, eligible_events),
    }


def evaluate_telemetry(path: str | None = None) -> dict:
    events = load_events(path)
    return {
        "telemetry_path": str(Path(path) if path else DEFAULT_TELEMETRY_PATH),
        "summary": summarize_events(events),
    }


if __name__ == "__main__":
    print(json.dumps(evaluate_telemetry(), indent=2, sort_keys=True))

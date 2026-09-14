"""Telemetry and rollout flag helpers for retrieval policy."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from _semantic_contract import (
    FOCUSED_DISPATCHER_ANCHOR_CAPABILITY,
    FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION,
    has_focused_dispatcher_anchor_contract,
)
from memory.retrieval_intent import (
    implementation_query_class,
    implementation_query_exact_identifiers,
    implementation_query_prefers_dispatchers,
    implementation_query_prefers_request_routing,
)
from memory.retrieval_metadata import coerce_meta


def merge_duplicate_experiments(mode: str, experiments: dict | None) -> dict:
    merged = duplicate_experiment_flags_from_env(mode)
    if isinstance(experiments, dict):
        merged.update(experiments)
    return merged


def duplicate_experiment_flags_from_env(mode: str = "code") -> dict:
    mode_norm = (mode or "code").strip().lower()
    stage = (os.getenv("LM_PROXY_DUPLICATE_ROLLOUT_STAGE") or "stage2").strip().lower()
    raw = (os.getenv("LM_PROXY_DUPLICATE_EXPERIMENTS") or "").strip()
    flags = {
        "boilerplate_variant_suppression": False,
        "canonical_docs_mirror_suppression": False,
        "helper_clone_suppression": False,
        "threshold_struct": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_STRUCT"),
        "threshold_lexical": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_LEXICAL"),
        "threshold_role": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_ROLE"),
        "min_length_ratio": _float_env("LM_PROXY_DUPLICATE_MIN_LENGTH_RATIO"),
        "max_length_ratio": _float_env("LM_PROXY_DUPLICATE_MAX_LENGTH_RATIO"),
        "threshold_query_distinction": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_QUERY_DISTINCTION"),
        "allow_cross_role_suppression": False,
    }
    stage_map = {
        "off": {},
        "none": {},
        "exact_only": {},
        "stage1": {"boilerplate_variant_suppression": True},
        "stage2": {
            "boilerplate_variant_suppression": True,
            "canonical_docs_mirror_suppression": True,
        },
        "stage3": {
            "boilerplate_variant_suppression": True,
            "canonical_docs_mirror_suppression": True,
            "helper_clone_suppression": True,
        },
    }
    for key, value in stage_map.get(stage, {}).items():
        flags[key] = value
    if mode_norm == "docs":
        flags["boilerplate_variant_suppression"] = False
        flags["helper_clone_suppression"] = False
    if not raw:
        return flags
    enabled = {
        token.strip().lower()
        for token in raw.split(",")
        if token.strip()
    }
    if (
        mode_norm != "docs"
        and ("boilerplate" in enabled or "boilerplate_variant_suppression" in enabled)
    ):
        flags["boilerplate_variant_suppression"] = True
    if "canonical_docs_mirror" in enabled or "canonical_docs_mirror_suppression" in enabled:
        flags["canonical_docs_mirror_suppression"] = True
    if (
        mode_norm != "docs"
        and ("helper_clone" in enabled or "helper_clone_suppression" in enabled)
    ):
        flags["helper_clone_suppression"] = True
    return flags


def duplicate_experiment_flags_with_query_class(
    mode: str = "code",
    query_class: str | None = None,
) -> dict:
    flags = duplicate_experiment_flags_from_env(mode)
    if isinstance(query_class, str):
        trimmed = query_class.strip()
        if trimmed:
            flags["query_class_override"] = trimmed
    return flags


def _telemetry_enabled(env_var: str, default: str = "1") -> bool:
    raw = os.getenv(env_var, default).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _append_telemetry_event(
    event: dict,
    *,
    path_env: str,
    default_filename: str,
    max_events_env: str,
    default_max: int,
) -> None:
    """Resolve target path and write a bounded NDJSON event."""
    path = os.getenv(path_env, "").strip()
    if path:
        target = Path(os.path.expanduser(path))
    else:
        target = Path(__file__).resolve().parents[3] / ".runtime" / default_filename
    try:
        _append_bounded_ndjson_event(
            target,
            event,
            max_events=_int_env(max_events_env, default_max),
        )
    except Exception:
        return


def duplicate_telemetry_enabled() -> bool:
    return _telemetry_enabled("LM_PROXY_DUPLICATE_TELEMETRY")


def append_duplicate_telemetry_event(
    trace: dict,
    *,
    query: str,
    tool: str,
    mode: str,
    topic: str = "",
) -> None:
    if not duplicate_telemetry_enabled() or not isinstance(trace, dict):
        return
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "mode": mode,
        "topic": topic,
        "query": (query or "")[:500],
        "selection": trace.get("selection", {}),
        "telemetry": trace.get("telemetry", {}),
        "suppression_policy": trace.get("suppression_policy", "exact_only"),
        "experiments": trace.get("experiments", {}),
    }
    _append_telemetry_event(
        event,
        path_env="LM_PROXY_DUPLICATE_TELEMETRY_PATH",
        default_filename="duplicate_telemetry.ndjson",
        max_events_env="LM_PROXY_DUPLICATE_TELEMETRY_MAX_EVENTS",
        default_max=500,
    )


def dispatcher_telemetry_enabled() -> bool:
    return _telemetry_enabled("LM_PROXY_DISPATCHER_TELEMETRY")


def append_dispatcher_telemetry_event(
    telemetry: dict,
    *,
    query: str,
    tool: str,
    topic: str = "",
) -> None:
    if not dispatcher_telemetry_enabled() or not isinstance(telemetry, dict):
        return
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "topic": topic,
        "query": (query or "")[:500],
        "telemetry": telemetry,
    }
    _append_telemetry_event(
        event,
        path_env="LM_PROXY_DISPATCHER_TELEMETRY_PATH",
        default_filename="dispatcher_telemetry.ndjson",
        max_events_env="LM_PROXY_DISPATCHER_TELEMETRY_MAX_EVENTS",
        default_max=200,
    )


def routing_telemetry_enabled() -> bool:
    return _telemetry_enabled("LM_PROXY_ROUTING_TELEMETRY")


def append_routing_telemetry_event(
    telemetry: dict,
    *,
    query: str,
    tool: str,
    topic: str = "",
) -> None:
    if not routing_telemetry_enabled() or not isinstance(telemetry, dict):
        return
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "topic": topic,
        "query": (query or "")[:500],
        "telemetry": telemetry,
    }
    _append_telemetry_event(
        event,
        path_env="LM_PROXY_ROUTING_TELEMETRY_PATH",
        default_filename="routing_telemetry.ndjson",
        max_events_env="LM_PROXY_ROUTING_TELEMETRY_MAX_EVENTS",
        default_max=200,
    )


def dispatcher_contract_telemetry(
    *,
    query: str,
    query_class: str | None,
    semantic_candidates: list[dict],
    ranked_candidates: list[dict],
    final_results: list[dict],
    rescue_applied: bool,
) -> dict | None:
    exact_identifiers = implementation_query_exact_identifiers(query)
    if not implementation_query_prefers_dispatchers(query) or not exact_identifiers:
        return None

    def _declared(row: dict) -> set[str]:
        meta = coerce_meta(row)
        return {
            str(symbol).strip().lower()
            for symbol in (meta.get("declared_symbols") or [])
            if str(symbol).strip()
        }

    def _exact_matches(rows: list[dict]) -> list[dict]:
        matches: list[dict] = []
        for row in rows or []:
            if int(row.get("implementation_dispatcher_priority", 0) or 0) < 5:
                continue
            if _declared(row) & exact_identifiers:
                matches.append(row)
        return matches

    def _contract_matches(rows: list[dict]) -> list[dict]:
        return [row for row in _exact_matches(rows) if has_focused_dispatcher_anchor_contract(coerce_meta(row))]

    def _top_snapshot(rows: list[dict]) -> dict:
        top = rows[0] if rows else {}
        top_meta = coerce_meta(top)
        top_declared = sorted(_declared(top)) if isinstance(top, dict) else []
        top_exact = bool(top_declared and (set(top_declared) & exact_identifiers))
        top_contract = bool(top_exact and has_focused_dispatcher_anchor_contract(top_meta))
        return {
            "file_path": top.get("file_path") if isinstance(top, dict) else None,
            "declared_symbols": top_declared,
            "exact_hit": top_exact,
            "contract_hit": top_contract,
        }

    semantic_exact = _exact_matches(semantic_candidates)
    semantic_contract = _contract_matches(semantic_candidates)
    ranked_exact = _exact_matches(ranked_candidates)
    ranked_contract = _contract_matches(ranked_candidates)
    final_exact = _exact_matches(final_results)
    final_contract = _contract_matches(final_results)

    semantic_top = _top_snapshot(semantic_candidates)
    ranked_top = _top_snapshot(ranked_candidates)
    final_top = _top_snapshot(final_results)

    diagnosis = "no_exact_dispatcher_signal"
    if semantic_contract:
        diagnosis = "ranking_or_promotion_needed"
        if ranked_top["contract_hit"]:
            diagnosis = "ranking_surfaces_contract"
        elif final_top["contract_hit"] and rescue_applied:
            diagnosis = "rescue_or_final_promotion_surfaces_contract"
        elif final_top["contract_hit"]:
            diagnosis = "final_promotion_surfaces_contract"
    elif semantic_exact:
        diagnosis = "contract_missing_from_semantic_candidates"
        if final_top["contract_hit"]:
            diagnosis = "contract_missing_from_semantic_candidates_but_recovered"
    elif final_top["contract_hit"]:
        diagnosis = "semantic_recall_missing_contract_candidate"

    return {
        "query_class": query_class or implementation_query_class(query),
        "exact_identifiers": sorted(exact_identifiers),
        "dispatcher_anchor_contract_capability": FOCUSED_DISPATCHER_ANCHOR_CAPABILITY,
        "dispatcher_anchor_contract_version": FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION,
        "rescue_applied": bool(rescue_applied),
        "diagnosis": diagnosis,
        "semantic_exact_match_count": len(semantic_exact),
        "semantic_contract_match_count": len(semantic_contract),
        "ranked_exact_match_count": len(ranked_exact),
        "ranked_contract_match_count": len(ranked_contract),
        "final_exact_match_count": len(final_exact),
        "final_contract_match_count": len(final_contract),
        "semantic_top": semantic_top,
        "ranked_top": ranked_top,
        "final_top": final_top,
    }


def routing_signal_telemetry(
    *,
    query: str,
    query_class: str | None,
    semantic_candidates: list[dict],
    ranked_candidates: list[dict],
    final_results: list[dict],
    partition_applied: bool,
) -> dict | None:
    if not implementation_query_prefers_request_routing(query):
        return None

    def _signal_snapshot(row: dict | None) -> dict:
        if not isinstance(row, dict):
            return {
                "file_path": None,
                "routing_hit": False,
                "request_handler_hit": False,
                "controller_entity_hit": False,
            }
        routing_hit = int(row.get("implementation_routing_priority", 0) or 0) > 0
        request_handler_hit = int(row.get("implementation_request_handler_priority", 0) or 0) > 0
        controller_entity_hit = int(row.get("implementation_controller_entity_hit", 0) or 0) > 0
        return {
            "file_path": row.get("file_path"),
            "routing_hit": routing_hit,
            "request_handler_hit": request_handler_hit,
            "controller_entity_hit": controller_entity_hit,
        }

    def _signal_rows(rows: list[dict]) -> list[dict]:
        matches: list[dict] = []
        for row in rows or []:
            snap = _signal_snapshot(row)
            if snap["routing_hit"] or snap["request_handler_hit"] or snap["controller_entity_hit"]:
                matches.append(row)
        return matches

    semantic_signal = _signal_rows(semantic_candidates)
    ranked_signal = _signal_rows(ranked_candidates)
    final_signal = _signal_rows(final_results)

    semantic_top = _signal_snapshot(semantic_candidates[0] if semantic_candidates else None)
    ranked_top = _signal_snapshot(ranked_candidates[0] if ranked_candidates else None)
    final_top = _signal_snapshot(final_results[0] if final_results else None)

    diagnosis = "no_routing_signal"
    if semantic_signal:
        diagnosis = "ranking_or_partition_needed"
        if ranked_top["routing_hit"] or ranked_top["request_handler_hit"] or ranked_top["controller_entity_hit"]:
            diagnosis = "ranking_surfaces_routing_signal"
        elif (
            final_top["routing_hit"] or final_top["request_handler_hit"] or final_top["controller_entity_hit"]
        ) and partition_applied:
            diagnosis = "partition_surfaces_routing_signal"
        elif final_top["routing_hit"] or final_top["request_handler_hit"] or final_top["controller_entity_hit"]:
            diagnosis = "final_promotion_surfaces_routing_signal"
    elif final_top["routing_hit"] or final_top["request_handler_hit"] or final_top["controller_entity_hit"]:
        diagnosis = "semantic_signal_missing_but_recovered"

    return {
        "query_class": query_class or implementation_query_class(query),
        "partition_applied": bool(partition_applied),
        "diagnosis": diagnosis,
        "semantic_signal_match_count": len(semantic_signal),
        "ranked_signal_match_count": len(ranked_signal),
        "final_signal_match_count": len(final_signal),
        "semantic_top": semantic_top,
        "ranked_top": ranked_top,
        "final_top": final_top,
    }



def _append_bounded_ndjson_event(target: Path, event: dict, *, max_events: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(event, sort_keys=True)
    if max_events <= 0:
        with target.open("a", encoding="utf-8") as fh:
            fh.write(encoded + "\n")
        return
    lines: list[str] = []
    if target.exists():
        try:
            with target.open("r", encoding="utf-8") as fh:
                lines = [line.rstrip("\n") for line in fh if line.strip()]
        except Exception:
            lines = []
    lines.append(encoded)
    if len(lines) > max_events:
        lines = lines[-max_events:]
    with target.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _float_env(name: str) -> float | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default

"""Telemetry and rollout flag helpers for retrieval policy."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


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


def duplicate_telemetry_enabled() -> bool:
    raw = os.getenv("LM_PROXY_DUPLICATE_TELEMETRY", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


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
    path = os.getenv("LM_PROXY_DUPLICATE_TELEMETRY_PATH", "").strip()
    if path:
        target = Path(os.path.expanduser(path))
    else:
        target = Path(__file__).resolve().parents[3] / ".runtime" / "duplicate_telemetry.ndjson"
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
    try:
        _append_bounded_ndjson_event(
            target,
            event,
            max_events=_int_env("LM_PROXY_DUPLICATE_TELEMETRY_MAX_EVENTS", 500),
        )
    except Exception:
        return


def dispatcher_telemetry_enabled() -> bool:
    raw = os.getenv("LM_PROXY_DISPATCHER_TELEMETRY", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def append_dispatcher_telemetry_event(
    telemetry: dict,
    *,
    query: str,
    tool: str,
    topic: str = "",
) -> None:
    if not dispatcher_telemetry_enabled() or not isinstance(telemetry, dict):
        return
    path = os.getenv("LM_PROXY_DISPATCHER_TELEMETRY_PATH", "").strip()
    if path:
        target = Path(os.path.expanduser(path))
    else:
        target = Path(__file__).resolve().parents[3] / ".runtime" / "dispatcher_telemetry.ndjson"
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "topic": topic,
        "query": (query or "")[:500],
        "telemetry": telemetry,
    }
    try:
        _append_bounded_ndjson_event(
            target,
            event,
            max_events=_int_env("LM_PROXY_DISPATCHER_TELEMETRY_MAX_EVENTS", 200),
        )
    except Exception:
        return


def routing_telemetry_enabled() -> bool:
    raw = os.getenv("LM_PROXY_ROUTING_TELEMETRY", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def append_routing_telemetry_event(
    telemetry: dict,
    *,
    query: str,
    tool: str,
    topic: str = "",
) -> None:
    if not routing_telemetry_enabled() or not isinstance(telemetry, dict):
        return
    path = os.getenv("LM_PROXY_ROUTING_TELEMETRY_PATH", "").strip()
    if path:
        target = Path(os.path.expanduser(path))
    else:
        target = Path(__file__).resolve().parents[3] / ".runtime" / "routing_telemetry.ndjson"
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "topic": topic,
        "query": (query or "")[:500],
        "telemetry": telemetry,
    }
    try:
        _append_bounded_ndjson_event(
            target,
            event,
            max_events=_int_env("LM_PROXY_ROUTING_TELEMETRY_MAX_EVENTS", 200),
        )
    except Exception:
        return


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

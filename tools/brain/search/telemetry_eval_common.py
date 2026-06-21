"""Shared primitives for offline search telemetry evaluators."""

from __future__ import annotations

import json
from pathlib import Path


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        with path.open(encoding="utf-8") as handle:
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


def safe_bool_rate(true_count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return true_count / float(total)


def telemetry_payload(event: dict) -> dict:
    payload = event.get("telemetry")
    return payload if isinstance(payload, dict) else {}


def count_signal(telemetry: dict, key: str) -> int:
    try:
        return int(telemetry.get(key, 0) or 0)
    except Exception:
        return 0

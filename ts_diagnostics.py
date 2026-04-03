"""Helpers for filtering known false-positive ts-pack diagnostics."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

_TS_QUERYRAW_TAGGED_TEMPLATE_RE = re.compile(r"\$queryRaw(?:Unsafe)?\s*<.+>\s*`")


def normalize_ts_pack_result(source: str, lang: str | None, result: dict | None) -> dict:
    """Return a copy of *result* with known false-positive diagnostics removed."""
    if not result:
        return {}

    normalized = deepcopy(result)
    diagnostics = list(normalized.get("diagnostics") or [])
    if not diagnostics or lang not in {"typescript", "tsx"}:
      return normalized

    lines = source.splitlines()
    filtered: list[dict[str, Any]] = []
    dropped = 0

    for diag in diagnostics:
        message = str(diag.get("message") or "")
        span = diag.get("span") or {}
        line_idx = span.get("start_line")
        line_text = lines[line_idx] if isinstance(line_idx, int) and 0 <= line_idx < len(lines) else ""

        if (
            message == "Missing expected node: !"
            and _TS_QUERYRAW_TAGGED_TEMPLATE_RE.search(line_text)
        ):
            dropped += 1
            continue

        filtered.append(diag)

    if dropped:
        normalized["diagnostics"] = filtered
        metrics = dict(normalized.get("metrics") or {})
        if "error_count" in metrics and isinstance(metrics["error_count"], int):
            metrics["error_count"] = max(0, metrics["error_count"] - dropped)
        if "errorCount" in metrics and isinstance(metrics["errorCount"], int):
            metrics["errorCount"] = max(0, metrics["errorCount"] - dropped)
        normalized["metrics"] = metrics

    return normalized

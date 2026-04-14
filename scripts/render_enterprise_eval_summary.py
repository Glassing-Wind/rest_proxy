#!/usr/bin/env python3
"""Render enterprise eval JSON artifacts into concise Markdown outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _fmt_metric(value) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return "n/a"


def render_summary(payload: dict) -> str:
    enterprise = payload.get("enterprise_summary") or {}
    trend = payload.get("trend_summary") or {}
    best = enterprise.get("best_retrieval_config") or {}
    metrics = best.get("metrics") or {}
    attention = list(trend.get("attention_needed") or [])
    query_counts = enterprise.get("retrieval_query_class_counts") or {}

    lines: list[str] = []
    lines.append("## Enterprise Eval")
    lines.append("")
    lines.append(f"- Overall status: `{trend.get('overall_status', 'unknown')}`")
    lines.append(f"- Live graph ok: `{enterprise.get('live_graph_ok')}`")
    lines.append(f"- Best retrieval config: `{best.get('name', '') or 'unknown'}`")
    if attention:
        joined = ", ".join(f"`{item}`" for item in attention)
        lines.append(f"- Attention needed: {joined}")
    else:
        lines.append("- Attention needed: none")
    lines.append("")
    lines.append("### Key Metrics")
    lines.append("")
    lines.append("| Metric | Value | Delta | Status |")
    lines.append("| --- | ---: | ---: | --- |")
    for name in ("mrr", "ndcg", "hit_at_k", "topk_redundancy_rate", "false_collapse_rate", "false_separation_rate"):
        delta = (trend.get("metric_deltas") or {}).get(name)
        status = (trend.get("metric_statuses") or {}).get(name, "n/a")
        lines.append(
            f"| `{name}` | {_fmt_metric(metrics.get(name))} | {_fmt_metric(delta)} | `{status}` |"
        )
    if query_counts:
        lines.append("")
        lines.append("### Query Classes")
        lines.append("")
        for name in sorted(query_counts):
            lines.append(f"- `{name}`: {query_counts[name]}")
    return "\n".join(lines) + "\n"


def render_pr_comment(payload: dict) -> str:
    trend = payload.get("trend_summary") or {}
    lines = [
        "<!-- enterprise-eval-comment -->",
        "## Enterprise Eval Alert",
        "",
        f"Status: `{trend.get('overall_status', 'unknown')}`",
        "",
        render_summary(payload).rstrip(),
        "",
        "_This comment is updated automatically when enterprise eval is not healthy._",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_path", help="Path to enterprise eval JSON artifact.")
    parser.add_argument(
        "--format",
        choices=("summary", "comment"),
        default="summary",
        help="Render a job-summary markdown block or a sticky PR comment body.",
    )
    args = parser.parse_args()
    payload = json.loads(Path(args.artifact_path).read_text(encoding="utf-8"))
    if args.format == "comment":
        print(render_pr_comment(payload), end="")
    else:
        print(render_summary(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

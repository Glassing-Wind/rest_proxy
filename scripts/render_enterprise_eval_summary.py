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
    dispatcher = enterprise.get("dispatcher_summary") or {}
    dispatcher_telemetry = enterprise.get("dispatcher_telemetry_summary") or {}
    dispatcher_telemetry_current = enterprise.get("dispatcher_telemetry_current_summary") or dispatcher_telemetry
    dispatcher_telemetry_current_scope = str(enterprise.get("dispatcher_telemetry_current_scope") or "historical")
    dispatcher_telemetry_recent = enterprise.get("dispatcher_telemetry_recent_summary") or {}
    routing_telemetry = enterprise.get("routing_telemetry_summary") or {}
    routing_telemetry_current = enterprise.get("routing_telemetry_current_summary") or routing_telemetry
    routing_telemetry_current_scope = str(enterprise.get("routing_telemetry_current_scope") or "historical")
    routing_telemetry_recent = enterprise.get("routing_telemetry_recent_summary") or {}
    attention = list(trend.get("attention_needed") or [])
    query_counts = enterprise.get("retrieval_query_class_counts") or {}
    mcp_workflows = list(enterprise.get("mcp_investigation_workflows") or [])

    lines: list[str] = []
    lines.append("## Enterprise Eval")
    lines.append("")
    lines.append(f"- Overall status: `{trend.get('overall_status', 'unknown')}`")
    lines.append(f"- Live graph ok: `{enterprise.get('live_graph_ok')}`")
    lines.append(f"- MCP investigation ok: `{enterprise.get('mcp_investigation_ok')}`")
    lines.append(f"- Best retrieval config: `{best.get('name', '') or 'unknown'}`")
    if attention:
        joined = ", ".join(f"`{item}`" for item in attention)
        lines.append(f"- Attention needed: {joined}")
    else:
        lines.append("- Attention needed: none")
    if not enterprise.get("mcp_investigation_skipped", True):
        lines.append(
            f"- MCP workflows trusted: `{len(mcp_workflows)}` workflow(s), "
            f"`{enterprise.get('mcp_investigation_trusted_tool_calls', 0)}` tool call(s)"
        )
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
    if dispatcher:
        lines.append("")
        lines.append("### Dispatcher Eval")
        lines.append("")
        lines.append(
            f"- Semantic candidate hit rate: `{_fmt_metric(dispatcher.get('semantic_candidate_hit_rate'))}`"
        )
        lines.append(
            f"- Semantic candidate contract hit rate: `{_fmt_metric(dispatcher.get('semantic_candidate_contract_hit_rate'))}`"
        )
        lines.append(
            f"- Implementation ranking top-hit rate: `{_fmt_metric(dispatcher.get('implementation_ranking_top_hit_rate'))}`"
        )
        lines.append(
            f"- Implementation ranking contract top-hit rate: `{_fmt_metric(dispatcher.get('implementation_ranking_contract_top_hit_rate'))}`"
        )
        lines.append(
            f"- Final dispatcher selection top-hit rate: `{_fmt_metric(dispatcher.get('final_dispatcher_selection_top_hit_rate'))}`"
        )
        lines.append(
            f"- Final dispatcher selection contract top-hit rate: `{_fmt_metric(dispatcher.get('final_dispatcher_selection_contract_top_hit_rate'))}`"
        )
        capability = dispatcher.get("dispatcher_anchor_contract_capability")
        version = dispatcher.get("dispatcher_anchor_contract_version")
        if capability or version is not None:
            lines.append(
                f"- Dispatcher anchor contract: `{capability or 'unknown'}` (version `{version if version is not None else 'n/a'}`)"
            )
        diagnosis_counts = dispatcher.get("diagnosis_counts") or {}
        if diagnosis_counts:
            for name in sorted(diagnosis_counts):
                lines.append(f"- Dispatcher diagnosis `{name}`: {diagnosis_counts[name]}")
    if dispatcher_telemetry_current:
        lines.append("")
        lines.append("### Live Dispatcher Telemetry")
        lines.append("")
        lines.append(f"- Current window: `{dispatcher_telemetry_current_scope}`")
        lines.append(
            f"- Contract-eligible events: `{dispatcher_telemetry_current.get('contract_eligible_events', 0)}`"
        )
        ignored = int(dispatcher_telemetry_current.get("ignored_no_signal_events", 0) or 0)
        if ignored:
            lines.append(f"- Ignored no-signal events: `{ignored}`")
        lines.append(
            f"- Rescue applied rate: `{_fmt_metric(dispatcher_telemetry_current.get('rescue_applied_rate'))}`"
        )
        lines.append(
            f"- Semantic top exact-hit rate: `{_fmt_metric(dispatcher_telemetry_current.get('semantic_top_exact_hit_rate'))}`"
        )
        lines.append(
            f"- Final top exact-hit rate: `{_fmt_metric(dispatcher_telemetry_current.get('final_top_exact_hit_rate'))}`"
        )
        lines.append(
            f"- Semantic top contract-hit rate: `{_fmt_metric(dispatcher_telemetry_current.get('semantic_top_contract_hit_rate'))}`"
        )
        lines.append(
            f"- Ranking top contract-hit rate: `{_fmt_metric(dispatcher_telemetry_current.get('implementation_ranking_top_contract_hit_rate'))}`"
        )
        lines.append(
            f"- Final top contract-hit rate: `{_fmt_metric(dispatcher_telemetry_current.get('final_top_contract_hit_rate'))}`"
        )
        diagnosis_counts = dispatcher_telemetry_current.get("diagnosis_counts") or {}
        if diagnosis_counts:
            for name in sorted(diagnosis_counts):
                lines.append(f"- Live diagnosis `{name}`: {diagnosis_counts[name]}")
    if dispatcher_telemetry and dispatcher_telemetry_current is not dispatcher_telemetry:
        lines.append("")
        lines.append("### Historical Dispatcher Telemetry")
        lines.append("")
        lines.append(
            f"- Historical total events: `{dispatcher_telemetry.get('total_events', 0)}`"
        )
        lines.append(
            f"- Historical contract-eligible events: `{dispatcher_telemetry.get('contract_eligible_events', 0)}`"
        )
        lines.append(
            f"- Historical rescue applied rate: `{_fmt_metric(dispatcher_telemetry.get('rescue_applied_rate'))}`"
        )
        lines.append(
            f"- Historical semantic top contract-hit rate: `{_fmt_metric(dispatcher_telemetry.get('semantic_top_contract_hit_rate'))}`"
        )
        lines.append(
            f"- Historical ranking top contract-hit rate: `{_fmt_metric(dispatcher_telemetry.get('implementation_ranking_top_contract_hit_rate'))}`"
        )
        lines.append(
            f"- Historical final top contract-hit rate: `{_fmt_metric(dispatcher_telemetry.get('final_top_contract_hit_rate'))}`"
        )
        diagnosis_counts = dispatcher_telemetry.get("diagnosis_counts") or {}
        if diagnosis_counts:
            for name in sorted(diagnosis_counts):
                lines.append(f"- Historical diagnosis `{name}`: {diagnosis_counts[name]}")
    if routing_telemetry_current:
        lines.append("")
        lines.append("### Live Routing Telemetry")
        lines.append("")
        lines.append(f"- Current window: `{routing_telemetry_current_scope}`")
        lines.append(
            f"- Signal-eligible events: `{routing_telemetry_current.get('signal_eligible_events', 0)}`"
        )
        ignored = int(routing_telemetry_current.get("ignored_no_signal_events", 0) or 0)
        if ignored:
            lines.append(f"- Ignored no-signal events: `{ignored}`")
        lines.append(
            f"- Partition applied rate: `{_fmt_metric(routing_telemetry_current.get('partition_applied_rate'))}`"
        )
        lines.append(
            f"- Semantic top signal-hit rate: `{_fmt_metric(routing_telemetry_current.get('semantic_top_signal_hit_rate'))}`"
        )
        lines.append(
            f"- Ranking top signal-hit rate: `{_fmt_metric(routing_telemetry_current.get('implementation_ranking_top_signal_hit_rate'))}`"
        )
        lines.append(
            f"- Final top signal-hit rate: `{_fmt_metric(routing_telemetry_current.get('final_top_signal_hit_rate'))}`"
        )
        diagnosis_counts = routing_telemetry_current.get("diagnosis_counts") or {}
        if diagnosis_counts:
            for name in sorted(diagnosis_counts):
                lines.append(f"- Routing diagnosis `{name}`: {diagnosis_counts[name]}")
    if routing_telemetry and routing_telemetry_current is not routing_telemetry:
        lines.append("")
        lines.append("### Historical Routing Telemetry")
        lines.append("")
        lines.append(
            f"- Historical total events: `{routing_telemetry.get('total_events', 0)}`"
        )
        lines.append(
            f"- Historical signal-eligible events: `{routing_telemetry.get('signal_eligible_events', 0)}`"
        )
        lines.append(
            f"- Historical partition applied rate: `{_fmt_metric(routing_telemetry.get('partition_applied_rate'))}`"
        )
        lines.append(
            f"- Historical semantic top signal-hit rate: `{_fmt_metric(routing_telemetry.get('semantic_top_signal_hit_rate'))}`"
        )
        lines.append(
            f"- Historical ranking top signal-hit rate: `{_fmt_metric(routing_telemetry.get('implementation_ranking_top_signal_hit_rate'))}`"
        )
        lines.append(
            f"- Historical final top signal-hit rate: `{_fmt_metric(routing_telemetry.get('final_top_signal_hit_rate'))}`"
        )
        diagnosis_counts = routing_telemetry.get("diagnosis_counts") or {}
        if diagnosis_counts:
            for name in sorted(diagnosis_counts):
                lines.append(f"- Historical routing diagnosis `{name}`: {diagnosis_counts[name]}")
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

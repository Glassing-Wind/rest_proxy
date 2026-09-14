"""Compact retrieval QA contracts and debug formatting."""

from __future__ import annotations


def result_label(result: dict, index: int) -> str:
    return str(result.get("file_path") or result.get("source_url") or f"result[{index}]")


def compact_duplicate_analysis(contract: dict, results: list[dict]) -> dict:
    """Reduce duplicate analysis to the decisions an MCP caller needs."""
    pairs = []
    for pair in contract.get("pairs") or []:
        if not isinstance(pair, dict) or not pair.get("duplicate"):
            continue
        left = pair.get("left")
        right = pair.get("right")
        item = {
            "left": left,
            "right": right,
            "left_path": (
                result_label(results[left], left)
                if isinstance(left, int) and 0 <= left < len(results)
                else None
            ),
            "right_path": (
                result_label(results[right], right)
                if isinstance(right, int) and 0 <= right < len(results)
                else None
            ),
            "score": pair.get("score"),
        }
        relations = pair.get("relations") or pair.get("duplicate_relations") or pair.get("relation")
        if relations:
            item["relations"] = relations if isinstance(relations, list) else [relations]
        pairs.append(item)

    kept = contract.get("keep_indices") or []
    suppressed = contract.get("suppressed_indices") or []
    output = {
        "mode": contract.get("mode"),
        "summary": {
            "input_count": len(results),
            "kept_count": len(kept),
            "suppressed_count": len(suppressed),
            "duplicate_pair_count": len(pairs),
        },
        "keep_indices": kept,
        "suppressed_indices": suppressed,
        "duplicate_pairs": pairs,
    }
    if not pairs:
        output["guidance"] = (
            "No duplicate pairs were detected; changing duplicate policy is unlikely "
            "to improve this result set."
        )
    return output


def compact_rerank_contract(contract: dict, results: list[dict]) -> dict:
    """Expose rerank order, suppression decisions, and health signals without echoing content."""
    kept = contract.get("keep_indices") or []
    suppressed = contract.get("suppressed_indices") or []
    telemetry = contract.get("telemetry") if isinstance(contract.get("telemetry"), dict) else {}
    return {
        "summary": {
            "input_count": len(results),
            "kept_count": len(kept),
            "suppressed_count": len(suppressed),
            "suppression_policy": contract.get("suppression_policy"),
        },
        "ordered_results": [
            {"index": idx, "path": result_label(results[idx], idx)}
            for idx in kept
            if isinstance(idx, int) and 0 <= idx < len(results)
        ],
        "suppressed_results": [
            {"index": idx, "path": result_label(results[idx], idx)}
            for idx in suppressed
            if isinstance(idx, int) and 0 <= idx < len(results)
        ],
        "relation_counts": telemetry.get("relation_counts") or {},
        "redundancy": {
            "before": telemetry.get("topk_redundancy_before", 0.0),
            "after": telemetry.get("topk_redundancy_after", 0.0),
        },
        "alerts": telemetry.get("regression_alerts") or [],
    }


def compact_implementation_ranking_trace(trace: dict) -> dict:
    """Keep only ranking contributions that affected the decision."""
    compact_rows = []
    for rank, row in enumerate(trace.get("rows") or [], start=1):
        components = row.get("components") if isinstance(row.get("components"), dict) else {}
        contributions = {
            key: value
            for key, value in components.items()
            if key != "base_relevance"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value != 0
        }
        compact_rows.append(
            {
                "rank": rank,
                "path": row.get("file_path"),
                "base_relevance": row.get("base_relevance", 0.0),
                "rank_score": row.get("rank_score", 0.0),
                "role": row.get("role"),
                "node_types": row.get("node_types") or [],
                "contributions": contributions,
            }
        )
    output = {"query_class": trace.get("query_class"), "rows": compact_rows}
    if not compact_rows:
        output["guidance"] = (
            "Provide candidate rows with content, file_path, and a relevance score. "
            "Current semantic metadata under metadata.file_roles, chunk_role, node_types, "
            "and file_symbols produces the most useful trace."
        )
    return output


def summarize_trace_for_debug(trace: dict) -> list[str]:
    selection = trace.get("selection") if isinstance(trace, dict) else {}
    telemetry = trace.get("telemetry") if isinstance(trace, dict) else {}
    lines = [
        "duplicate trace:",
        f"- suppression_policy={trace.get('suppression_policy', 'exact_only')}",
        f"- keep={selection.get('keep_indices', [])}",
        f"- exact_suppressed={selection.get('exact_suppressed_indices', [])}",
        f"- experimental_suppressed={telemetry.get('experimental_suppressions', 0)}",
        f"- query_class={telemetry.get('query_class', 'unknown')}",
        f"- topk_redundancy_before={telemetry.get('topk_redundancy_before', 0.0):.3f}",
        f"- topk_redundancy_after={telemetry.get('topk_redundancy_after', 0.0):.3f}",
        f"- multi_rep_groups={telemetry.get('multi_representative_group_count', 0)}",
        f"- query_distinct_multi_rep={telemetry.get('query_distinct_multi_rep_count', 0)}",
        f"- alerts={','.join(telemetry.get('regression_alerts', []) or []) or '(none)'}",
    ]
    candidates = trace.get("candidates") if isinstance(trace, dict) else []
    if isinstance(candidates, list):
        for candidate in candidates[:6]:
            if not isinstance(candidate, dict):
                continue
            lines.append(
                "- idx={idx} group={group} kept={kept} reason={reason} beat_by={beat} qdist={qdist:.3f} rels={rels}".format(
                    idx=candidate.get("idx"),
                    group=candidate.get("group_id"),
                    kept=candidate.get("kept"),
                    reason=candidate.get("decision_reason"),
                    beat=candidate.get("beaten_by"),
                    qdist=float(candidate.get("query_distinction_score", 0.0)),
                    rels=",".join(candidate.get("duplicate_relations") or []),
                )
            )
    return lines

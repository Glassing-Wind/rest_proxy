"""Duplicate analysis and diversification policy for retrieval results."""

from __future__ import annotations

import json
import subprocess

from memory.retrieval_metadata import coerce_meta, meta_score
from memory.retrieval_telemetry import (
    duplicate_experiment_flags_from_env,
    merge_duplicate_experiments,
)

__all__ = [
    "analyze_duplicate_results_contract",
    "analyze_near_duplicate_results",
    "candidate_relevance_score",
    "collapse_near_duplicate_results",
    "rerank_diverse_results",
    "rerank_retrieval_results_contract",
    "trace_diverse_results",
]


def _retrieval_policy_helpers():
    from memory import retrieval_policy

    return retrieval_policy


def _context_payload(results: list[dict]) -> str:
    payload: list[dict] = []
    for result in results:
        file_path = result.get("file_path") or result.get("source_url") or ""
        payload.append(
            {
                "file_path": file_path,
                "metadata": coerce_meta(result),
            }
        )
    return json.dumps(payload)


def _load_ts_pack():
    try:
        import tree_sitter_language_pack as ts_pack
    except Exception:
        return None
    return ts_pack


def _ts_pack_runtime_call(
    function_name: str,
    *,
    texts: list[str],
    relevance_scores: list[float] | None = None,
    query: str = "",
    mode: str = "code",
    contexts_json: str = "[]",
    experiments: dict | None = None,
):
    try:
        from _runtime import resolve_python_runtime
    except Exception:
        return None
    runtime = resolve_python_runtime()
    python_cmd = list(runtime.get("cmd") or [])
    if not python_cmd:
        return None
    payload = {
        "function": function_name,
        "texts": texts,
        "relevance_scores": relevance_scores or [],
        "query": query or None,
        "mode": mode,
        "contexts_json": contexts_json,
        "experiments": experiments or {},
    }
    script = """
import json, sys
import tree_sitter_language_pack as ts_pack
payload = json.loads(sys.stdin.read())
fn = payload["function"]
if fn == "analyze_duplicate_texts":
    result = ts_pack.analyze_duplicate_texts(
        payload["texts"],
        payload.get("query"),
        payload.get("mode"),
        payload.get("contexts_json"),
    )
elif fn == "rerank_diverse_texts":
    result = ts_pack.rerank_diverse_texts(
        payload["texts"],
        payload.get("relevance_scores") or [],
        payload.get("query"),
        payload.get("mode"),
        payload.get("contexts_json"),
    )
elif fn == "trace_diverse_texts":
    result = ts_pack.trace_diverse_texts(
        payload["texts"],
        payload.get("relevance_scores") or [],
        payload.get("query"),
        payload.get("mode"),
        payload.get("contexts_json"),
        json.dumps(payload.get("experiments") or {}),
    )
else:
    raise SystemExit(f"unknown function: {fn}")
print(json.dumps(result))
"""
    try:
        completed = subprocess.run(
            python_cmd + ["-c", script],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return None
    try:
        return json.loads(completed.stdout)
    except Exception:
        return None


def collapse_near_duplicate_results(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
    rerank_fn=None,
) -> list[dict]:
    if len(results) < 2:
        return results
    rerank = rerank_fn if callable(rerank_fn) else rerank_retrieval_results_contract
    contract = rerank(results, query=query, mode=mode)
    keep_indices = contract.get("keep_indices")
    if not isinstance(keep_indices, list):
        return results
    keep_set = {
        idx
        for idx in keep_indices
        if isinstance(idx, int) and 0 <= idx < len(results)
    }
    if not keep_set:
        return results
    return [results[idx] for idx in keep_indices if idx in keep_set]


def _coerce_result_score(result: dict) -> float:
    for key in ("rrf", "rank_score", "score", "relevance_score"):
        value = result.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def candidate_relevance_score(result: dict) -> float:
    for key in ("rank_score", "relevance_score", "score", "rrf"):
        value = result.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _normalize_result_item(result: dict, index: int) -> dict:
    normalized = dict(result) if isinstance(result, dict) else {"content": str(result)}
    normalized["original_index"] = index
    content = normalized.get("content")
    if not isinstance(content, str):
        normalized["content"] = "" if content is None else str(content)
    normalized["rrf"] = _coerce_result_score(normalized)
    meta = normalized.get("metadata")
    if meta is None and isinstance(normalized.get("_meta"), dict):
        normalized["metadata"] = dict(normalized["_meta"])
    elif isinstance(meta, dict):
        normalized["metadata"] = dict(meta)
    return normalized


def _normalize_result_items(results: list[dict]) -> list[dict]:
    return [_normalize_result_item(result, idx) for idx, result in enumerate(results)]


def _prepare_results_for_duplicate_rerank(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
) -> list[dict]:
    prepared = _normalize_result_items(results)
    policy = _retrieval_policy_helpers()
    if (mode or "code").strip().lower() != "code" or not policy.implementation_query_intent(query):
        return prepared
    query_class = policy.implementation_query_class(query)
    for row in prepared:
        if row.get("_implementation_rank_enriched"):
            continue
        meta = coerce_meta(row)
        row["_meta"] = meta
        row["meta_score"] = meta_score(meta)
        try:
            base_score = float(row.get("rank_score", row.get("rrf", 0.0)) or 0.0)
        except (TypeError, ValueError):
            base_score = 0.0
        policy.enrich_implementation_result(
            row,
            query=query,
            query_class=query_class,
            base_score=base_score,
            meta_boost=0.0,
        )
        row["_implementation_rank_enriched"] = True
    return prepared


def _dedupe_int_list(values, *, limit: int) -> list[int]:
    output: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        if not isinstance(value, int) or value in seen or value < 0 or value >= limit:
            continue
        seen.add(value)
        output.append(value)
    return output


def _normalize_duplicate_pairs(pairs, *, limit: int) -> list[dict]:
    normalized: list[dict] = []
    for pair in pairs or []:
        if not isinstance(pair, dict):
            continue
        left = pair.get("left")
        right = pair.get("right")
        if not isinstance(left, int) or not isinstance(right, int):
            continue
        if left < 0 or right < 0 or left >= limit or right >= limit:
            continue
        normalized.append(dict(pair))
    return normalized


def _normalize_duplicate_groups(groups, *, limit: int) -> list[dict]:
    normalized: list[dict] = []
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        members = _dedupe_int_list(group.get("members") or [], limit=limit)
        if not members:
            continue
        entry = dict(group)
        entry["members"] = members
        if "canonical_candidates" in entry:
            entry["canonical_candidates"] = _dedupe_int_list(
                entry.get("canonical_candidates") or [],
                limit=limit,
            )
        normalized.append(entry)
    return normalized


def _compact_candidate_trace(candidates) -> list[dict]:
    compact: list[dict] = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        compact.append(
            {
                "idx": candidate.get("idx"),
                "group_id": candidate.get("group_id"),
                "kept": candidate.get("kept"),
                "beaten_by": candidate.get("beaten_by"),
                "decision_reason": candidate.get("decision_reason"),
                "query_distinction_score": candidate.get("query_distinction_score"),
                "duplicate_relations": candidate.get("duplicate_relations") or [],
            }
        )
    return compact


def _effective_suppression_policy(
    trace: dict | None,
    telemetry: dict | None,
    experiments: dict,
) -> str:
    if isinstance(trace, dict):
        policy = str(trace.get("suppression_policy") or "exact_only")
    else:
        policy = "exact_only"
    if policy != "exact_only":
        return policy
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    if int(telemetry.get("experimental_suppressions") or 0) > 0:
        return "experimental_non_exact"
    if any(bool(experiments.get(key)) for key in (
        "boilerplate_variant_suppression",
        "canonical_docs_mirror_suppression",
        "helper_clone_suppression",
    )):
        candidates = trace.get("candidates") if isinstance(trace, dict) else []
        for candidate in candidates or []:
            if not isinstance(candidate, dict) or candidate.get("kept") is not False:
                continue
            relations = {str(rel) for rel in (candidate.get("duplicate_relations") or [])}
            reason = str(candidate.get("decision_reason") or "")
            if reason not in {"exact_duplicate_suppressed", "exact_duplicate"} or (
                relations and relations != {"exact_duplicate"}
            ):
                return "experimental_non_exact"
    return policy


def trace_diverse_results(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
    experiments: dict | None = None,
) -> dict:
    results = _prepare_results_for_duplicate_rerank(results, query=query, mode=mode)
    if len(results) < 2:
        keep = list(range(len(results)))
        mode_name = "docs_retrieval" if mode == "docs" else "code_retrieval"
        return {
            "selection": {
                "mode": mode_name,
                "keep_indices": keep,
                "suppressed_indices": [],
                "exact_suppressed_indices": [],
                "group_order": keep,
                "representative_indices": keep,
                "mmr_lambda": 0.78,
                "aspect_lambda": 0.18,
                "selected_aspects": [],
            },
            "candidates": [],
            "telemetry": {
                "mode": mode_name,
                "query_class": "unknown",
                "exact_suppressions": 0,
                "experimental_suppressions": 0,
                "relation_counts": {},
                "group_sizes": [],
                "representative_selection_reasons": {},
                "topk_redundancy_before": 0.0,
                "topk_redundancy_after": 0.0,
                "kept_group_multi_member_count": 0,
                "canonical_doc_preference_success": None,
                "version_sensitive_query": False,
            },
            "suppression_policy": "exact_only",
            "experiments": experiments or {},
        }
    ts_pack = _load_ts_pack()

    trace = getattr(ts_pack, "trace_diverse_texts", None) if ts_pack is not None else None
    if not callable(trace):
        payload = experiments if isinstance(experiments, dict) else duplicate_experiment_flags_from_env(mode)
        fallback = _ts_pack_runtime_call(
            "trace_diverse_texts",
            texts=[
                result.get("content") if isinstance(result.get("content"), str) else ""
                for result in results
            ],
            relevance_scores=[candidate_relevance_score(result) for result in results],
            query=query,
            mode=mode,
            contexts_json=_context_payload(results),
            experiments=payload,
        )
        if isinstance(fallback, dict):
            return fallback
        selection = rerank_diverse_results(results, query=query, mode=mode)
        return {
            "selection": selection,
            "candidates": [],
            "telemetry": {
                "mode": selection.get("mode", "code_retrieval"),
                "query_class": "unknown",
                "exact_suppressions": len(selection.get("exact_suppressed_indices", [])),
                "experimental_suppressions": 0,
                "relation_counts": {},
                "group_sizes": [],
                "representative_selection_reasons": {},
                "topk_redundancy_before": 0.0,
                "topk_redundancy_after": 0.0,
                "kept_group_multi_member_count": 0,
                "canonical_doc_preference_success": None,
                "version_sensitive_query": False,
            },
            "suppression_policy": "exact_only",
            "experiments": payload,
        }

    texts: list[str] = []
    relevance_scores: list[float] = []
    for result in results:
        content = result.get("content")
        texts.append(content if isinstance(content, str) else "")
        relevance_scores.append(candidate_relevance_score(result))

    payload = experiments if isinstance(experiments, dict) else duplicate_experiment_flags_from_env(mode)
    try:
        traced = trace(
            texts,
            relevance_scores,
            query or None,
            mode,
            _context_payload(results),
            json.dumps(payload),
        )
    except Exception:
        selection = rerank_diverse_results(results, query=query, mode=mode)
        return {
            "selection": selection,
            "candidates": [],
            "telemetry": {
                "mode": selection.get("mode", "code_retrieval"),
                "query_class": "unknown",
                "exact_suppressions": len(selection.get("exact_suppressed_indices", [])),
                "experimental_suppressions": 0,
                "relation_counts": {},
                "group_sizes": [],
                "representative_selection_reasons": {},
                "topk_redundancy_before": 0.0,
                "topk_redundancy_after": 0.0,
                "kept_group_multi_member_count": 0,
                "canonical_doc_preference_success": None,
                "version_sensitive_query": False,
            },
            "suppression_policy": "exact_only",
            "experiments": payload,
        }
    if isinstance(traced, dict):
        return traced
    return trace_diverse_results(results[:1], query=query, mode=mode, experiments=payload)


def analyze_near_duplicate_results(results: list[dict], *, query: str = "", mode: str = "code") -> dict:
    results = _prepare_results_for_duplicate_rerank(results, query=query, mode=mode)
    if len(results) < 2:
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "pairs": [],
            "groups": [],
        }
    ts_pack = _load_ts_pack()

    analyze = getattr(ts_pack, "analyze_duplicate_texts", None) if ts_pack is not None else None
    if not callable(analyze):
        fallback = _ts_pack_runtime_call(
            "analyze_duplicate_texts",
            texts=[
                result.get("content") if isinstance(result.get("content"), str) else ""
                for result in results
            ],
            query=query,
            mode=mode,
            contexts_json=_context_payload(results),
        )
        if isinstance(fallback, dict):
            return fallback
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "pairs": [],
            "groups": [],
        }

    texts: list[str] = []
    for result in results:
        content = result.get("content")
        texts.append(content if isinstance(content, str) else "")

    try:
        analysis = analyze(texts, query or None, mode, _context_payload(results))
    except Exception:
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "pairs": [],
            "groups": [],
        }

    return analysis if isinstance(analysis, dict) else {
        "mode": "code_retrieval",
        "keep_indices": list(range(len(results))),
        "suppressed_indices": [],
        "pairs": [],
    }


def rerank_diverse_results(results: list[dict], *, query: str = "", mode: str = "code") -> dict:
    results = _prepare_results_for_duplicate_rerank(results, query=query, mode=mode)
    if len(results) < 2:
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "exact_suppressed_indices": [],
            "group_order": list(range(len(results))),
            "representative_indices": list(range(len(results))),
        }
    ts_pack = _load_ts_pack()
    if ts_pack is None:
        fallback = _ts_pack_runtime_call(
            "rerank_diverse_texts",
            texts=[
                result.get("content") if isinstance(result.get("content"), str) else ""
                for result in results
            ],
            relevance_scores=[candidate_relevance_score(result) for result in results],
            query=query,
            mode=mode,
            contexts_json=_context_payload(results),
        )
        if isinstance(fallback, dict):
            return fallback
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
        }

    rerank = getattr(ts_pack, "rerank_diverse_texts", None)
    if not callable(rerank):
        analysis = analyze_near_duplicate_results(results, query=query, mode=mode)
        return {
            "mode": analysis.get("mode", "code_retrieval"),
            "keep_indices": analysis.get("keep_indices", list(range(len(results)))),
            "suppressed_indices": analysis.get("suppressed_indices", []),
            "exact_suppressed_indices": analysis.get("suppressed_indices", []),
            "group_order": [],
            "representative_indices": analysis.get("keep_indices", list(range(len(results)))),
        }

    texts: list[str] = []
    relevance_scores: list[float] = []
    for result in results:
        content = result.get("content")
        texts.append(content if isinstance(content, str) else "")
        relevance_scores.append(candidate_relevance_score(result))

    try:
        selection = rerank(texts, relevance_scores, query or None, mode, _context_payload(results))
    except Exception:
        analysis = analyze_near_duplicate_results(results, query=query, mode=mode)
        return {
            "mode": analysis.get("mode", "code_retrieval"),
            "keep_indices": analysis.get("keep_indices", list(range(len(results)))),
            "suppressed_indices": analysis.get("suppressed_indices", []),
            "exact_suppressed_indices": analysis.get("suppressed_indices", []),
            "group_order": [],
            "representative_indices": analysis.get("keep_indices", list(range(len(results)))),
        }

    return selection if isinstance(selection, dict) else {
        "mode": "code_retrieval",
        "keep_indices": list(range(len(results))),
        "suppressed_indices": [],
        "exact_suppressed_indices": [],
        "group_order": list(range(len(results))),
        "representative_indices": list(range(len(results))),
    }


def analyze_duplicate_results_contract(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
    analyze_fn=None,
) -> dict:
    prepared = _prepare_results_for_duplicate_rerank(results, query=query, mode=mode)
    analyze = analyze_fn if callable(analyze_fn) else analyze_near_duplicate_results
    analysis = analyze(prepared, query=query, mode=mode)
    keep_indices = _dedupe_int_list(
        analysis.get("keep_indices") or list(range(len(prepared))),
        limit=len(prepared),
    )
    suppressed_indices = _dedupe_int_list(
        analysis.get("suppressed_indices") or [],
        limit=len(prepared),
    )
    return {
        "keep_indices": keep_indices,
        "suppressed_indices": suppressed_indices,
        "pairs": _normalize_duplicate_pairs(analysis.get("pairs"), limit=len(prepared)),
        "groups": _normalize_duplicate_groups(analysis.get("groups"), limit=len(prepared)),
        "mode": analysis.get("mode", "docs_retrieval" if mode == "docs" else "code_retrieval"),
    }


def rerank_retrieval_results_contract(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
    experiments: dict | None = None,
    include_debug: bool = False,
    trace_fn=None,
    analyze_fn=None,
) -> dict:
    prepared = _prepare_results_for_duplicate_rerank(results, query=query, mode=mode)
    payload = merge_duplicate_experiments(mode, experiments)
    trace_call = trace_fn if callable(trace_fn) else trace_diverse_results
    analyze = analyze_fn if callable(analyze_fn) else analyze_near_duplicate_results
    trace = trace_call(prepared, query=query, mode=mode, experiments=payload)
    analysis = analyze(prepared, query=query, mode=mode)

    selection = trace.get("selection") if isinstance(trace, dict) else {}
    telemetry = trace.get("telemetry") if isinstance(trace, dict) else {}
    keep_indices = _dedupe_int_list(
        selection.get("keep_indices") or analysis.get("keep_indices") or list(range(len(prepared))),
        limit=len(prepared),
    )
    suppressed_indices = _dedupe_int_list(
        selection.get("suppressed_indices") or analysis.get("suppressed_indices") or [],
        limit=len(prepared),
    )
    exact_suppressed = _dedupe_int_list(
        selection.get("exact_suppressed_indices") or [],
        limit=len(prepared),
    )
    representative_indices = _dedupe_int_list(
        selection.get("representative_indices") or keep_indices,
        limit=len(prepared),
    )
    group_order = _dedupe_int_list(
        selection.get("group_order") or representative_indices,
        limit=len(prepared),
    )

    contract = {
        "results": [dict(prepared[idx]) for idx in keep_indices],
        "keep_indices": keep_indices,
        "suppressed_indices": suppressed_indices,
        "groups": _normalize_duplicate_groups(analysis.get("groups"), limit=len(prepared)),
        "pairs": _normalize_duplicate_pairs(analysis.get("pairs"), limit=len(prepared)),
        "selection": {
            "mode": selection.get("mode", "docs_retrieval" if mode == "docs" else "code_retrieval"),
            "keep_indices": keep_indices,
            "suppressed_indices": suppressed_indices,
            "exact_suppressed_indices": exact_suppressed,
            "group_order": group_order,
            "representative_indices": representative_indices,
            "mmr_lambda": selection.get("mmr_lambda"),
            "aspect_lambda": selection.get("aspect_lambda"),
            "selected_aspects": selection.get("selected_aspects") or [],
        },
        "telemetry": telemetry if isinstance(telemetry, dict) else {},
        "suppression_policy": _effective_suppression_policy(
            trace if isinstance(trace, dict) else None,
            telemetry if isinstance(telemetry, dict) else None,
            payload,
        ),
        "experiments": trace.get("experiments", payload) if isinstance(trace, dict) else payload,
    }
    if include_debug:
        candidates = trace.get("candidates") if isinstance(trace, dict) else []
        contract["trace"] = _compact_candidate_trace(candidates)
        contract["candidates"] = candidates if isinstance(candidates, list) else []
    return contract


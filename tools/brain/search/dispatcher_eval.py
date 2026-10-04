"""Benchmark dispatcher candidate recall vs ranking/rescue stages."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _semantic_contract import (
    FOCUSED_DISPATCHER_ANCHOR_CAPABILITY,
    FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION,
    SEMANTIC_CONTRACT_VERSION,
    has_focused_dispatcher_anchor_contract,
)

try:
    from tools.brain.search import semantic_helpers as sem_helpers
except Exception:
    _SEM_HELPERS_PATH = ROOT / "tools" / "brain" / "search" / "semantic_helpers.py"
    _spec = importlib.util.spec_from_file_location("dispatcher_eval_semantic_helpers", _SEM_HELPERS_PATH)
    sem_helpers = importlib.util.module_from_spec(_spec)
    assert _spec is not None and _spec.loader is not None
    _spec.loader.exec_module(sem_helpers)


BENCHMARK_PATH = ROOT / "benchmarks" / "dispatcher_selection_goldens.json"


def load_benchmarks(path: str | None = None) -> list[dict]:
    raw = Path(path) if path else BENCHMARK_PATH
    with raw.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    return cases if isinstance(cases, list) else []


def _normalize_meta(meta: object) -> dict:
    payload = dict(meta or {}) if isinstance(meta, dict) else {}
    payload.setdefault("semantic_contract_version", SEMANTIC_CONTRACT_VERSION)
    return payload


def _normalize_rows(rows: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for index, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        item = copy.deepcopy(row)
        item["metadata"] = _normalize_meta(item.get("metadata"))
        item.setdefault("chunk_index", index)
        item.setdefault("project_id", "bench")
        item.setdefault("rrf", 0.0)
        normalized.append(item)
    return normalized


def _is_expected_dispatcher(row: dict, expected: dict) -> bool:
    file_path = str(expected.get("file_path") or "")
    symbol = str(expected.get("declared_symbol") or "").strip().lower()
    if file_path and row.get("file_path") != file_path:
        return False
    if not symbol:
        return True
    meta = sem_helpers.coerce_meta(row)
    declared = {
        str(value).strip().lower()
        for value in (meta.get("declared_symbols") or [])
        if str(value).strip()
    }
    return symbol in declared


def _stage_snapshot(rows: list[dict], expected: dict) -> dict:
    match_indices = [idx for idx, row in enumerate(rows) if _is_expected_dispatcher(row, expected)]
    top = rows[0] if rows else None
    contract_match_indices = [
        idx for idx, row in enumerate(rows) if _is_expected_dispatcher(row, expected) and has_focused_dispatcher_anchor_contract(
            sem_helpers.coerce_meta(row)
        )
    ]
    top_meta = sem_helpers.coerce_meta(top) if top else {}
    return {
        "candidate_hit": bool(match_indices),
        "top_hit": bool(top and _is_expected_dispatcher(top, expected)),
        "match_indices": match_indices,
        "contract_candidate_hit": bool(contract_match_indices),
        "contract_top_hit": bool(
            top and _is_expected_dispatcher(top, expected) and has_focused_dispatcher_anchor_contract(top_meta)
        ),
        "contract_match_indices": contract_match_indices,
        "top_file_path": top.get("file_path") if isinstance(top, dict) else None,
        "top_declared_symbols": list(top_meta.get("declared_symbols") or []) if top else [],
        "top_dispatcher_anchor_contract_version": (
            top_meta.get("focused_dispatcher_anchor_contract_version") if top else None
        ),
    }


def _enrich_rows(rows: list[dict], query: str) -> list[dict]:
    query_class = sem_helpers.implementation_query_class(query)
    enriched: list[dict] = []
    for row in rows:
        item = copy.deepcopy(row)
        item["_meta"] = sem_helpers.coerce_meta(item)
        item["meta_score"] = sem_helpers.meta_score(item["_meta"])
        try:
            base_score = float(item.get("rrf", item.get("rank_score", 0.0)) or 0.0)
        except (TypeError, ValueError):
            base_score = 0.0
        sem_helpers.enrich_implementation_result(
            item,
            query=query,
            query_class=query_class,
            base_score=base_score,
            meta_boost=0.0,
        )
        enriched.append(item)
    return enriched


def _drop_profile_results(rows: list[dict], query: str) -> list[dict]:
    if not sem_helpers.implementation_query_prefers_dispatchers(query):
        return rows
    exact_identifiers = sem_helpers.implementation_query_exact_identifiers(query)
    symbols = sem_helpers.implementation_query_symbols(query)
    if "profile" in symbols or "profile" in exact_identifiers:
        return rows
    non_profile_rows: list[dict] = []
    for row in rows:
        meta = sem_helpers.coerce_meta(row)
        roles = sem_helpers.implementation_file_roles(meta)
        chunk_role = sem_helpers.implementation_chunk_role(meta, row.get("file_path"))
        norm = (row.get("file_path") or "").replace("\\", "/").lower()
        if "/profiles/" in norm or "profile_surface" in roles or chunk_role == "profile_definition":
            continue
        non_profile_rows.append(row)
    return non_profile_rows or rows


def _strong_dispatcher_match(row: dict, query: str) -> bool:
    exact_identifiers = sem_helpers.implementation_query_exact_identifiers(query)
    meta = sem_helpers.coerce_meta(row)
    declared_symbols = {
        str(symbol).strip().lower()
        for symbol in (meta.get("declared_symbols") or [])
        if str(symbol).strip()
    }
    if int(row.get("implementation_dispatcher_priority", 0) or 0) < 5:
        return False
    return bool(declared_symbols & exact_identifiers)


def _rank_semantic_candidates(rows: list[dict], query: str) -> list[dict]:
    ranked = _enrich_rows(rows, query)
    ranked.sort(key=sem_helpers.implementation_rank_tuple)
    ranked = _drop_profile_results(ranked, query)
    ranked = sem_helpers.dedupe_files(ranked)
    ranked.sort(key=sem_helpers.implementation_rank_tuple)
    return ranked


def _apply_dispatcher_rescue(
    ranked_rows: list[dict],
    rescue_rows: list[dict],
    query: str,
) -> tuple[list[dict], bool]:
    final_rows = list(ranked_rows)
    rescue_applied = False
    if not rescue_rows or not sem_helpers.implementation_query_prefers_dispatchers(query):
        rescue_rows = []
    rescue_ranked = _rank_semantic_candidates(rescue_rows, query)
    rescue_matches = [row for row in rescue_ranked if _strong_dispatcher_match(row, query)]
    if rescue_matches:
        rescue_applied = True
        replacement_files = {(row.get("project_id"), row.get("file_path")) for row in rescue_matches}
        final_rows = [
            row
            for row in final_rows
            if (row.get("project_id"), row.get("file_path")) not in replacement_files
        ]
        final_rows.extend(rescue_matches)
    final_rows = _drop_profile_results(final_rows, query)
    strong_dispatchers = [row for row in final_rows if _strong_dispatcher_match(row, query)]
    other_results = [row for row in final_rows if not _strong_dispatcher_match(row, query)]
    if strong_dispatchers:
        strong_dispatchers.sort(key=sem_helpers.implementation_rank_tuple)
        other_results.sort(key=sem_helpers.implementation_rank_tuple)
        final_rows = strong_dispatchers + other_results
    else:
        final_rows.sort(key=sem_helpers.implementation_rank_tuple)
    exact_dispatcher_matches = [row for row in final_rows if _strong_dispatcher_match(row, query)]
    if exact_dispatcher_matches:
        exact_dispatcher_matches.sort(key=sem_helpers.implementation_rank_tuple)
        best_dispatcher = exact_dispatcher_matches[0]
        final_rows = [
            best_dispatcher,
            *[
                row
                for row in final_rows
                if not (
                    row.get("project_id") == best_dispatcher.get("project_id")
                    and row.get("file_path") == best_dispatcher.get("file_path")
                    and row.get("chunk_index") == best_dispatcher.get("chunk_index")
                )
            ],
        ]
    return sem_helpers.dedupe_files(final_rows), rescue_applied


def _diagnose_case(stages: dict[str, dict], *, rescue_applied: bool) -> tuple[str, str]:
    semantic = stages["semantic_candidates"]
    ranked = stages["implementation_ranking"]
    final = stages["final_dispatcher_selection"]
    if not semantic["candidate_hit"]:
        if final["top_hit"]:
            return "final_dispatcher_selection", "semantic_recall_missing"
        return "none", "semantic_recall_missing"
    if semantic["top_hit"]:
        return "semantic_candidates", "no_intervention_needed"
    if ranked["top_hit"]:
        return "implementation_ranking", "ranking_fixed"
    if final["top_hit"]:
        if rescue_applied:
            return "final_dispatcher_selection", "rescue_required"
        return "final_dispatcher_selection", "final_promotion_needed"
    return "none", "unresolved_after_rescue"


def evaluate_case(case: dict) -> dict:
    query = str(case.get("query") or "")
    expected = case.get("expected") or {}
    semantic_candidates = _normalize_rows(case.get("semantic_candidates") or [])
    rescue_candidates = _normalize_rows(case.get("rescue_candidates") or [])
    ranked_rows = _rank_semantic_candidates(semantic_candidates, query)
    final_rows, rescue_applied = _apply_dispatcher_rescue(ranked_rows, rescue_candidates, query)
    stages = {
        "semantic_candidates": _stage_snapshot(semantic_candidates, expected),
        "implementation_ranking": _stage_snapshot(ranked_rows, expected),
        "final_dispatcher_selection": _stage_snapshot(final_rows, expected),
    }
    first_success_stage, diagnosis = _diagnose_case(stages, rescue_applied=rescue_applied)
    return {
        "id": case.get("id"),
        "query": query,
        "expected": expected,
        "stages": stages,
        "rescue_applied": rescue_applied,
        "dispatcher_anchor_contract": {
            "capability": FOCUSED_DISPATCHER_ANCHOR_CAPABILITY,
            "version": FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION,
            "semantic_candidates_match_has_contract": stages["semantic_candidates"]["contract_candidate_hit"],
            "implementation_ranking_top_has_contract": stages["implementation_ranking"]["contract_top_hit"],
            "final_top_has_contract": stages["final_dispatcher_selection"]["contract_top_hit"],
        },
        "first_success_stage": first_success_stage,
        "diagnosis": diagnosis,
    }


def evaluate_benchmarks(path: str | None = None) -> dict:
    cases = [evaluate_case(case) for case in load_benchmarks(path)]
    summary = {
        "total_cases": len(cases),
        "dispatcher_anchor_contract_capability": FOCUSED_DISPATCHER_ANCHOR_CAPABILITY,
        "dispatcher_anchor_contract_version": FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION,
        "semantic_candidate_hit_rate": 0.0,
        "semantic_candidate_contract_hit_rate": 0.0,
        "semantic_top_hit_rate": 0.0,
        "implementation_ranking_top_hit_rate": 0.0,
        "implementation_ranking_contract_top_hit_rate": 0.0,
        "final_dispatcher_selection_top_hit_rate": 0.0,
        "final_dispatcher_selection_contract_top_hit_rate": 0.0,
        "diagnosis_counts": {},
    }
    if cases:
        total = float(len(cases))
        summary["semantic_candidate_hit_rate"] = sum(
            1.0 for case in cases if case["stages"]["semantic_candidates"]["candidate_hit"]
        ) / total
        summary["semantic_candidate_contract_hit_rate"] = sum(
            1.0 for case in cases if case["stages"]["semantic_candidates"]["contract_candidate_hit"]
        ) / total
        summary["semantic_top_hit_rate"] = sum(
            1.0 for case in cases if case["stages"]["semantic_candidates"]["top_hit"]
        ) / total
        summary["implementation_ranking_top_hit_rate"] = sum(
            1.0 for case in cases if case["stages"]["implementation_ranking"]["top_hit"]
        ) / total
        summary["implementation_ranking_contract_top_hit_rate"] = sum(
            1.0 for case in cases if case["stages"]["implementation_ranking"]["contract_top_hit"]
        ) / total
        summary["final_dispatcher_selection_top_hit_rate"] = sum(
            1.0 for case in cases if case["stages"]["final_dispatcher_selection"]["top_hit"]
        ) / total
        summary["final_dispatcher_selection_contract_top_hit_rate"] = sum(
            1.0 for case in cases if case["stages"]["final_dispatcher_selection"]["contract_top_hit"]
        ) / total
        diagnosis_counts: dict[str, int] = {}
        for case in cases:
            diagnosis = str(case.get("diagnosis") or "unknown")
            diagnosis_counts[diagnosis] = diagnosis_counts.get(diagnosis, 0) + 1
        summary["diagnosis_counts"] = diagnosis_counts
    return {
        "summary": summary,
        "cases": cases,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate_benchmarks(), indent=2, sort_keys=True))

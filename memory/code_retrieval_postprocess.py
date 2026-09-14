"""Post-processing for code retrieval ranking, duplicate handling, and telemetry."""

from __future__ import annotations

import os

from memory import retrieval_duplicates
from memory import retrieval_metadata
from memory import retrieval_policy as sem_helpers
from memory import retrieval_telemetry
from runtime_logging import debug_log


async def postprocess_code_results(
    all_results: list[dict],
    *,
    query: str,
    impl_intent: bool,
    impl_query_class: str,
    dedupe_files: bool,
    include_debug: bool,
    path_hints: list[str],
) -> tuple[list[dict], dict | None, bool, bool]:
    duplicate_trace_enabled = include_debug or os.getenv(
        "LM_PROXY_DUPLICATE_TRACE", "0"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    duplicate_telemetry_enabled = retrieval_telemetry.duplicate_telemetry_enabled()
    duplicate_experiments = retrieval_telemetry.duplicate_experiment_flags_with_query_class(
        "code",
        impl_query_class if impl_intent else None,
    )
    duplicate_trace: dict | None = None

    if dedupe_files:
        pre_duplicate_results = list(all_results)
        if duplicate_trace_enabled or duplicate_telemetry_enabled or any(duplicate_experiments.values()):
            duplicate_trace = retrieval_duplicates.rerank_retrieval_results_contract(
                all_results,
                query=query,
                mode="code",
                experiments=duplicate_experiments,
                include_debug=include_debug or duplicate_trace_enabled,
            )
            reranked = duplicate_trace.get("results") if isinstance(duplicate_trace, dict) else None
            if isinstance(reranked, list) and reranked:
                all_results = reranked
        if impl_intent:
            all_results.sort(key=sem_helpers.implementation_rank_tuple)
        all_results = sem_helpers.dedupe_files(all_results)
        pre_dispatcher_recovery_results = list(all_results)
    else:
        pre_duplicate_results = list(all_results)
        pre_dispatcher_recovery_results = list(all_results)

    if impl_intent:
        all_results.sort(key=sem_helpers.implementation_rank_tuple)
        dispatcher_rescue_applied = False
        if (
            sem_helpers.implementation_query_prefers_dispatchers(query)
            and "profile" not in sem_helpers.implementation_query_symbols(query)
            and "profile" not in sem_helpers.implementation_query_exact_identifiers(query)
        ):
            non_profile_results: list[dict] = []
            for r in all_results:
                meta = retrieval_metadata.coerce_meta(r)
                if sem_helpers.implementation_is_profile_candidate(r.get("file_path"), meta):
                    continue
                non_profile_results.append(r)
            if non_profile_results:
                all_results = non_profile_results
        if sem_helpers.implementation_query_prefers_dispatchers(query) and path_hints:
            pass
        dispatcher_contract_trace = sem_helpers.dispatcher_contract_telemetry(
            query=query,
            query_class=impl_query_class,
            semantic_candidates=pre_duplicate_results,
            ranked_candidates=pre_dispatcher_recovery_results,
            final_results=all_results,
            rescue_applied=dispatcher_rescue_applied,
        )
        if dispatcher_contract_trace:
            retrieval_telemetry.append_dispatcher_telemetry_event(
                dispatcher_contract_trace,
                query=query,
                tool="search_codebase",
                topic="",
            )
            debug_log(
                "dispatcher_contract_telemetry",
                query=query,
                query_class=dispatcher_contract_trace.get("query_class"),
                diagnosis=dispatcher_contract_trace.get("diagnosis"),
                rescue_applied=dispatcher_contract_trace.get("rescue_applied"),
                exact_identifiers=dispatcher_contract_trace.get("exact_identifiers"),
                semantic_exact_match_count=dispatcher_contract_trace.get("semantic_exact_match_count"),
                semantic_contract_match_count=dispatcher_contract_trace.get("semantic_contract_match_count"),
                ranked_exact_match_count=dispatcher_contract_trace.get("ranked_exact_match_count"),
                ranked_contract_match_count=dispatcher_contract_trace.get("ranked_contract_match_count"),
                final_exact_match_count=dispatcher_contract_trace.get("final_exact_match_count"),
                final_contract_match_count=dispatcher_contract_trace.get("final_contract_match_count"),
                semantic_top=dispatcher_contract_trace.get("semantic_top"),
                ranked_top=dispatcher_contract_trace.get("ranked_top"),
                final_top=dispatcher_contract_trace.get("final_top"),
                dispatcher_anchor_contract_capability=dispatcher_contract_trace.get(
                    "dispatcher_anchor_contract_capability"
                ),
                dispatcher_anchor_contract_version=dispatcher_contract_trace.get(
                    "dispatcher_anchor_contract_version"
                ),
            )
        pre_routing_partition_results = list(all_results)
        routing_partition_applied = False
        routing_signal_trace = sem_helpers.routing_signal_telemetry(
            query=query,
            query_class=impl_query_class,
            semantic_candidates=pre_duplicate_results,
            ranked_candidates=pre_routing_partition_results,
            final_results=all_results,
            partition_applied=routing_partition_applied,
        )
        if routing_signal_trace:
            retrieval_telemetry.append_routing_telemetry_event(
                routing_signal_trace,
                query=query,
                tool="search_codebase",
                topic="",
            )
            debug_log(
                "routing_signal_telemetry",
                query=query,
                query_class=routing_signal_trace.get("query_class"),
                diagnosis=routing_signal_trace.get("diagnosis"),
                partition_applied=routing_signal_trace.get("partition_applied"),
                semantic_signal_match_count=routing_signal_trace.get("semantic_signal_match_count"),
                ranked_signal_match_count=routing_signal_trace.get("ranked_signal_match_count"),
                final_signal_match_count=routing_signal_trace.get("final_signal_match_count"),
                semantic_top=routing_signal_trace.get("semantic_top"),
                ranked_top=routing_signal_trace.get("ranked_top"),
                final_top=routing_signal_trace.get("final_top"),
            )
    return all_results, duplicate_trace, duplicate_trace_enabled, duplicate_telemetry_enabled

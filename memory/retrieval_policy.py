"""Shared retrieval ranking, filtering, deduplication, and rendering policy."""

from __future__ import annotations
from memory.retrieval_metadata import (
    attach_cargo_crate_meta,
    cap_per_dir,
    cap_per_file,
    cargo_manifest_dir,
    coerce_meta,
    filter_by_cargo_crate,
    format_meta,
    match_cargo_crate,
    meta_score,
    passes_filters,
    path_allowed,
    render_results,
)
from memory import retrieval_duplicates as _retrieval_duplicates
from memory.retrieval_duplicates import (
    analyze_near_duplicate_results,
    candidate_relevance_score,
    rerank_diverse_results,
    trace_diverse_results,
)
from memory.retrieval_contracts import (
    compact_duplicate_analysis,
    compact_implementation_ranking_trace,
    compact_rerank_contract,
    result_label,
    summarize_trace_for_debug,
)
from memory.retrieval_intent import (
    implementation_expected_runtime_entrypoint_paths,
    implementation_inferred_filename_hints,
    implementation_intent_policy,
    implementation_noise_exclude_patterns,
    implementation_provider_query_tokens,
    implementation_query_accepts_generated_surfaces,
    implementation_query_class,
    implementation_query_exact_identifiers,
    implementation_query_intent,
    implementation_query_prefers_command_definition,
    implementation_query_prefers_dispatchers,
    implementation_query_prefers_provider_wiring,
    implementation_query_prefers_request_routing,
    implementation_query_prefers_runtime_main_entrypoint,
    implementation_query_prefers_supporting_context,
    implementation_query_relaxes_dir_cap,
    implementation_support_surface_penalties,
    implementation_usage_surface_bonus,
    query_class_prefers_definitions,
    query_class_prefers_usage,
    usage_query_prefers_example_results,
    usage_query_prefers_test_results,
)
from memory.retrieval_scoring import (
    implementation_api_context_hit,
    implementation_api_entrypoint_hit,
    implementation_basename_token_hit,
    implementation_command_definition_priority,
    implementation_controller_entity_hit,
    implementation_declared_symbol_hit,
    implementation_definition_hit,
    implementation_dispatcher_contract_priority,
    implementation_dispatcher_priority,
    implementation_exact_identifier_hit,
    implementation_exact_member_usage_hit,
    implementation_exact_member_usage_site_hit,
    implementation_exact_signature_symbol_hit,
    implementation_export_hit,
    implementation_facade_surface_hit,
    implementation_path_hint_hit,
    implementation_provider_wiring_penalty,
    implementation_provider_wiring_priority,
    implementation_reexport_surface_hit,
    implementation_request_handler_priority,
    implementation_routing_priority,
    implementation_runtime_main_entrypoint_hit,
    implementation_runtime_main_entrypoint_hit_with_meta,
    implementation_server_infra_penalty,
    implementation_symbol_hit,
    implementation_view_body_priority,
)
from memory.retrieval_semantics import (
    apply_result_surface_flags,
    implementation_chunk_role,
    implementation_declared_symbol_roles,
    implementation_declares_callable,
    implementation_file_roles,
    implementation_has_file_roles,
    implementation_is_profile_candidate,
    implementation_member_usages,
    implementation_node_types,
)
from memory.retrieval_query import (
    implementation_query_definition_subject_identifiers,
    implementation_query_member_exprs,
    implementation_query_path_hints,
    implementation_query_symbols,
)
from memory.retrieval_rank_policy import (
    NODE_TYPE_POLICY_DEFINITION as NODE_TYPE_POLICY_DEFINITION,
    NODE_TYPE_POLICY_EXPLANATION as NODE_TYPE_POLICY_EXPLANATION,
    NODE_TYPE_POLICY_IMPLEMENTATION as NODE_TYPE_POLICY_IMPLEMENTATION,
    NODE_TYPE_POLICY_USAGE as NODE_TYPE_POLICY_USAGE,
    ROLE_POLICY_DEFINITION as ROLE_POLICY_DEFINITION,
    ROLE_POLICY_EXPLANATION as ROLE_POLICY_EXPLANATION,
    ROLE_POLICY_IMPLEMENTATION as ROLE_POLICY_IMPLEMENTATION,
    ROLE_POLICY_USAGE as ROLE_POLICY_USAGE,
    implementation_node_type_policy,
    implementation_node_type_priority,
    implementation_node_type_score,
    implementation_result_role,
    implementation_role_policy,
    implementation_role_priority,
    implementation_role_score,
)
from memory.retrieval_surfaces import (
    implementation_rank_tuple,
    is_doc_like_path as is_doc_like_path,
    is_generated_implementation_surface_path,
    is_low_signal_binding_surface_path as is_low_signal_binding_surface_path,
    is_low_signal_parser_data_path,
    is_low_signal_support_path,
    is_usage_heavy_path,
)
from memory.retrieval_telemetry import (
    append_dispatcher_telemetry_event,
    append_duplicate_telemetry_event,
    append_routing_telemetry_event,
    dispatcher_contract_telemetry,
    dispatcher_telemetry_enabled,
    duplicate_experiment_flags_from_env,
    duplicate_experiment_flags_with_query_class,
    duplicate_telemetry_enabled,
    merge_duplicate_experiments,
    routing_signal_telemetry,
    routing_telemetry_enabled,
)

__all__ = [
    "append_dispatcher_telemetry_event",
    "append_duplicate_telemetry_event",
    "append_routing_telemetry_event",
    "trace_diverse_results",
    "rerank_retrieval_results_contract",
    "rerank_diverse_results",
    "collapse_near_duplicate_results",
    "candidate_relevance_score",
    "analyze_near_duplicate_results",
    "analyze_duplicate_results_contract",
    "attach_cargo_crate_meta",
    "cap_per_dir",
    "cap_per_file",
    "cargo_manifest_dir",
    "coerce_meta",
    "compact_duplicate_analysis",
    "compact_implementation_ranking_trace",
    "compact_rerank_contract",
    "dispatcher_telemetry_enabled",
    "dispatcher_contract_telemetry",
    "duplicate_experiment_flags_from_env",
    "duplicate_experiment_flags_with_query_class",
    "duplicate_telemetry_enabled",
    "filter_by_cargo_crate",
    "format_meta",
    "implementation_expected_runtime_entrypoint_paths",
    "implementation_exact_member_usage_site_hit",
    "implementation_inferred_filename_hints",
    "implementation_node_type_policy",
    "implementation_node_type_priority",
    "implementation_node_type_score",
    "implementation_member_usages",
    "implementation_noise_exclude_patterns",
    "implementation_is_profile_candidate",
    "implementation_provider_query_tokens",
    "implementation_query_definition_subject_identifiers",
    "implementation_query_exact_identifiers",
    "implementation_query_member_exprs",
    "implementation_query_path_hints",
    "implementation_query_intent",
    "implementation_query_prefers_command_definition",
    "implementation_query_prefers_dispatchers",
    "implementation_query_prefers_provider_wiring",
    "implementation_query_prefers_runtime_main_entrypoint",
    "implementation_query_prefers_supporting_context",
    "implementation_query_relaxes_dir_cap",
    "implementation_query_symbols",
    "implementation_result_role",
    "implementation_role_policy",
    "implementation_role_priority",
    "implementation_role_score",
    "implementation_runtime_main_entrypoint_hit",
    "implementation_declared_symbol_roles",
    "implementation_has_file_roles",
    "is_doc_like_path",
    "is_generated_implementation_surface_path",
    "is_low_signal_binding_surface_path",
    "is_low_signal_parser_data_path",
    "is_low_signal_support_path",
    "match_cargo_crate",
    "meta_score",
    "merge_duplicate_experiments",
    "passes_filters",
    "path_allowed",
    "result_label",
    "render_results",
    "routing_telemetry_enabled",
    "routing_signal_telemetry",
    "summarize_trace_for_debug",
    "usage_query_prefers_example_results",
    "usage_query_prefers_test_results",
]


_context_payload = _retrieval_duplicates._context_payload


def collapse_near_duplicate_results(results: list[dict], *, query: str = "", mode: str = "code") -> list[dict]:
    return _retrieval_duplicates.collapse_near_duplicate_results(
        results,
        query=query,
        mode=mode,
        rerank_fn=rerank_retrieval_results_contract,
    )


def analyze_duplicate_results_contract(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
) -> dict:
    return _retrieval_duplicates.analyze_duplicate_results_contract(
        results,
        query=query,
        mode=mode,
        analyze_fn=analyze_near_duplicate_results,
    )


def rerank_retrieval_results_contract(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
    experiments: dict | None = None,
    include_debug: bool = False,
) -> dict:
    return _retrieval_duplicates.rerank_retrieval_results_contract(
        results,
        query=query,
        mode=mode,
        experiments=experiments,
        include_debug=include_debug,
        trace_fn=trace_diverse_results,
        analyze_fn=analyze_near_duplicate_results,
    )


def enrich_implementation_result(
    result: dict,
    *,
    query: str,
    query_class: str,
    base_score: float,
    meta_boost: float = 0.0,
    base_bonus: float = 0.0,
) -> dict:
    meta = coerce_meta(result)
    file_roles = implementation_file_roles(meta)
    result["_meta"] = meta
    apply_result_surface_flags(result)
    result["implementation_callable_priority"] = int(
        implementation_declares_callable(meta, result.get("content", ""))
    )
    result["implementation_symbol_hit"] = implementation_symbol_hit(meta, query)
    result["implementation_declared_symbol_hit"] = implementation_declared_symbol_hit(meta, query)
    result["implementation_exact_identifier_hit"] = implementation_exact_identifier_hit(meta, query)
    result["implementation_path_hint_hit"] = implementation_path_hint_hit(
        result.get("file_path"),
        query=query,
    )
    if query_class_prefers_usage(query_class):
        result["implementation_basename_token_hit"] = 0
    else:
        result["implementation_basename_token_hit"] = implementation_basename_token_hit(
            result.get("file_path"),
            query,
        )
    result["implementation_dispatcher_priority"] = implementation_dispatcher_priority(
        meta,
        result.get("file_path"),
        query,
    )
    result["implementation_command_definition_priority"] = implementation_command_definition_priority(
        meta,
        result.get("file_path"),
        query,
    )
    result["implementation_provider_wiring_priority"] = implementation_provider_wiring_priority(
        meta,
        result.get("file_path"),
        query,
    )
    result["implementation_dispatcher_contract_priority"] = implementation_dispatcher_contract_priority(
        meta,
        query,
    )
    result["implementation_routing_priority"] = implementation_routing_priority(
        meta,
        result.get("file_path"),
        query,
    )
    result["implementation_request_handler_priority"] = implementation_request_handler_priority(
        meta,
        result.get("file_path"),
        query,
    )
    result["implementation_controller_entity_hit"] = implementation_controller_entity_hit(
        meta,
        result.get("file_path"),
        query,
    )
    chunk_role = implementation_chunk_role(meta)
    result["implementation_chunk_role"] = chunk_role
    result["implementation_exact_member_usage_hit"] = implementation_exact_member_usage_hit(
        result.get("content", ""),
        query,
        meta,
    )
    result["implementation_member_usage_priority"] = int(
        result.get("implementation_exact_member_usage_hit", 0) or 0
    )
    result["implementation_definition_hit"] = implementation_definition_hit(result.get("content", ""), query)
    result["implementation_exact_signature_symbol_hit"] = implementation_exact_signature_symbol_hit(
        result.get("content", ""),
        query,
    )
    result["implementation_export_hit"] = implementation_export_hit(
        result.get("content", ""),
        result.get("file_path"),
        meta,
    )
    result["implementation_reexport_surface_hit"] = implementation_reexport_surface_hit(
        result.get("content", ""),
        result.get("file_path"),
        meta,
        definition_hit=int(result.get("implementation_definition_hit", 0) or 0),
        signature_hit=int(result.get("implementation_exact_signature_symbol_hit", 0) or 0),
    )
    result["implementation_api_context_hit"] = implementation_api_context_hit(meta)
    result["implementation_view_body_priority"] = implementation_view_body_priority(
        meta,
        query,
        query_class,
    )
    result["implementation_facade_surface_hit"] = implementation_facade_surface_hit(
        result.get("file_path"),
        meta,
        symbol_hit=int(result.get("implementation_symbol_hit", 0) or 0),
        declared_symbol_hit=int(result.get("implementation_declared_symbol_hit", 0) or 0),
        definition_hit=int(result.get("implementation_definition_hit", 0) or 0),
        signature_hit=int(result.get("implementation_exact_signature_symbol_hit", 0) or 0),
        export_hit=int(result.get("implementation_export_hit", 0) or 0),
        api_context_hit=int(result.get("implementation_api_context_hit", 0) or 0),
    )
    result["implementation_api_entrypoint_hit"] = implementation_api_entrypoint_hit(
        result.get("file_path", ""),
        max(
            int(result.get("implementation_definition_hit", 0) or 0),
            int(result.get("implementation_declared_symbol_hit", 0) or 0),
        ),
        meta,
    )
    result["implementation_runtime_main_entrypoint_hit"] = implementation_runtime_main_entrypoint_hit_with_meta(
        result.get("file_path", ""),
        query,
        meta,
    )
    result["implementation_usage_heavy_penalty"] = (
        query_class_prefers_definitions(query_class)
        and is_usage_heavy_path(result.get("file_path", ""), file_roles)
    )
    result["implementation_node_type_priority"] = implementation_node_type_priority(meta, query_class)
    result["implementation_node_type_score"] = implementation_node_type_score(meta, query_class)
    role = implementation_result_role(
        result.get("file_path"),
        meta,
        definition_hit=int(result.get("implementation_definition_hit", 0) or 0),
        export_hit=int(result.get("implementation_export_hit", 0) or 0),
        api_entrypoint_hit=int(result.get("implementation_api_entrypoint_hit", 0) or 0),
    )
    if (
        implementation_query_prefers_request_routing(query)
        and "controller_surface" in file_roles
        and chunk_role in {"definition", "context"}
        and (
            int(result.get("implementation_controller_entity_hit", 0) or 0) > 0
            or int(result.get("implementation_request_handler_priority", 0) or 0) > 0
            or int(result.get("implementation_routing_priority", 0) or 0) > 0
        )
    ):
        role = "public_api_definition"
    if (
        query_class in {"api_definition_lookup", "symbol_lookup"}
        and int(result.get("implementation_reexport_surface_hit", 0) or 0) > 0
        and role in {"public_api_definition", "canonical_definition", "internal_implementation"}
    ):
        role = "supporting_context"
    if (
        query_class in {"api_definition_lookup", "symbol_lookup"}
        and int(result.get("implementation_facade_surface_hit", 0) or 0) > 0
        and role in {"public_api_definition", "canonical_definition", "internal_implementation"}
    ):
        role = "supporting_context"
    result["implementation_role"] = role
    result["implementation_role_priority"] = implementation_role_priority(role, query_class)
    result["implementation_role_score"] = implementation_role_score(role, query_class)

    parser_data_penalty = 0.08 if result["low_signal_parser_data"] else 0.0
    binding_surface_penalty = 0.06 if result["low_signal_binding_surface"] else 0.0
    if result["generated_implementation_surface"]:
        if implementation_query_accepts_generated_surfaces(query):
            generated_surface_penalty = 0.0
        elif query_class in {"implementation_search", "implementation_explanation", "api_definition_lookup"}:
            generated_surface_penalty = 0.18
        else:
            generated_surface_penalty = 0.08
    else:
        generated_surface_penalty = 0.0
    support_surface_penalties = implementation_support_surface_penalties(
        result.get("file_path"),
        meta=meta,
        query=query,
        query_class=query_class,
        doc_like=bool(result["doc_like"]),
        low_signal_support=bool(result["low_signal_support_path"]),
        usage_heavy=bool(result["implementation_usage_heavy_penalty"]),
    )
    provider_wiring_penalty = implementation_provider_wiring_penalty(
        meta,
        result.get("file_path"),
        query,
    )
    doc_penalty = float(support_surface_penalties["doc_penalty"])
    support_path_penalty = float(support_surface_penalties["support_path_penalty"])
    usage_penalty = float(support_surface_penalties["usage_penalty"])
    intent_policy = implementation_intent_policy(query, query_class)
    symbol_bonus = 0.015 * min(int(result.get("implementation_symbol_hit", 0) or 0), 2)
    declared_symbol_bonus = 0.0
    exact_identifier_bonus = 0.0
    path_hint_bonus = 0.0
    basename_token_bonus = 0.0
    view_body_bonus = 0.0
    dispatcher_bonus = 0.0
    command_definition_bonus = 0.0
    provider_wiring_bonus = 0.0
    routing_bonus = 0.0
    request_handler_bonus = 0.0
    controller_entity_bonus = 0.0
    runtime_main_bonus = 0.0
    member_usage_bonus = 0.0
    chunk_role_bonus = 0.0
    callable_bonus = 0.0
    if query_class_prefers_usage(query_class):
        member_usage_bonus = float(intent_policy["member_usage_bonus_usage"]) * min(
            int(result.get("implementation_exact_member_usage_hit", 0) or 0), 2
        )
        exact_member_hits = int(result.get("implementation_exact_member_usage_hit", 0) or 0)
        chunk_role_bonus = implementation_usage_surface_bonus(
            chunk_role=chunk_role,
            query=query,
            query_class=query_class,
            exact_member_hits=exact_member_hits,
        )
    elif query_class == "implementation_search":
        member_usage_bonus = float(intent_policy["member_usage_bonus_search"]) * min(
            int(result.get("implementation_exact_member_usage_hit", 0) or 0), 2
        )
    path_hint_hits = int(result.get("implementation_path_hint_hit", 0) or 0)
    if path_hint_hits > 0:
        if query_class == "implementation_search":
            path_hint_bonus = float(intent_policy["path_hint_bonus_search"]) * min(path_hint_hits, 1)
        elif query_class_prefers_definitions(query_class):
            path_hint_bonus = float(intent_policy["path_hint_bonus_definition"]) * min(path_hint_hits, 1)
    basename_token_hits = int(result.get("implementation_basename_token_hit", 0) or 0)
    if basename_token_hits > 0 and query_class in {"implementation_search", "implementation_explanation", "api_definition_lookup"}:
        basename_token_bonus = 0.03 * min(basename_token_hits, 2)
    view_body_hits = int(result.get("implementation_view_body_priority", 0) or 0)
    if view_body_hits > 0:
        view_body_bonus = 0.03 * min(view_body_hits, 3)
    command_definition_hits = int(result.get("implementation_command_definition_priority", 0) or 0)
    if command_definition_hits > 0 and query_class_prefers_definitions(query_class):
        command_definition_bonus = 0.035 * min(command_definition_hits, 3)
    provider_wiring_hits = int(result.get("implementation_provider_wiring_priority", 0) or 0)
    if provider_wiring_hits > 0:
        provider_wiring_bonus = float(intent_policy["provider_wiring_bonus_weight"]) * min(
            provider_wiring_hits, 4
        )
    callable_hits = int(result.get("implementation_callable_priority", 0) or 0)
    if callable_hits > 0 and bool(intent_policy["allow_callable_bonus"]):
        callable_bonus = float(intent_policy["callable_bonus_weight"]) * min(callable_hits, 1)
    runtime_main_hits = int(result.get("implementation_runtime_main_entrypoint_hit", 0) or 0)
    if runtime_main_hits > 0:
        runtime_main_bonus = float(intent_policy["runtime_main_bonus_weight"]) * min(runtime_main_hits, 1)
    library_entrypoint_penalty = 0.0
    if float(intent_policy["library_entrypoint_penalty"]) > 0.0:
        norm = (result.get("file_path") or "").replace("\\", "/").lower()
        if norm.endswith(("/src/lib.rs", "/__init__.py", "/lib.rs")):
            library_entrypoint_penalty = float(intent_policy["library_entrypoint_penalty"])
    definition_bonus = float(intent_policy["definition_bonus_weight"]) * min(
        int(result.get("implementation_definition_hit", 0) or 0), 2
    )
    signature_bonus = float(intent_policy["signature_bonus_weight"]) * min(
        int(result.get("implementation_exact_signature_symbol_hit", 0) or 0), 2
    )
    if query_class_prefers_definitions(query_class):
        exact_identifier_bonus = float(intent_policy["exact_identifier_bonus_weight"]) * min(
            int(result.get("implementation_exact_identifier_hit", 0) or 0), 2
        )
        declared_symbol_bonus = float(intent_policy["declared_symbol_bonus_definition"]) * min(
            int(result.get("implementation_declared_symbol_hit", 0) or 0), 2
        )
        export_bonus = float(intent_policy["export_bonus_definition"]) * min(
            int(result.get("implementation_export_hit", 0) or 0), 2
        )
        api_entrypoint_bonus = float(intent_policy["api_entrypoint_bonus_definition"]) * min(
            int(result.get("implementation_api_entrypoint_hit", 0) or 0), 1
        )
        api_context_bonus = float(intent_policy["api_context_bonus_definition"]) * min(
            int(result.get("implementation_api_context_hit", 0) or 0), 1
        )
        reexport_surface_penalty = float(intent_policy["reexport_surface_penalty_definition"]) * min(
            int(result.get("implementation_reexport_surface_hit", 0) or 0), 1
        )
        facade_surface_penalty = float(intent_policy["facade_surface_penalty_definition"]) * min(
            int(result.get("implementation_facade_surface_hit", 0) or 0), 1
        )
    elif query_class == "implementation_search":
        exact_identifier_bonus = float(intent_policy["exact_identifier_bonus_weight"]) * min(
            int(result.get("implementation_exact_identifier_hit", 0) or 0), 2
        )
        declared_symbol_bonus = float(intent_policy["declared_symbol_bonus_search"]) * min(
            int(result.get("implementation_declared_symbol_hit", 0) or 0), 2
        )
        export_bonus = float(intent_policy["export_bonus_search"]) * min(
            int(result.get("implementation_export_hit", 0) or 0), 2
        )
        api_entrypoint_bonus = 0.0
        api_context_bonus = 0.0
        reexport_surface_penalty = 0.0
        facade_surface_penalty = 0.0
    else:
        exact_identifier_bonus = float(intent_policy["exact_identifier_bonus_weight"]) * min(
            int(result.get("implementation_exact_identifier_hit", 0) or 0), 2
        )
        declared_symbol_bonus = float(intent_policy["declared_symbol_bonus_general"]) * min(
            int(result.get("implementation_declared_symbol_hit", 0) or 0), 2
        )
        export_bonus = 0.0
        api_entrypoint_bonus = 0.0
        api_context_bonus = 0.0
        reexport_surface_penalty = 0.0
        facade_surface_penalty = 0.0
    server_infra_penalty = implementation_server_infra_penalty(meta, result.get("file_path"), query)
    meta_component = (float(result.get("meta_score", 0.0) or 0.0) * meta_boost) if meta_boost > 0 else 0.0

    result["rank_score"] = (
        float(base_score)
        + float(base_bonus)
        + meta_component
        + float(result.get("implementation_node_type_score", 0.0) or 0.0)
        + float(result.get("implementation_role_score", 0.0) or 0.0)
        + symbol_bonus
        + exact_identifier_bonus
        + declared_symbol_bonus
        + path_hint_bonus
        + basename_token_bonus
        + view_body_bonus
        + provider_wiring_bonus
        + dispatcher_bonus
        + command_definition_bonus
        + routing_bonus
        + request_handler_bonus
        + controller_entity_bonus
        + callable_bonus
        + runtime_main_bonus
        + member_usage_bonus
        + chunk_role_bonus
        + definition_bonus
        + signature_bonus
        + export_bonus
        + api_entrypoint_bonus
        + api_context_bonus
        - doc_penalty
        - parser_data_penalty
        - binding_surface_penalty
        - generated_surface_penalty
        - support_path_penalty
        - provider_wiring_penalty
        - library_entrypoint_penalty
        - reexport_surface_penalty
        - facade_surface_penalty
        - usage_penalty
        - server_infra_penalty
    )
    result["implementation_rank_components"] = {
        "base_relevance": float(base_score),
        "base_bonus": float(base_bonus),
        "meta_component": meta_component,
        "node_type_score": float(result.get("implementation_node_type_score", 0.0) or 0.0),
        "role_score": float(result.get("implementation_role_score", 0.0) or 0.0),
        "symbol_bonus": symbol_bonus,
        "exact_identifier_bonus": exact_identifier_bonus,
        "declared_symbol_bonus": declared_symbol_bonus,
        "path_hint_bonus": path_hint_bonus,
        "basename_token_bonus": basename_token_bonus,
        "view_body_bonus": view_body_bonus,
        "provider_wiring_bonus": provider_wiring_bonus,
        "dispatcher_bonus": dispatcher_bonus,
        "command_definition_bonus": command_definition_bonus,
        "routing_bonus": routing_bonus,
        "request_handler_bonus": request_handler_bonus,
        "controller_entity_bonus": controller_entity_bonus,
        "callable_bonus": callable_bonus,
        "runtime_main_bonus": runtime_main_bonus,
        "member_usage_bonus": member_usage_bonus,
        "chunk_role_bonus": chunk_role_bonus,
        "definition_bonus": definition_bonus,
        "signature_bonus": signature_bonus,
        "export_bonus": export_bonus,
        "api_entrypoint_bonus": api_entrypoint_bonus,
        "api_context_bonus": api_context_bonus,
        "reexport_surface_penalty": reexport_surface_penalty,
        "facade_surface_penalty": facade_surface_penalty,
        "doc_penalty": doc_penalty,
        "parser_data_penalty": parser_data_penalty,
        "binding_surface_penalty": binding_surface_penalty,
        "generated_surface_penalty": generated_surface_penalty,
        "support_path_penalty": support_path_penalty,
        "provider_wiring_penalty": provider_wiring_penalty,
        "library_entrypoint_penalty": library_entrypoint_penalty,
        "usage_penalty": usage_penalty,
        "server_infra_penalty": server_infra_penalty,
        "role": role,
        "chunk_role": chunk_role,
        "node_types": sorted(implementation_node_types(meta)),
    }
    return result


def build_implementation_ranking_trace(results: list[dict], query: str) -> dict:
    query_class = implementation_query_class(query)
    enriched: list[dict] = []
    for result in results:
        row = dict(result)
        meta = coerce_meta(row)
        row["_meta"] = meta
        row["meta_score"] = meta_score(meta)
        try:
            base_score = float(row.get("rrf", row.get("rank_score", 0.0)) or 0.0)
        except (TypeError, ValueError):
            base_score = 0.0
        enrich_implementation_result(
            row,
            query=query,
            query_class=query_class,
            base_score=base_score,
            meta_boost=0.0,
        )
        enriched.append(row)
    enriched.sort(key=implementation_rank_tuple)
    return {
        "query_class": query_class,
        "rows": [
            {
                "file_path": row.get("file_path"),
                "base_relevance": row.get("implementation_rank_components", {}).get("base_relevance", 0.0),
                "rank_score": row.get("rank_score", 0.0),
                "role": row.get("implementation_role"),
                "role_priority": row.get("implementation_role_priority"),
                "node_types": row.get("implementation_rank_components", {}).get("node_types", []),
                "node_type_priority": row.get("implementation_node_type_priority"),
                "components": row.get("implementation_rank_components", {}),
            }
            for row in enriched
        ],
    }


def dedupe_files(results: list[dict]) -> list[dict]:
    def _has_implementation_ranking(result: dict) -> bool:
        return any(
            key in result
            for key in (
                "implementation_role_priority",
                "implementation_dispatcher_priority",
                "implementation_command_definition_priority",
                "implementation_routing_priority",
                "implementation_request_handler_priority",
            )
        )

    chosen_by_file: dict[str, dict] = {}
    file_order: list[str] = []
    for result in results:
        file_path = result.get("file_path")
        if not file_path:
            continue
        existing = chosen_by_file.get(file_path)
        if existing is None:
            chosen_by_file[file_path] = result
            file_order.append(file_path)
            continue
        if _has_implementation_ranking(result) and _has_implementation_ranking(existing):
            if implementation_rank_tuple(result) < implementation_rank_tuple(existing):
                chosen_by_file[file_path] = result
    return [chosen_by_file[file_path] for file_path in file_order]

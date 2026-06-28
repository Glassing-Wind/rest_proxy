"""Implementation-query intent and ranking-intent policy."""

from __future__ import annotations

from memory.retrieval_intent_queries import (
    implementation_expected_runtime_entrypoint_paths,
    implementation_inferred_filename_hints,
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
    query_class_prefers_definitions,
    query_class_prefers_usage,
    usage_query_prefers_example_results,
    usage_query_prefers_test_results,
)
from memory.retrieval_query import implementation_query_path_hints, implementation_query_symbols
from memory.retrieval_semantics import implementation_file_roles

__all__ = [
    "implementation_expected_runtime_entrypoint_paths",
    "implementation_inferred_filename_hints",
    "implementation_intent_policy",
    "implementation_noise_exclude_patterns",
    "implementation_provider_query_tokens",
    "implementation_query_accepts_generated_surfaces",
    "implementation_query_class",
    "implementation_query_exact_identifiers",
    "implementation_query_intent",
    "implementation_query_path_hints",
    "implementation_query_prefers_command_definition",
    "implementation_query_prefers_dispatchers",
    "implementation_query_prefers_provider_wiring",
    "implementation_query_prefers_request_routing",
    "implementation_query_prefers_runtime_main_entrypoint",
    "implementation_query_prefers_supporting_context",
    "implementation_query_relaxes_dir_cap",
    "implementation_query_symbols",
    "implementation_support_surface_penalties",
    "implementation_usage_surface_bonus",
    "query_class_prefers_definitions",
    "query_class_prefers_usage",
    "usage_query_prefers_example_results",
    "usage_query_prefers_test_results",
]


def implementation_support_surface_penalties(
    file_path: str | None,
    *,
    meta: dict | None = None,
    query: str,
    query_class: str,
    doc_like: bool,
    low_signal_support: bool,
    usage_heavy: bool,
) -> dict[str, float]:
    penalties = {
        "doc_penalty": 0.0,
        "support_path_penalty": 0.0,
        "usage_penalty": 0.0,
    }
    if doc_like:
        penalties["doc_penalty"] = 0.05
    norm = (file_path or "").replace("\\", "/").lower()
    file_roles = implementation_file_roles(meta or {})
    template_like = "/templates/" in norm or norm.endswith(".html")
    if template_like and not implementation_query_prefers_supporting_context(query):
        penalties["support_path_penalty"] = max(
            penalties["support_path_penalty"],
            0.07 if query_class_prefers_definitions(query_class) else 0.04,
        )
    static_asset_like = (
        "/resources/static/" in norm
        or "/static/" in norm
        or norm.endswith(".css")
        or norm.endswith(".scss")
        or norm.endswith(".less")
        or norm.endswith(".sass")
    )
    if (
        static_asset_like
        and implementation_query_prefers_request_routing(query)
        and not implementation_query_prefers_supporting_context(query)
    ):
        penalties["support_path_penalty"] = max(
            penalties["support_path_penalty"],
            0.14 if query_class_prefers_definitions(query_class) else 0.08,
        )
    if (
        "config_surface" in file_roles
        and "implementation_surface" not in file_roles
        and implementation_query_prefers_request_routing(query)
        and not implementation_query_prefers_supporting_context(query)
    ):
        penalties["support_path_penalty"] = max(
            penalties["support_path_penalty"],
            0.16 if query_class_prefers_definitions(query_class) else 0.10,
        )
    if low_signal_support and not implementation_query_prefers_supporting_context(query):
        penalties["support_path_penalty"] = max(
            penalties["support_path_penalty"],
            0.05 if query_class_prefers_definitions(query_class) else 0.03,
        )
    if (
        (
            "/profiles/" in norm
            or "profile_surface" in file_roles
        )
        and implementation_query_prefers_dispatchers(query)
        and "profile" not in implementation_query_symbols(query)
        and "profile" not in implementation_query_exact_identifiers(query)
    ):
        penalties["support_path_penalty"] = max(
            penalties["support_path_penalty"],
            0.35 if query_class_prefers_definitions(query_class) else 0.18,
        )
    if usage_heavy:
        penalties["usage_penalty"] = 0.04 if query_class_prefers_definitions(query_class) else 0.0
    return penalties


def implementation_usage_surface_bonus(
    *,
    chunk_role: str,
    query: str,
    query_class: str,
    exact_member_hits: int,
) -> float:
    if exact_member_hits <= 0 or not query_class_prefers_usage(query_class):
        return 0.0
    if chunk_role == "example_usage":
        return 0.13 if usage_query_prefers_example_results(query) else 0.11
    if chunk_role == "test_usage":
        return 0.08 if usage_query_prefers_test_results(query) else 0.06
    if chunk_role == "usage":
        return 0.03
    return 0.0


def implementation_intent_policy(query: str, query_class: str) -> dict[str, float | bool]:
    policy: dict[str, float | bool] = {
        "member_usage_bonus_usage": 0.0,
        "member_usage_bonus_search": 0.0,
        "path_hint_bonus_search": 0.0,
        "path_hint_bonus_definition": 0.0,
        "provider_wiring_bonus_weight": 0.0,
        "callable_bonus_weight": 0.0,
        "runtime_main_bonus_weight": 0.16,
        "definition_bonus_weight": 0.025,
        "signature_bonus_weight": 0.04,
        "declared_symbol_bonus_definition": 0.0,
        "declared_symbol_bonus_search": 0.0,
        "declared_symbol_bonus_general": 0.0,
        "exact_identifier_bonus_weight": 0.0,
        "export_bonus_definition": 0.0,
        "export_bonus_search": 0.0,
        "api_entrypoint_bonus_definition": 0.0,
        "api_context_bonus_definition": 0.0,
        "reexport_surface_penalty_definition": 0.0,
        "facade_surface_penalty_definition": 0.0,
        "library_entrypoint_penalty": 0.0,
        "allow_callable_bonus": False,
    }
    if query_class_prefers_usage(query_class):
        policy["member_usage_bonus_usage"] = 0.06
        return policy
    if query_class == "implementation_search":
        policy["member_usage_bonus_search"] = 0.015
        policy["path_hint_bonus_search"] = 0.12
        policy["provider_wiring_bonus_weight"] = 0.05
        policy["callable_bonus_weight"] = 0.03
        policy["declared_symbol_bonus_search"] = 0.02
        policy["exact_identifier_bonus_weight"] = 0.08
        policy["export_bonus_search"] = 0.01
        policy["allow_callable_bonus"] = True
    elif query_class == "implementation_explanation":
        policy["path_hint_bonus_definition"] = 0.08
        policy["provider_wiring_bonus_weight"] = 0.05
        policy["callable_bonus_weight"] = 0.03
        policy["declared_symbol_bonus_definition"] = 0.05
        policy["exact_identifier_bonus_weight"] = 0.08
        policy["export_bonus_definition"] = 0.03
        policy["api_entrypoint_bonus_definition"] = 0.03
        policy["api_context_bonus_definition"] = 0.02
        policy["reexport_surface_penalty_definition"] = 0.07
        policy["facade_surface_penalty_definition"] = 0.05
        policy["allow_callable_bonus"] = True
    elif query_class_prefers_definitions(query_class):
        policy["path_hint_bonus_definition"] = 0.08
        policy["provider_wiring_bonus_weight"] = 0.05
        policy["callable_bonus_weight"] = 0.03
        policy["declared_symbol_bonus_definition"] = 0.05
        policy["exact_identifier_bonus_weight"] = 0.08
        policy["export_bonus_definition"] = 0.03
        policy["api_entrypoint_bonus_definition"] = 0.03
        policy["api_context_bonus_definition"] = 0.02
        policy["reexport_surface_penalty_definition"] = 0.07
        policy["facade_surface_penalty_definition"] = 0.05
        policy["allow_callable_bonus"] = True
    else:
        policy["declared_symbol_bonus_general"] = 0.01
        policy["exact_identifier_bonus_weight"] = 0.04
    if implementation_query_prefers_runtime_main_entrypoint(query):
        policy["library_entrypoint_penalty"] = 0.05
    return policy


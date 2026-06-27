"""Shared retrieval ranking, filtering, deduplication, and rendering policy."""

from __future__ import annotations

import re

from _semantic_contract import (
    FOCUSED_DISPATCHER_ANCHOR_CAPABILITY,
    FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION,
    has_focused_dispatcher_anchor_contract,
)
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
from memory.retrieval_semantics import (
    CALLSITE_NODE_TYPES,
    DECLARATION_NODE_TYPES,
    EXPORT_NODE_TYPES,
    MODULE_NODE_TYPES,
    TYPE_DEFINITION_NODE_TYPES,
    apply_result_surface_flags,
    implementation_allows_path_fallback,
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
    _implementation_definition_declaration_patterns,
    implementation_query_definition_subject_identifiers,
    implementation_query_member_exprs,
    implementation_query_path_hints,
    implementation_query_symbols,
)
from memory.retrieval_surfaces import (
    implementation_rank_tuple,
    is_doc_like_path,
    is_generated_implementation_surface_path,
    is_low_signal_binding_surface_path,
    is_low_signal_parser_data_path,
    is_low_signal_support_path,
    is_usage_heavy_path,
)
from memory.retrieval_telemetry import (
    append_dispatcher_telemetry_event,
    append_duplicate_telemetry_event,
    append_routing_telemetry_event,
    dispatcher_telemetry_enabled,
    duplicate_experiment_flags_from_env,
    duplicate_experiment_flags_with_query_class,
    duplicate_telemetry_enabled,
    merge_duplicate_experiments,
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
    "duplicate_experiment_flags_from_env",
    "duplicate_experiment_flags_with_query_class",
    "duplicate_telemetry_enabled",
    "filter_by_cargo_crate",
    "format_meta",
    "implementation_is_profile_candidate",
    "is_generated_implementation_surface_path",
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
    "summarize_trace_for_debug",
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


def implementation_query_intent(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    strong_terms = [
        "neo4j",
        "gds",
        "indexing",
        "finalization",
        "finalize",
        "retried",
        "retry",
        "resolved",
        "resolve",
        "import",
        "imports",
        "service",
        "route",
        "handler",
        "handled",
        "request",
        "requests",
        "http",
        "controller",
        "function",
        "method",
        "class",
        "parser",
        "symbol",
        "extraction",
        "repository analysis",
        "code intel",
        "call graph",
        "implementation",
        "code path",
        "how does",
        "where is",
        "where does",
        "find",
        "bug",
        "fix",
        "logic",
        "routing",
        "router",
        "call site",
        "db model",
        "prisma",
        "quickbooks",
        "tenant credit",
        "accounting sync",
    ]
    if any(term in text for term in strong_terms):
        return True
    if re.search(r"\b[a-z_][a-z0-9_]*\s*\(", text):
        return True
    if "::" in text:
        return True
    token_hits = re.findall(r"[a-zA-Z_]{3,}", text)
    return any(tok in {"svc", "api", "db", "route", "routing", "router", "model", "handler", "server"} for tok in token_hits)


def implementation_query_class(query: str) -> str:
    text = (query or "").strip().lower()
    if not implementation_query_intent(text):
        return "general"
    path_hints = implementation_query_path_hints(text)
    if re.search(r"\b(?:where|how)\s+is\s+[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+\s+used\b", text):
        return "usage_lookup"
    if re.search(r"\bexamples?\s+of\s+[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+\b", text):
        return "usage_lookup"
    if (
        "call site" in text
        or "where is it called" in text
        or re.search(r"\bwhere\s+is\s+[a-z_][a-z0-9_]*\s+called\b", text)
        or "who calls" in text
        or "callers of" in text
        or "usage of" in text
        or "usage" in text
        or re.search(r"\b(?:where|how)\s+is\b.*\bused\b", text)
    ):
        return "usage_lookup"
    if (
        "where is" in text
        or "where does" in text
        or "defined" in text
        or "definition" in text
        or "entrypoint" in text
        or "public api" in text
        or "api entrypoint" in text
    ):
        if path_hints and ("entrypoint" in text or "main" in text):
            return "implementation_search"
        return "api_definition_lookup"
    if "how does" in text:
        return "implementation_explanation"
    if re.search(r"\b[a-z_][a-z0-9_]*\s*\(", text):
        return "symbol_lookup"
    return "implementation_search"


def query_class_prefers_definitions(query_class: str) -> bool:
    return query_class in {"api_definition_lookup", "implementation_explanation", "symbol_lookup"}


def query_class_prefers_usage(query_class: str) -> bool:
    return query_class == "usage_lookup"


def usage_query_prefers_test_results(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in (" test", " tests", "testing", "spec", "e2e", "unit test"))


def usage_query_prefers_example_results(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in (" example", " examples", "sample", "smoke"))


def implementation_query_prefers_runtime_main_entrypoint(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    if not implementation_query_path_hints(text):
        return False
    return "main" in text and "entrypoint" in text


def implementation_expected_runtime_entrypoint_paths(query: str) -> list[str]:
    if not implementation_query_prefers_runtime_main_entrypoint(query):
        return []
    suffixes = (
        "src/main.rs",
        "src/main.py",
        "src/main.ts",
        "src/main.tsx",
        "src/main.js",
        "src/main.jsx",
    )
    paths: list[str] = []
    for hint in implementation_query_path_hints(query):
        prefix = hint.rstrip("/")
        for suffix in suffixes:
            paths.append(f"{prefix}/{suffix}")
    return paths


def implementation_query_relaxes_dir_cap(query: str) -> bool:
    if not query:
        return False
    if implementation_query_path_hints(query):
        return True
    if implementation_inferred_filename_hints(query):
        return True
    return bool(implementation_expected_runtime_entrypoint_paths(query))


def implementation_query_prefers_dispatchers(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    if "dispatch" in text or "dispatcher" in text:
        return True

    context_terms = (
        "model",
        "models",
        "provider",
        "providers",
        "openai",
        "anthropic",
    )
    has_context = any(term in text for term in context_terms)
    if not has_context:
        return False

    dispatcher_terms = (
        "selected",
        "selection",
        "inference",
        "infer",
        "wiring",
        "wire",
    )
    return any(term in text for term in dispatcher_terms)


def implementation_query_prefers_provider_wiring(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text or "provider" not in text:
        return False
    return implementation_query_prefers_dispatchers(query) or "openai" in text


def implementation_provider_query_tokens(query: str) -> set[str]:
    if not implementation_query_prefers_provider_wiring(query):
        return set()
    generic = {
        "provider",
        "providers",
        "wiring",
        "wire",
        "work",
        "works",
        "selection",
        "selected",
        "dispatch",
        "model",
        "models",
        "infer",
        "inference",
        "class",
        "classes",
        "how",
        "does",
    }
    return {
        token
        for token in (implementation_query_symbols(query) | implementation_query_exact_identifiers(query))
        if token and token not in generic
    }


def implementation_query_accepts_generated_surfaces(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    generated_terms = (
        "generated",
        "stub",
        "stubs",
        "proto",
        "protobuf",
        ".grpc.swift",
        ".pb.swift",
    )
    return any(term in text for term in generated_terms)


def implementation_query_prefers_supporting_context(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    return any(term in text for term in ("script", "tooling", "build", "release", "dev tool", "developer tool"))


def implementation_query_prefers_request_routing(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    if "routing" in text or "router" in text:
        return True
    request_terms = ("request", "requests", "incoming", "http", "endpoint", "controller")
    routing_terms = ("server", "handler", "handled", "handle", "mapping", "mapped", "route", "routes")
    return any(term in text for term in request_terms) and any(term in text for term in routing_terms)


def implementation_query_prefers_command_definition(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    return ("command" in text or "subcommand" in text) and "enum" in text


def implementation_noise_exclude_patterns(query: str) -> list[str]:
    text = (query or "").strip().lower()
    if not text:
        return []
    patterns: list[str] = []
    if implementation_query_prefers_request_routing(query) and "grpc" in text:
        patterns.extend(
            [
                "*Browser.swift",
                "*Advertiser.swift",
                "*Signer.swift",
            ]
        )
        if not implementation_query_accepts_generated_surfaces(query):
            patterns.extend(
                [
                    "*.grpc.swift",
                    "*.pb.swift",
                ]
            )
    return patterns


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

def implementation_dispatcher_contract_priority(meta: dict, query: str) -> int:
    if not implementation_query_prefers_dispatchers(query):
        return 0
    if not isinstance(meta, dict) or not has_focused_dispatcher_anchor_contract(meta):
        return 0
    exact_identifiers = implementation_query_exact_identifiers(query)
    if not exact_identifiers:
        return 0
    declared_symbols = {
        str(symbol).strip().lower()
        for symbol in (meta.get("declared_symbols") or [])
        if str(symbol).strip()
    }
    if declared_symbols & exact_identifiers:
        return 2
    file_symbols = {
        str(symbol).strip().lower()
        for symbol in (meta.get("file_symbols") or [])
        if str(symbol).strip()
    }
    if file_symbols & exact_identifiers:
        return 1
    return 0


def implementation_query_exact_identifiers(query: str) -> set[str]:
    raw = (query or "").strip()
    if not raw:
        return set()
    stopwords = {
        "how",
        "does",
        "work",
        "works",
        "what",
        "where",
        "when",
        "which",
        "into",
        "from",
        "with",
        "that",
        "this",
        "have",
        "uses",
        "using",
        "used",
        "build",
        "builds",
        "local",
        "files",
        "file",
        "code",
        "tree",
        "pack",
        "index",
        "ts",
        "implemented",
        "implementation",
        "defined",
        "define",
        "definition",
        "enum",
        "struct",
        "class",
        "trait",
        "protocol",
        "interface",
        "type",
    }
    identifiers: set[str] = set()
    for token in re.findall(r"[$A-Za-z_][A-Za-z0-9_]*", raw):
        if len(token) < 3:
            continue
        lowered = token.lower()
        if lowered in stopwords:
            continue
        if token.startswith("$") or "_" in token or any(ch.isupper() for ch in token[1:]):
            identifiers.add(lowered)
    query_symbols = implementation_query_symbols(raw)
    if implementation_query_prefers_dispatchers(raw):
        if "model" in query_symbols or "models" in query_symbols:
            identifiers.add("infer_model")
        if "provider" in query_symbols or "providers" in query_symbols:
            identifiers.update({"infer_provider", "infer_provider_class"})
    identifiers.update(implementation_query_definition_subject_identifiers(raw))
    return identifiers


def dispatcher_contract_telemetry(
    *,
    query: str,
    query_class: str | None,
    semantic_candidates: list[dict],
    ranked_candidates: list[dict],
    final_results: list[dict],
    rescue_applied: bool,
) -> dict | None:
    exact_identifiers = implementation_query_exact_identifiers(query)
    if not implementation_query_prefers_dispatchers(query) or not exact_identifiers:
        return None

    def _declared(row: dict) -> set[str]:
        meta = coerce_meta(row)
        return {
            str(symbol).strip().lower()
            for symbol in (meta.get("declared_symbols") or [])
            if str(symbol).strip()
        }

    def _exact_matches(rows: list[dict]) -> list[dict]:
        matches: list[dict] = []
        for row in rows or []:
            if int(row.get("implementation_dispatcher_priority", 0) or 0) < 5:
                continue
            if _declared(row) & exact_identifiers:
                matches.append(row)
        return matches

    def _contract_matches(rows: list[dict]) -> list[dict]:
        return [row for row in _exact_matches(rows) if has_focused_dispatcher_anchor_contract(coerce_meta(row))]

    def _top_snapshot(rows: list[dict]) -> dict:
        top = rows[0] if rows else {}
        top_meta = coerce_meta(top)
        top_declared = sorted(_declared(top)) if isinstance(top, dict) else []
        top_exact = bool(top_declared and (set(top_declared) & exact_identifiers))
        top_contract = bool(top_exact and has_focused_dispatcher_anchor_contract(top_meta))
        return {
            "file_path": top.get("file_path") if isinstance(top, dict) else None,
            "declared_symbols": top_declared,
            "exact_hit": top_exact,
            "contract_hit": top_contract,
        }

    semantic_exact = _exact_matches(semantic_candidates)
    semantic_contract = _contract_matches(semantic_candidates)
    ranked_exact = _exact_matches(ranked_candidates)
    ranked_contract = _contract_matches(ranked_candidates)
    final_exact = _exact_matches(final_results)
    final_contract = _contract_matches(final_results)

    semantic_top = _top_snapshot(semantic_candidates)
    ranked_top = _top_snapshot(ranked_candidates)
    final_top = _top_snapshot(final_results)

    diagnosis = "no_exact_dispatcher_signal"
    if semantic_contract:
        diagnosis = "ranking_or_promotion_needed"
        if ranked_top["contract_hit"]:
            diagnosis = "ranking_surfaces_contract"
        elif final_top["contract_hit"] and rescue_applied:
            diagnosis = "rescue_or_final_promotion_surfaces_contract"
        elif final_top["contract_hit"]:
            diagnosis = "final_promotion_surfaces_contract"
    elif semantic_exact:
        diagnosis = "contract_missing_from_semantic_candidates"
        if final_top["contract_hit"]:
            diagnosis = "contract_missing_from_semantic_candidates_but_recovered"
    elif final_top["contract_hit"]:
        diagnosis = "semantic_recall_missing_contract_candidate"

    return {
        "query_class": query_class or implementation_query_class(query),
        "exact_identifiers": sorted(exact_identifiers),
        "dispatcher_anchor_contract_capability": FOCUSED_DISPATCHER_ANCHOR_CAPABILITY,
        "dispatcher_anchor_contract_version": FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION,
        "rescue_applied": bool(rescue_applied),
        "diagnosis": diagnosis,
        "semantic_exact_match_count": len(semantic_exact),
        "semantic_contract_match_count": len(semantic_contract),
        "ranked_exact_match_count": len(ranked_exact),
        "ranked_contract_match_count": len(ranked_contract),
        "final_exact_match_count": len(final_exact),
        "final_contract_match_count": len(final_contract),
        "semantic_top": semantic_top,
        "ranked_top": ranked_top,
        "final_top": final_top,
    }


def routing_signal_telemetry(
    *,
    query: str,
    query_class: str | None,
    semantic_candidates: list[dict],
    ranked_candidates: list[dict],
    final_results: list[dict],
    partition_applied: bool,
) -> dict | None:
    if not implementation_query_prefers_request_routing(query):
        return None

    def _signal_snapshot(row: dict | None) -> dict:
        if not isinstance(row, dict):
            return {
                "file_path": None,
                "routing_hit": False,
                "request_handler_hit": False,
                "controller_entity_hit": False,
            }
        routing_hit = int(row.get("implementation_routing_priority", 0) or 0) > 0
        request_handler_hit = int(row.get("implementation_request_handler_priority", 0) or 0) > 0
        controller_entity_hit = int(row.get("implementation_controller_entity_hit", 0) or 0) > 0
        return {
            "file_path": row.get("file_path"),
            "routing_hit": routing_hit,
            "request_handler_hit": request_handler_hit,
            "controller_entity_hit": controller_entity_hit,
        }

    def _signal_rows(rows: list[dict]) -> list[dict]:
        matches: list[dict] = []
        for row in rows or []:
            snap = _signal_snapshot(row)
            if snap["routing_hit"] or snap["request_handler_hit"] or snap["controller_entity_hit"]:
                matches.append(row)
        return matches

    semantic_signal = _signal_rows(semantic_candidates)
    ranked_signal = _signal_rows(ranked_candidates)
    final_signal = _signal_rows(final_results)

    semantic_top = _signal_snapshot(semantic_candidates[0] if semantic_candidates else None)
    ranked_top = _signal_snapshot(ranked_candidates[0] if ranked_candidates else None)
    final_top = _signal_snapshot(final_results[0] if final_results else None)

    diagnosis = "no_routing_signal"
    if semantic_signal:
        diagnosis = "ranking_or_partition_needed"
        if ranked_top["routing_hit"] or ranked_top["request_handler_hit"] or ranked_top["controller_entity_hit"]:
            diagnosis = "ranking_surfaces_routing_signal"
        elif (
            final_top["routing_hit"] or final_top["request_handler_hit"] or final_top["controller_entity_hit"]
        ) and partition_applied:
            diagnosis = "partition_surfaces_routing_signal"
        elif final_top["routing_hit"] or final_top["request_handler_hit"] or final_top["controller_entity_hit"]:
            diagnosis = "final_promotion_surfaces_routing_signal"
    elif final_top["routing_hit"] or final_top["request_handler_hit"] or final_top["controller_entity_hit"]:
        diagnosis = "semantic_signal_missing_but_recovered"

    return {
        "query_class": query_class or implementation_query_class(query),
        "partition_applied": bool(partition_applied),
        "diagnosis": diagnosis,
        "semantic_signal_match_count": len(semantic_signal),
        "ranked_signal_match_count": len(ranked_signal),
        "final_signal_match_count": len(final_signal),
        "semantic_top": semantic_top,
        "ranked_top": ranked_top,
        "final_top": final_top,
    }


def implementation_inferred_filename_hints(query: str) -> list[str]:
    text = (query or "").strip().lower()
    if not text:
        return []
    hints: set[str] = set()
    query_symbols = implementation_query_symbols(query)
    if implementation_query_prefers_dispatchers(query):
        if "model" in query_symbols or "models" in query_symbols:
            hints.add("models/__init__.py")
    if implementation_query_prefers_provider_wiring(query):
        provider_tokens = sorted(implementation_provider_query_tokens(query))
        for token in provider_tokens:
            hints.add(f"providers/{token}.py")
    view_like_tokens = {
        token
        for token in query_symbols
        if token in {"sidebar", "toolbar", "panel", "inspector", "canvas", "editor", "modal", "sheet"}
    }
    if view_like_tokens:
        for token in view_like_tokens:
            hints.add(f"{token}view.swift")
            hints.add(f"views/{token}view.swift")
    if implementation_query_prefers_request_routing(query):
        generic = {
            "request",
            "requests",
            "routing",
            "route",
            "routes",
            "router",
            "server",
            "handler",
            "handled",
            "handle",
            "incoming",
            "http",
            "endpoint",
            "controller",
            "implemented",
            "implementation",
            "spring",
            "petclinic",
            "gin",
        }
        for token in implementation_query_symbols(query):
            if token in generic:
                continue
            hints.add(f"{token}controller.java")
        if "grpc" in text:
            hints.update(
                {
                    "grpc/server",
                    "server/sources",
                    "serviceimpl",
                    "serviceimpl.swift",
                    "handler",
                }
            )
    if "flow" in text and "event" in text:
        if "inbound" in text or "outbound" in text:
            hints.add("channelpipeline.swift")
        hints.add("pipeline")
    return sorted(hints)


def implementation_path_hint_hit(file_path: str | None, query: str | None = None, path_hints: list[str] | None = None) -> int:
    norm = (file_path or "").replace("\\", "/").lower()
    if not norm:
        return 0
    if path_hints is not None:
        hints = path_hints
    else:
        query_text = query or ""
        hints = implementation_query_path_hints(query_text)
        inferred = implementation_inferred_filename_hints(query_text)
        if inferred:
            hints = sorted(set(hints) | set(inferred))
    if not hints:
        return 0
    return sum(1 for hint in hints if hint and hint in norm)


def implementation_basename_token_hit(file_path: str | None, query: str) -> int:
    basename = ((file_path or "").replace("\\", "/").rsplit("/", 1)[-1]).strip().lower()
    if not basename:
        return 0
    query_tokens = implementation_query_symbols(query) | implementation_query_exact_identifiers(query)
    if not query_tokens:
        return 0
    basename_tokens = {
        token.lower()
        for token in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+", basename.replace(".", "_"))
        if token
    }
    return sum(1 for token in query_tokens if token in basename_tokens)


def implementation_symbol_hit(meta: dict, query: str) -> int:
    if not isinstance(meta, dict):
        return 0
    symbols = implementation_query_symbols(query)
    if not symbols:
        return 0
    file_symbols = meta.get("file_symbols")
    if not isinstance(file_symbols, list) or not file_symbols:
        return 0
    lowered = {
        str(symbol).strip().lower()
        for symbol in file_symbols
        if str(symbol).strip()
    }
    if not lowered:
        return 0
    return sum(1 for symbol in symbols if symbol in lowered)


def implementation_declared_symbol_hit(meta: dict, query: str) -> int:
    if not isinstance(meta, dict):
        return 0
    symbols = implementation_query_symbols(query)
    if not symbols:
        return 0
    declared_symbols = meta.get("declared_symbols")
    if not isinstance(declared_symbols, list) or not declared_symbols:
        return 0
    lowered = {
        str(symbol).strip().lower()
        for symbol in declared_symbols
        if str(symbol).strip()
    }
    if not lowered:
        return 0
    return sum(1 for symbol in symbols if symbol in lowered)


def implementation_exact_identifier_hit(meta: dict, query: str) -> int:
    if not isinstance(meta, dict):
        return 0
    identifiers = implementation_query_exact_identifiers(query)
    if not identifiers:
        return 0
    candidates: set[str] = set()
    for key in ("declared_symbols", "file_symbols"):
        values = meta.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value).strip().lower()
            if text:
                candidates.add(text)
    if not candidates:
        return 0
    return sum(1 for identifier in identifiers if identifier in candidates)


def implementation_dispatcher_priority(meta: dict, file_path: str | None, query: str) -> int:
    if not implementation_query_prefers_dispatchers(query):
        return 0
    if not isinstance(meta, dict):
        return 0
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    symbol_roles = implementation_declared_symbol_roles(meta)
    file_roles = implementation_file_roles(meta)
    candidates: set[str] = set()
    for key in ("declared_symbols", "file_symbols"):
        values = meta.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value).strip().lower()
            if text:
                candidates.add(text)
    priority = 0
    if "dispatcher_surface" in file_roles:
        priority = max(priority, 3)
    for lowered_roles in symbol_roles.values():
        if "canonical_dispatcher" in lowered_roles:
            if "model_selector" in lowered_roles:
                priority = max(priority, 6)
            elif "provider_selector" in lowered_roles:
                priority = max(priority, 5)
            else:
                priority = max(priority, 4)
        elif "model_selector" in lowered_roles or "provider_selector" in lowered_roles:
            priority = max(priority, 3)
        elif "dispatcher" in lowered_roles:
            priority = max(priority, 2)
    if not candidates:
        return priority
    if basename == "__init__.py" and "infer_model" in candidates:
        priority = max(priority, 6)
    for symbol in candidates:
        if symbol in {"infer_model", "infer_provider", "infer_provider_class"}:
            if symbol == "infer_model":
                priority = max(priority, 6)
            elif symbol in {"infer_provider", "infer_provider_class"}:
                priority = max(priority, 5)
        elif re.match(r"^(infer|resolve|select|dispatch|choose)_[a-z0-9_]+$", symbol):
            priority = max(priority, 2)
        elif "provider" in symbol or "model" in symbol:
            priority = max(priority, 1)
    return priority


def implementation_command_definition_priority(meta: dict, file_path: str | None, query: str) -> int:
    if not implementation_query_prefers_command_definition(query):
        return 0
    if not isinstance(meta, dict):
        return 0
    symbol_roles = implementation_declared_symbol_roles(meta)
    file_roles = implementation_file_roles(meta)
    priority = 0
    if "command_surface" in file_roles:
        priority = max(priority, 3)
    for lowered_roles in symbol_roles.values():
        if "command_enum" in lowered_roles:
            priority = max(priority, 5)
    norm = (file_path or "").replace("\\", "/").lower()
    if norm.endswith("/src/lib.rs") or norm.endswith("/src/main.rs"):
        priority = max(priority, 4 if priority > 0 else 0)
    return priority


def implementation_provider_wiring_priority(meta: dict, file_path: str | None, query: str) -> int:
    provider_tokens = implementation_provider_query_tokens(query)
    if not provider_tokens:
        return 0
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    symbol_roles = implementation_declared_symbol_roles(meta)
    file_roles = implementation_file_roles(meta)
    candidates: set[str] = set()
    if isinstance(meta, dict):
        for key in ("declared_symbols", "file_symbols"):
            values = meta.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value).strip().lower()
                if text:
                    candidates.add(text)
    priority = 0
    if "provider_dispatcher_surface" in file_roles:
        priority = max(priority, 3)
    for lowered_roles in symbol_roles.values():
        if "canonical_dispatcher" in lowered_roles and "provider_selector" in lowered_roles:
            priority = max(priority, 4)
        elif "provider_selector" in lowered_roles:
            priority = max(priority, 3)
    if basename == "__init__.py" and {"infer_provider", "infer_provider_class"} & candidates:
        priority = max(priority, 4)
    for token in provider_tokens:
        if f"/providers/{token}." in norm:
            priority = max(priority, 3)
        elif token in basename:
            priority = max(priority, 2)
        if f"{token}provider" in candidates:
            priority = max(priority, 3)
        elif any(token in symbol for symbol in candidates):
            priority = max(priority, 1)
    return priority


def implementation_provider_wiring_penalty(meta: dict, file_path: str | None, query: str) -> float:
    provider_tokens = implementation_provider_query_tokens(query)
    if not provider_tokens:
        return 0.0
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    candidates: set[str] = set()
    if isinstance(meta, dict):
        for key in ("declared_symbols", "file_symbols"):
            values = meta.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value).strip().lower()
                if text:
                    candidates.add(text)
    if "/providers/" not in norm:
        return 0.14
    if basename == "__init__.py" and {"infer_provider", "infer_provider_class"} & candidates:
        return 0.0
    for token in provider_tokens:
        if f"/providers/{token}." in norm or f"{token}provider" in candidates:
            return 0.0
    return 0.10


def implementation_routing_priority(meta: dict, file_path: str | None, query: str) -> int:
    if not implementation_query_prefers_request_routing(query):
        return 0
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    priority = 0
    file_roles = implementation_file_roles(meta)
    has_file_roles = implementation_has_file_roles(meta)
    if "route_definition_surface" in file_roles:
        priority = max(priority, 3)
    if "request_handler_surface" in file_roles:
        priority = max(priority, 2)
    if "controller_surface" in file_roles:
        priority = max(priority, 2)
    if not has_file_roles:
        if any(part in norm for part in ("/grpc/server/", "/server/sources/", "/server/")):
            priority = max(priority, 2)
        if "/controller/" in norm or basename.endswith("controller.java"):
            priority = max(priority, 2)
        if "serviceimpl" in basename or basename.endswith("server.swift"):
            priority = max(priority, 2)
    candidates: set[str] = set()
    if isinstance(meta, dict):
        for key in ("declared_symbols", "file_symbols"):
            values = meta.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value).strip().lower()
                if text:
                    candidates.add(text)
    for symbol in candidates:
        if re.match(r"^(handle|route|dispatch)_[a-z0-9_]+$", symbol):
            priority = max(priority, 3)
        elif symbol.startswith(("handle", "route", "dispatch")):
            priority = max(priority, 3)
        elif "route" in symbol or "mapping" in symbol:
            priority = max(priority, 3)
        elif symbol.endswith("controller"):
            priority = max(priority, 2)
        elif symbol.startswith("process"):
            priority = max(priority, 2)
        elif symbol.startswith(("manage", "update")):
            priority = max(priority, 1)
    return priority


def implementation_request_handler_priority(meta: dict, file_path: str | None, query: str) -> int:
    if not implementation_query_prefers_request_routing(query):
        return 0
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    priority = 0
    file_roles = implementation_file_roles(meta)
    has_file_roles = implementation_has_file_roles(meta)
    if "request_handler_surface" in file_roles:
        priority = max(priority, 3)
    elif "controller_surface" in file_roles:
        priority = max(priority, 2)
    if not has_file_roles:
        if "serviceimpl" in basename:
            priority = max(priority, 3)
        elif "/controller/" in norm or basename.endswith("controller.java"):
            priority = max(priority, 2)
        elif basename.endswith(("service.swift", "server.swift")):
            priority = max(priority, 1)
    candidates: set[str] = set()
    if isinstance(meta, dict):
        for key in ("declared_symbols", "file_symbols"):
            values = meta.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value).strip().lower()
                if text:
                    candidates.add(text)
    for symbol in candidates:
        if symbol.startswith("handle"):
            priority = max(priority, 3)
        elif symbol.startswith("process"):
            priority = max(priority, 2)
        elif symbol.startswith(("generate", "complete", "cancel")):
            priority = max(priority, 2)
    return priority


def implementation_controller_entity_hit(meta: dict, file_path: str | None, query: str) -> int:
    if not implementation_query_prefers_request_routing(query):
        return 0
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    if not ("/controller/" in norm or basename.endswith("controller.java")):
        return 0
    generic = {
        "request",
        "requests",
        "routing",
        "route",
        "routes",
        "router",
        "server",
        "handler",
        "handled",
        "handle",
        "incoming",
        "http",
        "endpoint",
        "controller",
        "implemented",
        "implementation",
        "spring",
        "petclinic",
        "java",
    }
    query_tokens = {
        token
        for token in (implementation_query_symbols(query) | implementation_query_exact_identifiers(query))
        if token and token not in generic
    }
    if not query_tokens:
        return 0

    symbol_candidates: set[str] = set()
    if isinstance(meta, dict):
        for key in ("declared_symbols", "file_symbols"):
            values = meta.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value).strip().lower()
                if text:
                    symbol_candidates.add(text)

    priority = 0
    for token in query_tokens:
        controller_name = f"{token}controller"
        if controller_name in symbol_candidates:
            priority = max(priority, 2)
        if token in basename:
            priority = max(priority, 1)
    return priority


def implementation_server_infra_penalty(meta: dict, file_path: str | None, query: str) -> float:
    if not implementation_query_prefers_request_routing(query):
        return 0.0
    text = (query or "").strip().lower()
    grpc_routing_query = "grpc" in text
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    penalty = 0.0
    if any(token in basename for token in ("browser.swift", "advertiser.swift", "signer.swift")):
        penalty = max(penalty, 0.18 if grpc_routing_query else 0.08)
    candidates: set[str] = set()
    if isinstance(meta, dict):
        for key in ("declared_symbols", "file_symbols"):
            values = meta.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value).strip().lower()
                if text:
                    candidates.add(text)
    infra_prefixes = ("netservice", "advertise", "resolve", "discover", "browse", "completeboost")
    if any(symbol.startswith(infra_prefixes) for symbol in candidates):
        penalty = max(penalty, 0.18 if grpc_routing_query else 0.08)
    return penalty


def implementation_exact_member_usage_hit(content: str | None, query: str, meta: dict | None = None) -> int:
    exprs = implementation_query_member_exprs(query)
    if not exprs:
        return 0
    meta_hits = implementation_member_usages(meta or {})
    if meta_hits:
        return sum(1 for expr in exprs if expr in meta_hits)
    text = (content or "").lower()
    if not text:
        return 0
    return sum(1 for expr in exprs if expr in text)


def implementation_exact_member_usage_site_hit(result: dict) -> bool:
    if int(result.get("implementation_exact_member_usage_hit", 0) or 0) <= 0:
        return False
    role = str(result.get("implementation_role") or "").strip().lower()
    if role in {"usage_callsite", "test_example"}:
        return True
    chunk_role = implementation_chunk_role(coerce_meta(result), result.get("file_path"))
    return chunk_role in {"usage", "example_usage", "test_usage"}


def implementation_definition_hit(content: str | None, query: str) -> int:
    text = (content or "").strip()
    if not text:
        return 0
    symbols = implementation_query_symbols(query)
    lowered = text.lower()
    hits = 0
    for symbol in symbols:
        patterns = [
            rf"\bpub\s+fn\s+{re.escape(symbol)}\s*\(",
            rf"\bfn\s+{re.escape(symbol)}\s*\(",
            rf"\bdef\s+{re.escape(symbol)}\s*\(",
            rf"\basync\s+def\s+{re.escape(symbol)}\s*\(",
            rf"\bfunction\s+{re.escape(symbol)}\s*\(",
            rf"\bexport\s+(?:async\s+)?function\s+{re.escape(symbol)}\s*\(",
            rf"\b{re.escape(symbol)}\s*:\s*function\b",
        ]
        if any(re.search(pattern, lowered) for pattern in patterns):
            hits += 1
    for pattern in _implementation_definition_declaration_patterns(query):
        if re.search(pattern, lowered):
            hits += 1
    return hits


def implementation_api_entrypoint_hit(file_path: str | None, definition_hit: int, meta: dict | None = None) -> int:
    metadata = meta or {}
    file_roles = implementation_file_roles(metadata)
    if {"api_surface", "runtime_entrypoint_surface", "library_facade_surface"} & file_roles and definition_hit > 0:
        return 1
    if not implementation_allows_path_fallback(metadata):
        return 0
    if definition_hit <= 0 or not file_path:
        return 0
    norm = (file_path or "").replace("\\", "/").lower()
    api_entrypoint_suffixes = (
        "/src/lib.rs",
        "/src/main.rs",
        "/src/main.py",
        "/src/main.ts",
        "/src/main.tsx",
        "/src/main.js",
        "/src/main.jsx",
        "/src/index.ts",
        "/src/index.tsx",
        "/src/index.js",
        "/src/index.jsx",
        "/__init__.py",
        "/lib.rs",
    )
    if norm.endswith(api_entrypoint_suffixes):
        return 1
    if norm.endswith("/main.go") and ("/cmd/" in norm or norm.startswith("cmd/")):
        return 1
    return 0


def implementation_runtime_main_entrypoint_hit(file_path: str | None, query: str) -> int:
    return implementation_runtime_main_entrypoint_hit_with_meta(file_path, query, None)


def implementation_runtime_main_entrypoint_hit_with_meta(
    file_path: str | None,
    query: str,
    meta: dict | None = None,
) -> int:
    metadata = meta or {}
    file_roles = implementation_file_roles(metadata)
    if not implementation_query_prefers_runtime_main_entrypoint(query):
        return 0
    if "runtime_entrypoint_surface" in file_roles:
        return 1
    if not implementation_allows_path_fallback(metadata):
        return 0
    if not file_path:
        return 0
    norm = (file_path or "").replace("\\", "/").lower()
    runtime_main_suffixes = (
        "/src/main.rs",
        "/src/main.py",
        "/src/main.ts",
        "/src/main.tsx",
        "/src/main.js",
        "/src/main.jsx",
    )
    if norm.endswith(runtime_main_suffixes):
        return 1
    if norm.endswith("/main.go") and ("/cmd/" in norm or norm.startswith("cmd/")):
        return 1
    return 0


def implementation_exact_signature_symbol_hit(content: str | None, query: str) -> int:
    text = (content or "").strip()
    if not text:
        return 0
    header = "\n".join(text.splitlines()[:3]).lower()
    symbols = implementation_query_symbols(query)
    hits = 0
    for symbol in symbols:
        patterns = [
            rf"\bpub\s+fn\s+{re.escape(symbol)}\s*\(",
            rf"\bfn\s+{re.escape(symbol)}\s*\(",
            rf"\bdef\s+{re.escape(symbol)}\s*\(",
            rf"\basync\s+def\s+{re.escape(symbol)}\s*\(",
            rf"\bfunction\s+{re.escape(symbol)}\s*\(",
            rf"\bexport\s+(?:async\s+)?function\s+{re.escape(symbol)}\s*\(",
            rf"\b{re.escape(symbol)}\s*:\s*function\b",
        ]
        if any(re.search(pattern, header) for pattern in patterns):
            hits += 1
    for pattern in _implementation_definition_declaration_patterns(query):
        if re.search(pattern, header):
            hits += 1
    return hits


def implementation_export_hit(content: str | None, file_path: str | None, meta: dict) -> int:
    file_roles = implementation_file_roles(meta)
    allows_path_fallback = implementation_allows_path_fallback(meta)
    node_types = implementation_node_types(meta)
    path = (file_path or "").replace("\\", "/").lower()
    text = (content or "").lower()
    score = 0
    if node_types & EXPORT_NODE_TYPES:
        score += 1
    if (
        allows_path_fallback
        and ("/src/lib.rs" in path or path.endswith("/lib.rs") or path.endswith("/__init__.py"))
    ):
        if "pub fn " in text or "pub use " in text or "__all__" in text or "export " in text:
            score += 1
    if {"library_facade_surface", "api_surface"} & file_roles:
        if "pub fn " in text or "pub use " in text or "__all__" in text or "export " in text:
            score += 1
    if re.search(r"\bpub\s+use\b", text) or re.search(r"\bexport\s+(?:\{|\*)", text):
        score += 1
    return score


def implementation_reexport_surface_hit(
    content: str | None,
    file_path: str | None,
    meta: dict,
    *,
    definition_hit: int,
    signature_hit: int,
) -> int:
    if definition_hit > 0 or signature_hit > 0:
        return 0
    file_roles = implementation_file_roles(meta)
    if "library_facade_surface" in file_roles:
        return 1
    if not implementation_allows_path_fallback(meta):
        return 0
    path = (file_path or "").replace("\\", "/").lower()
    text = (content or "").lower()
    node_types = implementation_node_types(meta)
    reexport_like = any(
        (
            re.search(r"\bfrom\s+\.", text),
            re.search(r"\bpub\s+use\b", text),
            re.search(r"\b__all__\b", text),
            re.search(r"\bexport\s+(?:\{|\*)", text),
        )
    )
    if not reexport_like:
        return 0
    if path.endswith("/__init__.py") or path.endswith("/__init__.pyi") or path.endswith("/lib.rs"):
        return 1
    if node_types & EXPORT_NODE_TYPES:
        return 1
    return 0


def implementation_facade_surface_hit(
    file_path: str | None,
    meta: dict,
    *,
    symbol_hit: int,
    declared_symbol_hit: int,
    definition_hit: int,
    signature_hit: int,
    export_hit: int,
    api_context_hit: int,
) -> int:
    if symbol_hit <= 0:
        return 0
    if declared_symbol_hit > 0 or definition_hit > 0 or signature_hit > 0:
        return 0
    file_roles = implementation_file_roles(meta)
    if "library_facade_surface" in file_roles:
        return 1
    if not implementation_allows_path_fallback(meta):
        return 0
    node_types = implementation_node_types(meta)
    declaration_like = bool(node_types & DECLARATION_NODE_TYPES)
    module_like = bool(node_types & MODULE_NODE_TYPES)
    chunk_role = implementation_chunk_role(meta, file_path)
    if export_hit > 0 or api_context_hit > 0:
        return 1
    if chunk_role == "definition" and (declaration_like or module_like):
        return 1
    return 0


def implementation_api_context_hit(meta: dict) -> int:
    if not isinstance(meta, dict):
        return 0
    context_path = meta.get("context_path")
    if not isinstance(context_path, list):
        return 0
    lowered = {str(part).strip().lower() for part in context_path if str(part).strip()}
    return 1 if {"api", "public", "exports"} & lowered else 0


def implementation_view_body_priority(meta: dict, query: str, query_class: str) -> int:
    if query_class not in {"implementation_search", "implementation_explanation", "api_definition_lookup"}:
        return 0
    file_roles = implementation_file_roles(meta)
    if "view_surface" not in file_roles:
        return 0
    context_path = meta.get("context_path")
    if not isinstance(context_path, list) or not context_path:
        return 0
    lowered = [str(part).strip().lower() for part in context_path if str(part).strip()]
    if not lowered or lowered[-1] != "body":
        return 0
    query_symbols = implementation_query_symbols(query)
    if {"sidebar", "toolbar", "panel", "inspector", "canvas", "editor"} & query_symbols:
        return 5
    return 3


NODE_TYPE_POLICY_USAGE = {
    "declaration": {"priority": 2, "score": -0.01},
    "callsite": {"priority": 4, "score": 0.05},
    "export": {"priority": 0, "score": 0.0},
    "type_or_module": {"priority": 0, "score": 0.0},
}

NODE_TYPE_POLICY_DEFINITION = {
    "declaration": {"priority": 4, "score": 0.05},
    "callsite": {"priority": -4, "score": -0.06},
    "export": {"priority": 2, "score": 0.03},
    "type_or_module": {"priority": 1, "score": 0.01},
}

NODE_TYPE_POLICY_EXPLANATION = {
    "declaration": {"priority": 1, "score": -0.02},
    "callsite": {"priority": 0, "score": -0.02},
    "export": {"priority": 1, "score": 0.01},
    "type_or_module": {"priority": 0, "score": 0.0},
}

NODE_TYPE_POLICY_IMPLEMENTATION = {
    "declaration": {"priority": 2, "score": 0.03},
    "callsite": {"priority": 1, "score": -0.01},
    "export": {"priority": 0, "score": 0.03},
    "type_or_module": {"priority": 0, "score": 0.01},
}


def implementation_node_type_policy(query_class: str) -> dict[str, dict[str, float | int]]:
    if query_class_prefers_usage(query_class):
        return NODE_TYPE_POLICY_USAGE
    if query_class == "implementation_explanation":
        return NODE_TYPE_POLICY_EXPLANATION
    if query_class_prefers_definitions(query_class):
        return NODE_TYPE_POLICY_DEFINITION
    return NODE_TYPE_POLICY_IMPLEMENTATION


def implementation_node_type_priority(meta: dict, query_class: str) -> int:
    node_types = implementation_node_types(meta)
    declaration_like = bool(node_types & DECLARATION_NODE_TYPES)
    callsite_like = bool(node_types & CALLSITE_NODE_TYPES)
    export_like = bool(node_types & EXPORT_NODE_TYPES)
    type_like = bool(node_types & TYPE_DEFINITION_NODE_TYPES)
    module_like = bool(node_types & MODULE_NODE_TYPES)
    policy = implementation_node_type_policy(query_class)
    score = 0
    if declaration_like:
        score += int(policy["declaration"]["priority"])
    if export_like:
        score += int(policy["export"]["priority"])
    if type_like or module_like:
        score += int(policy["type_or_module"]["priority"])
    if callsite_like and not declaration_like:
        score += int(policy["callsite"]["priority"])
    elif callsite_like and query_class_prefers_usage(query_class):
        score += int(policy["callsite"]["priority"])
    return score


def implementation_result_role(
    file_path: str | None,
    meta: dict,
    *,
    definition_hit: int,
    export_hit: int,
    api_entrypoint_hit: int,
) -> str:
    path = (file_path or "").replace("\\", "/").lower()
    node_types = implementation_node_types(meta)
    declaration_like = bool(node_types & DECLARATION_NODE_TYPES)
    callsite_like = bool(node_types & CALLSITE_NODE_TYPES)
    api_context_hit = implementation_api_context_hit(meta)
    chunk_role = implementation_chunk_role(meta, file_path)
    file_roles = implementation_file_roles(meta)
    allows_path_fallback = implementation_allows_path_fallback(meta)
    if "generated_surface" in file_roles:
        return "generated_surface"
    if "binding_surface" in file_roles:
        return "generated_surface"
    if allows_path_fallback and is_generated_implementation_surface_path(file_path):
        return "generated_surface"
    if allows_path_fallback and is_low_signal_binding_surface_path(file_path):
        return "generated_surface"
    if "docs_surface" in file_roles and "implementation_surface" not in file_roles:
        return "docs"
    if allows_path_fallback and is_doc_like_path(file_path, file_roles):
        return "docs"
    if "config_surface" in file_roles and "implementation_surface" not in file_roles:
        return "supporting_context"
    if file_roles & {"example_surface", "test_surface"}:
        return "test_example"
    if chunk_role in {"example_usage", "test_usage"}:
        return "test_example"
    if chunk_role == "script_support":
        return "supporting_context"
    if chunk_role == "canonical_dispatcher_definition":
        return "canonical_definition"
    if chunk_role == "profile_definition":
        return "supporting_context"
    if chunk_role == "usage" and definition_hit <= 0 and export_hit <= 0:
        return "usage_callsite"
    if chunk_role == "definition" and (api_entrypoint_hit > 0 or api_context_hit > 0):
        return "public_api_definition"
    if chunk_role == "definition" and export_hit > 0:
        return "canonical_definition"
    if chunk_role == "definition" and definition_hit > 0:
        return "internal_implementation"
    if allows_path_fallback and is_usage_heavy_path(file_path):
        if callsite_like and definition_hit <= 0 and export_hit <= 0:
            if any(segment in path for segment in ("/tests/", "/test/", "/e2e/", "/examples/", "/spec/")):
                return "test_example"
            return "usage_callsite"
    if declaration_like and (api_entrypoint_hit > 0 or api_context_hit > 0 or "api_surface" in file_roles):
        return "public_api_definition"
    if declaration_like and export_hit > 0:
        return "canonical_definition"
    if declaration_like and definition_hit > 0:
        return "internal_implementation"
    if "dispatcher_surface" in file_roles or "library_facade_surface" in file_roles:
        return "canonical_definition"
    if "profile_surface" in file_roles:
        return "supporting_context"
    if callsite_like:
        return "usage_callsite"
    if allows_path_fallback and is_usage_heavy_path(file_path):
        return "test_example"
    return "internal_implementation" if declaration_like else "supporting_context"


ROLE_POLICY_USAGE = {
    "test_example": {"priority": 7, "score": 0.10},
    "usage_callsite": {"priority": 6, "score": 0.07},
    "internal_implementation": {"priority": 4, "score": 0.02},
    "canonical_definition": {"priority": 3, "score": 0.0},
    "public_api_definition": {"priority": 2, "score": -0.01},
    "supporting_context": {"priority": 1, "score": 0.0},
    "generated_surface": {"priority": 0, "score": 0.0},
    "docs": {"priority": 0, "score": 0.0},
}

ROLE_POLICY_EXPLANATION = {
    "internal_implementation": {"priority": 7, "score": 0.09},
    "canonical_definition": {"priority": 6, "score": 0.05},
    "public_api_definition": {"priority": 5, "score": 0.02},
    "supporting_context": {"priority": 3, "score": 0.01},
    "usage_callsite": {"priority": 1, "score": -0.06},
    "test_example": {"priority": 0, "score": -0.08},
    "generated_surface": {"priority": 0, "score": 0.0},
    "docs": {"priority": 0, "score": 0.0},
}

ROLE_POLICY_DEFINITION = {
    "public_api_definition": {"priority": 7, "score": 0.10},
    "canonical_definition": {"priority": 6, "score": 0.07},
    "internal_implementation": {"priority": 5, "score": 0.03},
    "supporting_context": {"priority": 3, "score": 0.0},
    "usage_callsite": {"priority": 1, "score": -0.07},
    "test_example": {"priority": 0, "score": -0.06},
    "generated_surface": {"priority": 0, "score": 0.0},
    "docs": {"priority": 0, "score": 0.0},
}

ROLE_POLICY_IMPLEMENTATION = {
    "internal_implementation": {"priority": 5, "score": 0.04},
    "canonical_definition": {"priority": 4, "score": 0.03},
    "public_api_definition": {"priority": 4, "score": 0.02},
    "supporting_context": {"priority": 3, "score": 0.0},
    "usage_callsite": {"priority": 2, "score": 0.0},
    "test_example": {"priority": 1, "score": -0.01},
    "generated_surface": {"priority": 0, "score": 0.0},
    "docs": {"priority": 0, "score": 0.0},
}


def implementation_role_policy(query_class: str) -> dict[str, dict[str, float | int]]:
    if query_class_prefers_usage(query_class):
        return ROLE_POLICY_USAGE
    if query_class == "implementation_explanation":
        return ROLE_POLICY_EXPLANATION
    if query_class_prefers_definitions(query_class):
        return ROLE_POLICY_DEFINITION
    return ROLE_POLICY_IMPLEMENTATION


def implementation_role_priority(role: str, query_class: str) -> int:
    policy = implementation_role_policy(query_class)
    entry = policy.get(role, {})
    return int(entry.get("priority", 0) or 0)


def implementation_role_score(role: str, query_class: str) -> float:
    policy = implementation_role_policy(query_class)
    entry = policy.get(role, {})
    return float(entry.get("score", 0.0) or 0.0)


def implementation_node_type_score(meta: dict, query_class: str) -> float:
    node_types = implementation_node_types(meta)
    declaration_like = bool(node_types & DECLARATION_NODE_TYPES)
    callsite_like = bool(node_types & CALLSITE_NODE_TYPES)
    export_like = bool(node_types & EXPORT_NODE_TYPES)
    type_like = bool(node_types & TYPE_DEFINITION_NODE_TYPES)
    module_like = bool(node_types & MODULE_NODE_TYPES)
    policy = implementation_node_type_policy(query_class)
    score = 0.0
    if declaration_like:
        score += float(policy["declaration"]["score"])
    if export_like:
        score += float(policy["export"]["score"])
    if type_like or module_like:
        score += float(policy["type_or_module"]["score"])
    if callsite_like and not declaration_like:
        score += float(policy["callsite"]["score"])
    elif callsite_like and query_class_prefers_usage(query_class):
        score += float(policy["callsite"]["score"])
    return score


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

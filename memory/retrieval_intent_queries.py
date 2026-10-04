"""Implementation retrieval query intent classification and hints."""

from __future__ import annotations

import re

from memory.retrieval_query import (
    implementation_query_definition_subject_identifiers,
    implementation_query_path_hints,
    implementation_query_symbols,
)

__all__ = [
    "implementation_expected_runtime_entrypoint_paths",
    "implementation_inferred_filename_hints",
    "implementation_noise_exclude_patterns",
    "implementation_provider_query_tokens",
    "implementation_query_accepts_generated_surfaces",
    "implementation_query_class",
    "implementation_query_exact_identifiers",
    "implementation_query_intent",
    "implementation_query_prefers_command_definition",
    "implementation_query_prefers_dispatchers",
    "implementation_query_prefers_provider_wiring",
    "implementation_query_prefers_request_routing",
    "implementation_query_prefers_runtime_main_entrypoint",
    "implementation_query_prefers_supporting_context",
    "implementation_query_relaxes_dir_cap",
    "query_class_prefers_definitions",
    "query_class_prefers_usage",
    "usage_query_prefers_example_results",
    "usage_query_prefers_test_results",
]


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

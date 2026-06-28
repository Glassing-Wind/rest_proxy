"""Implementation retrieval scoring primitives."""

from __future__ import annotations

import re

from _semantic_contract import has_focused_dispatcher_anchor_contract
from memory.retrieval_intent import (
    implementation_inferred_filename_hints,
    implementation_provider_query_tokens,
    implementation_query_exact_identifiers,
    implementation_query_path_hints,
    implementation_query_prefers_command_definition,
    implementation_query_prefers_dispatchers,
    implementation_query_prefers_request_routing,
    implementation_query_prefers_runtime_main_entrypoint,
)
from memory.retrieval_metadata import coerce_meta
from memory.retrieval_query import (
    _implementation_definition_declaration_patterns,
    implementation_query_member_exprs,
    implementation_query_symbols,
)
from memory.retrieval_semantics import (
    DECLARATION_NODE_TYPES,
    EXPORT_NODE_TYPES,
    MODULE_NODE_TYPES,
    implementation_allows_path_fallback,
    implementation_chunk_role,
    implementation_declared_symbol_roles,
    implementation_file_roles,
    implementation_has_file_roles,
    implementation_member_usages,
    implementation_node_types,
)

__all__ = [
    "implementation_api_context_hit",
    "implementation_api_entrypoint_hit",
    "implementation_basename_token_hit",
    "implementation_command_definition_priority",
    "implementation_controller_entity_hit",
    "implementation_declared_symbol_hit",
    "implementation_definition_hit",
    "implementation_dispatcher_contract_priority",
    "implementation_dispatcher_priority",
    "implementation_exact_identifier_hit",
    "implementation_exact_member_usage_hit",
    "implementation_exact_member_usage_site_hit",
    "implementation_exact_signature_symbol_hit",
    "implementation_export_hit",
    "implementation_facade_surface_hit",
    "implementation_path_hint_hit",
    "implementation_provider_wiring_penalty",
    "implementation_provider_wiring_priority",
    "implementation_reexport_surface_hit",
    "implementation_request_handler_priority",
    "implementation_routing_priority",
    "implementation_runtime_main_entrypoint_hit",
    "implementation_runtime_main_entrypoint_hit_with_meta",
    "implementation_server_infra_penalty",
    "implementation_symbol_hit",
    "implementation_view_body_priority",
]


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


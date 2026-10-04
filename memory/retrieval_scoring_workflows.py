"""Workflow-specific implementation retrieval scoring primitives."""

from __future__ import annotations

import re

from _semantic_contract import has_focused_dispatcher_anchor_contract
from memory.retrieval_intent import (
    implementation_provider_query_tokens,
    implementation_query_exact_identifiers,
    implementation_query_prefers_command_definition,
    implementation_query_prefers_dispatchers,
    implementation_query_prefers_request_routing,
)
from memory.retrieval_query import implementation_query_symbols
from memory.retrieval_semantics import (
    implementation_declared_symbol_roles,
    implementation_file_roles,
    implementation_has_file_roles,
)


def _symbol_candidates(meta: dict) -> set[str]:
    candidates: set[str] = set()
    if not isinstance(meta, dict):
        return candidates
    for key in ("declared_symbols", "file_symbols"):
        values = meta.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value).strip().lower()
            if text:
                candidates.add(text)
    return candidates


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


def implementation_dispatcher_priority(meta: dict, file_path: str | None, query: str) -> int:
    if not implementation_query_prefers_dispatchers(query):
        return 0
    if not isinstance(meta, dict):
        return 0
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    symbol_roles = implementation_declared_symbol_roles(meta)
    file_roles = implementation_file_roles(meta)
    candidates = _symbol_candidates(meta)
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
    candidates = _symbol_candidates(meta)
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
    candidates = _symbol_candidates(meta)
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
    candidates = _symbol_candidates(meta)
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
    candidates = _symbol_candidates(meta)
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

    symbol_candidates = _symbol_candidates(meta)

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
    candidates = _symbol_candidates(meta)
    infra_prefixes = ("netservice", "advertise", "resolve", "discover", "browse", "completeboost")
    if any(symbol.startswith(infra_prefixes) for symbol in candidates):
        penalty = max(penalty, 0.18 if grpc_routing_query else 0.08)
    return penalty


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

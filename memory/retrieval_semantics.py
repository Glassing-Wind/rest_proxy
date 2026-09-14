"""Semantic metadata interpretation helpers for retrieval ranking."""

from __future__ import annotations

import re

from memory.retrieval_metadata import coerce_meta
from memory.retrieval_surfaces import (
    is_doc_like_path,
    is_generated_implementation_surface_path,
    is_low_signal_binding_surface_path,
    is_low_signal_parser_data_path,
    is_low_signal_support_path,
)

__all__ = [
    "CALLSITE_NODE_TYPES",
    "DECLARATION_NODE_TYPES",
    "EXPORT_NODE_TYPES",
    "MODULE_NODE_TYPES",
    "TYPE_DEFINITION_NODE_TYPES",
    "apply_result_surface_flags",
    "implementation_allows_path_fallback",
    "implementation_chunk_role",
    "implementation_declared_symbol_roles",
    "implementation_declares_callable",
    "implementation_file_roles",
    "implementation_has_file_roles",
    "implementation_is_profile_candidate",
    "implementation_member_usages",
    "implementation_node_types",
]


def implementation_member_usages(meta: dict) -> set[str]:
    if not isinstance(meta, dict):
        return set()
    member_usages = meta.get("member_usages")
    if not isinstance(member_usages, list):
        return set()
    return {
        str(expr).strip().lower()
        for expr in member_usages
        if str(expr).strip()
    }


def implementation_file_roles(meta: dict) -> set[str]:
    if not isinstance(meta, dict):
        return set()
    file_roles = meta.get("file_roles")
    if not isinstance(file_roles, list):
        return set()
    return {
        str(role).strip().lower()
        for role in file_roles
        if str(role).strip()
    }


def apply_result_surface_flags(result: dict) -> dict:
    """Attach role-first surface classifications used by search ranking."""
    meta = coerce_meta(result)
    roles = implementation_file_roles(meta)
    file_path = result.get("file_path")
    result["doc_like"] = is_doc_like_path(file_path, roles)
    result["low_signal_parser_data"] = is_low_signal_parser_data_path(file_path, roles)
    result["low_signal_binding_surface"] = is_low_signal_binding_surface_path(file_path, roles)
    result["generated_implementation_surface"] = is_generated_implementation_surface_path(
        file_path, roles
    )
    result["low_signal_support_path"] = is_low_signal_support_path(file_path, roles)
    return result


def implementation_has_file_roles(meta: dict) -> bool:
    if not isinstance(meta, dict):
        return False
    return isinstance(meta.get("file_roles"), list)


def implementation_is_profile_candidate(file_path: str | None, meta: dict) -> bool:
    """Return whether a row is profile-only rather than an explicit dispatcher."""
    file_roles = implementation_file_roles(meta)
    chunk_role = implementation_chunk_role(meta, file_path)
    dispatcher_roles = {
        "dispatcher_surface",
        "model_dispatcher_surface",
        "provider_dispatcher_surface",
    }
    if file_roles & dispatcher_roles or chunk_role == "canonical_dispatcher_definition":
        return False
    norm = (file_path or "").replace("\\", "/").lower()
    return (
        "/profiles/" in norm
        or "profile_surface" in file_roles
        or chunk_role == "profile_definition"
    )


def implementation_allows_path_fallback(meta: dict) -> bool:
    return False


def implementation_declared_symbol_roles(meta: dict) -> dict[str, set[str]]:
    if not isinstance(meta, dict):
        return {}
    raw = meta.get("declared_symbol_roles")
    if not isinstance(raw, dict):
        return {}
    roles: dict[str, set[str]] = {}
    for symbol, symbol_roles in raw.items():
        normalized = str(symbol).strip().lower()
        if not normalized or not isinstance(symbol_roles, list):
            continue
        lowered = {
            str(role).strip().lower()
            for role in symbol_roles
            if str(role).strip()
        }
        if lowered:
            roles[normalized] = lowered
    return roles


def implementation_chunk_role(meta: dict, file_path: str | None = None) -> str:
    if not isinstance(meta, dict):
        meta = {}
    role = meta.get("chunk_role")
    if isinstance(role, str) and role.strip():
        return role.strip().lower()
    file_roles = implementation_file_roles(meta)
    if "example_surface" in file_roles:
        return "example_usage"
    if "test_surface" in file_roles:
        return "test_usage"
    if "implementation_surface" in file_roles:
        return ""
    if "docs_surface" in file_roles:
        return "docs_support"
    if "config_surface" in file_roles:
        return "config_support"
    if "support_surface" in file_roles:
        return "script_support"
    if not implementation_allows_path_fallback(meta):
        return ""
    path = (file_path or "").replace("\\", "/").lower()
    if not path:
        return ""
    if (
        path.startswith("examples/")
        or "/examples/" in path
        or path.startswith("samples/")
        or "/samples/" in path
    ):
        return "example_usage"
    if any(segment in path for segment in ("/tests/", "/test/", "/e2e/", "/spec/")):
        return "test_usage"
    if path.startswith("scripts/") or "/scripts/" in path:
        return "script_support"
    return ""


def implementation_declares_callable(meta: dict, content: str | None = None) -> bool:
    if not isinstance(meta, dict):
        meta = {}
    node_types = implementation_node_types(meta)
    callable_node_types = {
        "function_definition",
        "function_declaration",
        "function_item",
        "method_definition",
        "method_declaration",
        "init_declaration",
        "deinit_declaration",
        "protocol_function_declaration",
    }
    if node_types & callable_node_types:
        return True
    text = (content or "").strip().lower()
    if not text:
        return False
    return bool(
        re.search(r"\b(?:async\s+)?def\s+[a-z_][a-z0-9_]*\s*\(", text)
        or re.search(r"\bfunc\s+[a-z_][a-z0-9_]*\s*\(", text)
        or re.search(r"\bfunction\s+[a-z_][a-z0-9_]*\s*\(", text)
        or re.search(r"\b(?:public\s+|private\s+|internal\s+|open\s+)?static\s+func\s+[a-z_][a-z0-9_]*\s*\(", text)
    )


DECLARATION_NODE_TYPES = {
    "function_definition",
    "function_declaration",
    "function_item",
    "method_definition",
    "method_declaration",
    "method_item",
    "impl_item",
    "impl_block",
    "class_definition",
    "class_declaration",
    "class_specifier",
    "struct_item",
    "struct_specifier",
    "enum_item",
    "enum_specifier",
    "type_alias_declaration",
    "module",
    "mod_item",
    "source_file",
}

EXPORT_NODE_TYPES = {
    "export_statement",
    "export_clause",
    "export_specifier",
    "public_item",
}

CALLSITE_NODE_TYPES = {
    "call_expression",
    "call",
    "expression_statement",
    "match_expression",
    "await_expression",
    "argument_list",
}

TYPE_DEFINITION_NODE_TYPES = {
    "class_definition",
    "class_declaration",
    "class_specifier",
    "struct_item",
    "struct_specifier",
    "enum_item",
    "enum_specifier",
    "type_alias_declaration",
}

MODULE_NODE_TYPES = {"module", "mod_item", "source_file"}


def implementation_node_types(meta: dict) -> set[str]:
    if not isinstance(meta, dict):
        return set()
    node_types = meta.get("node_types")
    if not isinstance(node_types, list):
        return set()
    return {
        str(node_type).strip().lower()
        for node_type in node_types
        if str(node_type).strip()
    }


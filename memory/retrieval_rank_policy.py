"""Role and node-type policy for implementation retrieval ranking."""

from __future__ import annotations

from memory.retrieval_intent import query_class_prefers_definitions, query_class_prefers_usage
from memory.retrieval_scoring import implementation_api_context_hit
from memory.retrieval_semantics import (
    CALLSITE_NODE_TYPES,
    DECLARATION_NODE_TYPES,
    EXPORT_NODE_TYPES,
    MODULE_NODE_TYPES,
    TYPE_DEFINITION_NODE_TYPES,
    implementation_allows_path_fallback,
    implementation_chunk_role,
    implementation_file_roles,
    implementation_node_types,
)
from memory.retrieval_surfaces import (
    is_doc_like_path,
    is_generated_implementation_surface_path,
    is_low_signal_binding_surface_path,
    is_usage_heavy_path,
)

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

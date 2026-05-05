"""Shared semantic indexing contract version helpers."""

from __future__ import annotations


SEMANTIC_CONTRACT_VERSION = 1
FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION = 1
FOCUSED_DISPATCHER_ANCHOR_CAPABILITY = (
    f"focused_dispatcher_anchor_v{FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION}"
)


def normalize_semantic_contract_version(raw: object) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def has_current_semantic_contract(meta: dict | None) -> bool:
    if not isinstance(meta, dict):
        return False
    return normalize_semantic_contract_version(meta.get("semantic_contract_version")) == SEMANTIC_CONTRACT_VERSION


def normalize_semantic_contract_capabilities(raw: object) -> set[str]:
    if isinstance(raw, (list, tuple, set)):
        return {str(value).strip() for value in raw if str(value).strip()}
    if isinstance(raw, str) and raw.strip():
        return {raw.strip()}
    return set()


def normalize_focused_dispatcher_anchor_contract_version(raw: object) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def has_focused_dispatcher_anchor_contract(meta: dict | None) -> bool:
    if not isinstance(meta, dict):
        return False
    contract_version = normalize_focused_dispatcher_anchor_contract_version(
        meta.get("focused_dispatcher_anchor_contract_version")
    )
    if contract_version == FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION:
        return True
    capabilities = normalize_semantic_contract_capabilities(meta.get("semantic_contract_capabilities"))
    return FOCUSED_DISPATCHER_ANCHOR_CAPABILITY in capabilities

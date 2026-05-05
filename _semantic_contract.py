"""Shared semantic indexing contract version helpers."""

from __future__ import annotations


SEMANTIC_CONTRACT_VERSION = 1


def normalize_semantic_contract_version(raw: object) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def has_current_semantic_contract(meta: dict | None) -> bool:
    if not isinstance(meta, dict):
        return False
    return normalize_semantic_contract_version(meta.get("semantic_contract_version")) == SEMANTIC_CONTRACT_VERSION

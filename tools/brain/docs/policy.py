"""Retrieval policy helpers for documentation search."""

from __future__ import annotations

import re

from tools.brain.docs.config import topic_family_patterns


_NEO4J_OPERATIONAL_TERMS = (
    "deadlock",
    "deadlockdetected",
    "lock",
    "locking",
    "contention",
    "timeout",
    "transient",
)


def is_operational_query(topic: str, query: str) -> bool:
    lower = f"{topic} {query}".lower()
    return any(term in lower for term in _NEO4J_OPERATIONAL_TERMS)


def expand_query(topic: str, query: str) -> str:
    expanded = [query.strip()]
    lower = f"{topic} {query}".lower()
    if "neo4j" in lower or (topic or "").lower() == "neo4j":
        if any(term in lower for term in _NEO4J_OPERATIONAL_TERMS):
            expanded.extend(
                [
                    "deadlock locking lock contention retry retryable transient transaction timeout",
                    "\"How to diagnose locking issues\" OR \"transaction and lock timeouts\"",
                ]
            )
        if "deadlock" in lower or "deadlockdetected" in lower:
            expanded.extend(
                [
                    "\"DeadlockDetected\"",
                    "\"Neo.TransientError.Transaction.DeadlockDetected\"",
                ]
            )
    return " OR ".join(part for part in expanded if part)


def extract_exact_terms(topic: str, query: str) -> list[str]:
    exact_terms = set(
        match.strip("\"'")
        for match in re.findall(r"(Neo\.[A-Za-z0-9_.]+|[A-Z][A-Za-z0-9_.]*Detected)", query)
        if match.strip("\"'")
    )
    lower = f"{topic} {query}".lower()
    if "neo4j" in lower or (topic or "").lower() == "neo4j":
        if "deadlock" in lower or "deadlockdetected" in lower:
            exact_terms.update(
                {
                    "DeadlockDetected",
                    "Neo.TransientError.Transaction.DeadlockDetected",
                }
            )
    return sorted(exact_terms)


def doc_type_from_result(url: str, title: str, metadata: dict | None = None) -> str:
    if metadata:
        doc_type = str(metadata.get("doc_type") or "").strip()
        if doc_type:
            return doc_type
    url_lower = (url or "").lower()
    title_lower = (title or "").lower()
    if "/developer/kb/" in url_lower or "knowledge base" in title_lower:
        return "knowledge-base"
    if "/operations-manual/" in url_lower:
        return "operations-manual"
    if "/python-manual/" in url_lower:
        return "python-driver-manual"
    if "/java-reference/" in url_lower:
        return "java-reference"
    if "/cypher-manual/" in url_lower:
        return "cypher-manual"
    return "documentation"


def topic_filter_sql(topic: str) -> tuple[str, dict[str, str]]:
    patterns = topic_family_patterns(topic)
    if not patterns:
        return "", {}
    if len(patterns) == 1 and "%" not in patterns[0]:
        return "AND source = %(topic)s", {"topic": patterns[0]}

    clauses = []
    params: dict[str, str] = {}
    for idx, pattern in enumerate(patterns):
        key = f"topic_{idx}"
        if "%" in pattern:
            clauses.append(f"source ILIKE %({key})s")
        else:
            clauses.append(f"source = %({key})s")
        params[key] = pattern
    return "AND (" + " OR ".join(clauses) + ")", params

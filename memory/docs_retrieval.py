"""memory/docs_retrieval.py — core documentation retrieval and ranking policy."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Topics and Families
# ---------------------------------------------------------------------------
TOPIC_FAMILIES = {
    "neo4j": ["neo4j", "neo4j-%"],
}

_NEO4J_OPERATIONAL_TERMS = (
    "deadlock",
    "deadlockdetected",
    "lock",
    "locking",
    "contention",
    "timeout",
    "transient",
)


def topic_family_patterns(topic: str) -> list[str]:
    topic = (topic or "").strip()
    if not topic:
        return []
    return list(TOPIC_FAMILIES.get(topic, [topic]))


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


def _docs_legacy_source_exclusion_sql(topic: str) -> tuple[str, dict[str, str]]:
    if topic:
        return "", {}
    excluded = [
        "%test%",
        "%tmp%",
        "%scratch%",
        "%demo%",
        "%sample%",
        "%experimental%",
        "%staging%",
        "%draft%",
    ]
    clauses = []
    params: dict[str, str] = {}
    for idx, pattern in enumerate(excluded):
        key = f"ex{idx}"
        clauses.append(f"source NOT ILIKE %({key})s")
        params[key] = pattern
    legacy_filter = (
        "AND ("
        "metadata ? 'doc_type' "
        "OR metadata ? 'source_type' "
        "OR ("
        + " AND ".join(clauses)
        + "))"
    )
    return legacy_filter, params


# ---------------------------------------------------------------------------
# Diversification and Reranking
# ---------------------------------------------------------------------------


def _url_diverse_docs_selection(results: list[dict], k: int) -> list[dict]:
    """Conservative fallback when the lower-level contract is unavailable."""
    seen_url: dict[str, int] = {}
    diverse = []
    for result in results:
        source_url = result["source_url"]
        if seen_url.get(source_url, 0) < 1:
            diverse.append(result)
            seen_url[source_url] = seen_url.get(source_url, 0) + 1
        if len(diverse) >= k:
            break
    return diverse[:k]


def _apply_diverse_docs_selection(
    results: list[dict], *, query: str, k: int
) -> tuple[list[dict], dict | None]:
    """Apply the lower-level duplicate/diversification contract in docs mode."""
    fallback_trace = {
        "suppression_policy": "exact_only",
        "experiments": {},
        "selection": {"keep_indices": list(range(min(len(results), k)))},
    }
    if len(results) < 2:
        return results[:k], fallback_trace
    try:
        from memory import retrieval_duplicates
        from memory.retrieval_telemetry import duplicate_experiment_flags_from_env
    except Exception:
        return _url_diverse_docs_selection(results, k), fallback_trace

    experiments = duplicate_experiment_flags_from_env("docs")
    try:
        contract = retrieval_duplicates.rerank_retrieval_results_contract(
            results,
            query=query,
            mode="docs",
            experiments=experiments,
            include_debug=False,
        )
    except Exception:
        return _url_diverse_docs_selection(results, k), fallback_trace

    selection = contract.get("selection") if isinstance(contract, dict) else {}
    keep_indices = selection.get("keep_indices") if isinstance(selection, dict) else None
    if not isinstance(keep_indices, list):
        return _url_diverse_docs_selection(results, k), contract if isinstance(contract, dict) else None

    chosen = _url_diverse_docs_selection(
        [row for row in (contract.get("results") or []) if isinstance(row, dict)],
        k,
    )
    if chosen:
        return chosen, contract if isinstance(contract, dict) else None
    return _url_diverse_docs_selection(results, k), contract if isinstance(contract, dict) else None


# ---------------------------------------------------------------------------
# Retrieval Entrypoint
# ---------------------------------------------------------------------------


async def search_docs(
    query: str,
    query_vector: list[float],
    topic: str = "",
    k: int = 8,
) -> tuple[list[dict], dict | None]:
    """
    Perform hybrid semantic + full-text search on doc_embeddings table.
    Applies topic filtering, operational synonym query expansion, exact terms boost,
    source-based RRF ranking, and duplicate/diversity-aware selection.
    """
    import memory.store as ms

    vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
    expanded_query = expand_query(topic, query)
    exact_terms = extract_exact_terms(topic, query)
    operational_query = is_operational_query(topic, query)
    fetch = min(k * 10, 200)
    topic_sql, topic_params = topic_filter_sql(topic)
    test_exclusion_sql, test_exclusion_params = _docs_legacy_source_exclusion_sql(topic)

    if query.strip():
        sql = f"""
            WITH semantic AS (
                SELECT id, url, metadata->>'title' AS title, chunk_index, content,
                       metadata->'context_path' AS context_path,
                       metadata AS metadata,
                       ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rank
                FROM doc_embeddings WHERE TRUE {topic_sql} {test_exclusion_sql} LIMIT %(fetch)s
            ),
            keyword AS (
                SELECT id, ROW_NUMBER() OVER (
                           ORDER BY ts_rank(search_vec, websearch_to_tsquery('english', %(qt)s)) DESC
                       ) AS rank
                FROM doc_embeddings
                WHERE TRUE {topic_sql} {test_exclusion_sql}
                  AND search_vec @@ websearch_to_tsquery('english', %(qt)s)
                LIMIT %(fetch)s
            ),
            fused AS (
                SELECT s.url, s.title, s.chunk_index, s.content, s.context_path, s.metadata,
                       (
                           2.0/(60+s.rank)
                           + COALESCE(1.0/(60+k.rank), 0.0)
                           + CASE
                               WHEN %(operational_query)s AND s.url ILIKE '%%/developer/kb/%%' THEN 0.030
                               WHEN %(operational_query)s AND s.url ILIKE '%%/docs/operations-manual/%%' THEN 0.024
                               WHEN %(operational_query)s AND s.url ILIKE '%%/docs/python-manual/%%' THEN 0.014
                               WHEN %(operational_query)s AND s.url ILIKE '%%/docs/java-reference/%%' THEN 0.010
                               ELSE 0.0
                             END
                           + CASE
                               WHEN cardinality(%(exact_terms)s::text[]) > 0 AND EXISTS (
                                   SELECT 1
                                   FROM unnest(%(exact_terms)s::text[]) AS term
                                   WHERE lower(s.content) LIKE '%%' || lower(term) || '%%'
                                      OR lower(COALESCE(s.title, '')) LIKE '%%' || lower(term) || '%%'
                                      OR lower(s.url) LIKE '%%' || lower(term) || '%%'
                               ) THEN 0.040
                               ELSE 0.0
                             END
                       ) AS rrf_score
                FROM semantic s LEFT JOIN keyword k ON s.id = k.id
            )
            SELECT url, title, chunk_index, content, rrf_score, context_path, metadata
            FROM fused ORDER BY rrf_score DESC LIMIT %(pool)s
        """
        params = {
            "vec": vec_str,
            "qt": expanded_query,
            "fetch": fetch,
            "k": k,
            "pool": k * 3,
            "exact_terms": exact_terms,
            "operational_query": operational_query,
        }
        params.update(test_exclusion_params)
    else:
        sql = f"""
            SELECT url, metadata->>'title' AS title, chunk_index, content,
                   (1.0/(60 + ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector))) AS rrf_score,
                   metadata->'context_path' AS context_path,
                   metadata
            FROM doc_embeddings WHERE TRUE {topic_sql} {test_exclusion_sql}
            ORDER BY embedding <=> %(vec)s::vector LIMIT %(pool)s
        """
        params = {
            "vec": vec_str,
            "pool": k * 3,
            "exact_terms": exact_terms,
            "operational_query": operational_query,
        }
        params.update(test_exclusion_params)

    params.update(topic_params)

    results = []
    async with ms._pg_pool.connection() as conn:
        await conn.execute("BEGIN")
        async with conn.cursor() as cur:
            await cur.execute("SET LOCAL hnsw.ef_search = 100")
            await cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            await cur.execute(sql, params)
            async for row in cur:
                results.append(
                    {
                        "source_url": row[0],
                        "title": row[1],
                        "chunk_index": row[2],
                        "content": row[3],
                        "rrf_score": row[4],
                        "context_path": row[5] or [],
                        "metadata": row[6] or {},
                    }
                )

    if not results:
        return [], None

    results, trace = _apply_diverse_docs_selection(results, query=query, k=k)
    return results, trace

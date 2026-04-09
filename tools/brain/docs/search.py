"""tools/docs/search.py — documentation search tool."""

import json
import re
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
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


def _is_operational_query(topic: str, query: str) -> bool:
    lower = f"{topic} {query}".lower()
    return any(term in lower for term in _NEO4J_OPERATIONAL_TERMS)


def _expand_query(topic: str, query: str) -> str:
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


def _extract_exact_terms(topic: str, query: str) -> list[str]:
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


def _doc_type_from_result(url: str, title: str, metadata: dict | None = None) -> str:
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


def _topic_filter_sql(topic: str) -> tuple[str, dict[str, str]]:
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


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def search_documentation(query: str, topic: str = "", k: int = 8) -> str:
        """
        Search indexed documentation using hybrid vector + full-text search (RRF).
        Returns relevant chunks with source URLs and titles.

        Args:
            query: Natural language question or keyword search.
            topic: Optional topic filter (e.g. 'neo4j'). Leave empty to search all docs.
            k:     Number of results to return (default 8).
        """
        try:
            import memory.store as ms
            from embedding_service import get_embedding_service

            await ms.open_pool()
            if not ms._pg_pool_available():
                return "Postgres pool not available — check LM_PROXY_PG_DSN in .env"

            svc = get_embedding_service()
            vecs = await svc.embed_batch_async([query])
            query_vec = vecs[0]
            if not query_vec:
                return "Could not generate query embedding."

            vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
            expanded_query = _expand_query(topic, query)
            exact_terms = _extract_exact_terms(topic, query)
            operational_query = _is_operational_query(topic, query)
            fetch = min(k * 10, 200)
            topic_sql, topic_params = _topic_filter_sql(topic)
            test_exclusion_sql = ""
            test_exclusion_params: dict[str, str] = {}
            if not topic:
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
                for idx, pattern in enumerate(excluded):
                    key = f"ex{idx}"
                    clauses.append(f"source NOT ILIKE %({key})s")
                    test_exclusion_params[key] = pattern
                test_exclusion_sql = "AND " + " AND ".join(clauses)

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
                tip = f" (topic='{topic}')" if topic else ""
                return f"No documentation found for: '{query}'{tip}\nRun download_documentation() first."

            seen_url: dict[str, int] = {}
            diverse = []
            for r in results:
                source_url = r["source_url"]
                if seen_url.get(source_url, 0) < 1:
                    diverse.append(r)
                    seen_url[source_url] = seen_url.get(source_url, 0) + 1
            results = diverse[:k]

            lines = [
                f"Documentation search: '{query}'" + (f"  [topic={topic}]" if topic else ""),
                "",
            ]
            for i, r in enumerate(results, 1):
                lines.append(f"[{i}] {r['title'] or r['source_url']}")
                lines.append(f"    URL:   {r['source_url']}")
                metadata = r.get("metadata") or {}
                domain = metadata.get("domain") or urlparse(r["source_url"]).netloc
                doc_type = _doc_type_from_result(r["source_url"], r["title"] or "", metadata)
                lines.append(f"    Source: {domain} [{doc_type}]")
                ctx = r.get("context_path") or []
                if isinstance(ctx, str):
                    try:
                        ctx = json.loads(ctx)
                    except Exception:
                        ctx = []
                if ctx:
                    lines.append(f"    Path:  {' > '.join(ctx)}")
                lines.append(f"    Score: {r['rrf_score']:.4f}")
                lines.append("")
                lines.append(f"    {r['content'][:600].strip().replace(chr(10), ' ')}…")
                lines.append("")
            return "\n".join(lines)

        except Exception as e:
            return f"Error searching documentation: {e}"

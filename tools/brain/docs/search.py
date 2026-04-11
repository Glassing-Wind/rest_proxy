"""tools/docs/search.py — documentation search tool."""

import json
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from tools.brain.docs.policy import (
    doc_type_from_result,
    expand_query,
    extract_exact_terms,
    is_operational_query,
    topic_filter_sql,
)


def _apply_diverse_docs_selection(results: list[dict], *, query: str, k: int) -> tuple[list[dict], dict | None]:
    """Apply the lower-level duplicate/diversification contract in docs mode."""
    if len(results) < 2:
        return results[:k], None
    try:
        from tools.brain.search.semantic_helpers import (
            duplicate_experiment_flags_from_env,
            rerank_retrieval_results_contract,
        )
    except Exception:
        return _url_diverse_docs_selection(results, k), None

    experiments = duplicate_experiment_flags_from_env("docs")
    try:
        contract = rerank_retrieval_results_contract(
            results,
            query=query,
            mode="docs",
            experiments=experiments,
            include_debug=False,
        )
    except Exception:
        return _url_diverse_docs_selection(results, k), None

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
            expanded_query = expand_query(topic, query)
            exact_terms = extract_exact_terms(topic, query)
            operational_query = is_operational_query(topic, query)
            fetch = min(k * 10, 200)
            topic_sql, topic_params = topic_filter_sql(topic)
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

            results, trace = _apply_diverse_docs_selection(results, query=query, k=k)
            if isinstance(trace, dict):
                try:
                    from tools.brain.search.semantic_helpers import append_duplicate_telemetry_event

                    append_duplicate_telemetry_event(
                        trace,
                        query=query,
                        tool="search_documentation",
                        mode="docs",
                        topic=topic,
                    )
                except Exception:
                    pass

            lines = [
                f"Documentation search: '{query}'" + (f"  [topic={topic}]" if topic else ""),
                "",
            ]
            for i, r in enumerate(results, 1):
                lines.append(f"[{i}] {r['title'] or r['source_url']}")
                lines.append(f"    URL:   {r['source_url']}")
                metadata = r.get("metadata") or {}
                domain = metadata.get("domain") or urlparse(r["source_url"]).netloc
                doc_type = doc_type_from_result(r["source_url"], r["title"] or "", metadata)
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

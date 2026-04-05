"""tools/docs/search.py — documentation search tool."""

import json
from mcp.server.fastmcp import FastMCP


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
            fetch = min(k * 10, 200)
            topic_sql = "AND source = %(topic)s" if topic else ""
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
                               ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rank
                        FROM doc_embeddings WHERE TRUE {topic_sql} {test_exclusion_sql} LIMIT %(fetch)s
                    ),
                    keyword AS (
                        SELECT id, ROW_NUMBER() OVER (
                                   ORDER BY ts_rank(search_vec, websearch_to_tsquery('english', %(qt)s)) DESC
                               ) AS rank
                        FROM doc_embeddings
                        WHERE TRUE {topic_sql} {test_exclusion_sql} AND search_vec @@ websearch_to_tsquery('english', %(qt)s)
                        LIMIT %(fetch)s
                    ),
                    fused AS (
                        SELECT s.url, s.title, s.chunk_index, s.content, s.context_path,
                               (2.0/(60+s.rank) + COALESCE(1.0/(60+k.rank), 0.0)) AS rrf_score
                        FROM semantic s LEFT JOIN keyword k ON s.id = k.id
                    )
                    SELECT url, title, chunk_index, content, rrf_score, context_path
                    FROM fused ORDER BY rrf_score DESC LIMIT %(pool)s
                """
                params = {
                    "vec": vec_str,
                    "qt": query,
                    "fetch": fetch,
                    "k": k,
                    "pool": k * 3,
                }
                params.update(test_exclusion_params)
            else:
                sql = f"""
                    SELECT url, metadata->>'title' AS title, chunk_index, content,
                           (1.0/(60 + ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector))) AS rrf_score,
                           metadata->'context_path' AS context_path
                    FROM doc_embeddings WHERE TRUE {topic_sql} {test_exclusion_sql}
                    ORDER BY embedding <=> %(vec)s::vector LIMIT %(pool)s
                """
                params = {"vec": vec_str, "k": k, "pool": k * 3}
                params.update(test_exclusion_params)

            if topic:
                params["topic"] = topic

            results = []
            async with ms._pg_pool.connection() as conn:
                await conn.execute("BEGIN")
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SET LOCAL hnsw.ef_search = 100"
                    )  # resets when connection returns to pool
                    await cur.execute(
                        "SET LOCAL hnsw.iterative_scan = relaxed_order"
                    )  # auto-expands scan for filtered queries
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
                            }
                        )

            if not results:
                tip = f" (topic='{topic}')" if topic else ""
                return f"No documentation found for: '{query}'{tip}\nRun download_documentation() first."

            # ── Source diversity: cap at 2 chunks per domain ─────────────────
            # Prevents a single URL with many chunks from dominating results.
            from urllib.parse import urlparse as _urlparse

            seen_domain: dict[str, int] = {}
            diverse = []
            for r in results:
                domain = _urlparse(r["source_url"]).netloc
                if seen_domain.get(domain, 0) < 2:
                    diverse.append(r)
                    seen_domain[domain] = seen_domain.get(domain, 0) + 1
            results = diverse[:k]

            lines = [
                f"Documentation search: '{query}'"
                + (f"  [topic={topic}]" if topic else ""),
                "",
            ]
            for i, r in enumerate(results, 1):
                lines.append(f"[{i}] {r['title'] or r['source_url']}")
                lines.append(f"    URL:   {r['source_url']}")
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

"""tools/documentation.py — documentation research, crawling, and search tools."""

import os
import sys
import json
import threading
import subprocess
from mcp.server.fastmcp import FastMCP
from _jobs import _JOBS, _JOBS_LOCK, _drain_proc_output, _finalize_job


async def _tavily_search(query: str, max_results: int = 10) -> list:
    """
    Search via Tavily API (async-native, no threading workaround needed).
    Returns a list of dicts with 'url', 'title', 'content' keys.
    Falls back to an empty list if TAVILY_API_KEY is not set.
    """
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return []
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=api_key)
    resp = await client.search(query, max_results=max_results, include_domains=[])
    return resp.get("results", [])


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def research_documentation(topic: str, query: str) -> str:
        """
        Search the web for documentation relevant to a topic/query.
        Returns candidate URLs with titles and snippets for the agent to review.
        Call download_documentation() with chosen URLs to crawl and index them.

        Args:
            topic: Library or product name (e.g. 'neo4j', 'pgvector', 'crawl4ai').
            query: Specific question or feature to find docs for.
        """
        try:
            import httpx
            from urllib.parse import urlparse

            search_query = f"{topic} {query}"
            hits = await _tavily_search(search_query, max_results=10)
            if not hits:
                hits = await _tavily_search(
                    f"{topic} {query} documentation", max_results=10
                )

            if not hits:
                return (
                    f"No results found for: {topic} — {query}  (Is TAVILY_API_KEY set?)"
                )

            lines = [f"Documentation search: '{topic}' — '{query}'", ""]
            seen_domains = set()

            for hit in hits:
                url = hit.get("url", "")
                title = hit.get("title", "")
                body = (hit.get("content") or "")[:120].replace("\n", " ")
                score = hit.get("score", 0)
                lines += [f"• {title}  [{score:.2f}]", f"  {url}", f"  {body}…", ""]

                domain = urlparse(url).netloc
                if domain not in seen_domains:
                    seen_domains.add(domain)
                    llms_url = f"{urlparse(url).scheme}://{domain}/llms.txt"
                    try:
                        async with httpx.AsyncClient(timeout=5) as client:
                            r = await client.get(llms_url)
                            if r.status_code == 200:
                                lines += [
                                    f"  ✅ llms.txt found at {llms_url} (structured page list available)",
                                    "",
                                ]
                    except Exception:
                        pass

            lines += [
                "---",
                f"Found {len(hits)} results. Call download_documentation(urls=[...], topic='{topic}')",
            ]
            lines.append("to crawl, extract, and index any of the above URLs.")
            return "\n".join(lines)
        except Exception as e:
            return f"Error during research: {e}"

    @mcp.tool()
    async def download_documentation(urls: list, topic: str) -> str:
        """
        Crawl, extract, chunk, embed, and index documentation URLs into Postgres.
        Returns immediately with a job_id. Use get_index_status(job_id) to monitor.

        Pages are discovered via llms.txt/sitemap before crawling.
        Content is extracted as native markdown via crawl4ai, then chunked at
        heading-section boundaries using an AST-aware splitter (tree-sitter markdown).
        Each chunk includes a context_path breadcrumb (e.g. ['GRPCServer', 'Error Handling']).
        Idempotent: re-crawling updates existing chunks rather than duplicating.

        Args:
            urls:  List of documentation URLs to crawl (seed URLs).
            topic: Label for these docs (e.g. 'neo4j', 'pgvector').
        """
        try:
            import time, uuid

            if not urls:
                return "Error: no URLs provided."

            base_dir = os.path.dirname(os.path.abspath(__file__))
            # base_dir is tools/ — step up to rest_proxy/
            base_dir = os.path.dirname(base_dir)
            job_id = str(uuid.uuid4())[:8]

            urls_file = os.path.join(base_dir, f"doc_{job_id}_urls.json")
            with open(urls_file, "w") as fh:
                json.dump(list(urls), fh)

            with _JOBS_LOCK:
                _JOBS[job_id] = {
                    "status": "running",
                    "project_id": f"doc:{topic}",
                    "project_path": f"docs://{topic}",
                    "file_count": len(urls),
                    "struct_rc": 0,
                    "sem_rc": None,
                    "logs": [],
                    "started_at": time.time(),
                    "finished_at": None,
                }

            doc_cmd = [
                sys.executable,
                os.path.join(base_dir, "doc_indexer.py"),
                "--urls-file",
                urls_file,
                "--topic",
                topic,
            ]
            proc = subprocess.Popen(
                doc_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            threading.Thread(
                target=_drain_proc_output,
                args=(proc, job_id, "[doc]", "sem_rc"),
                daemon=True,
            ).start()
            threading.Thread(
                target=_finalize_job, args=(job_id, urls_file), daemon=True
            ).start()

            return (
                f"Documentation indexing started in background.\n"
                f"  job_id: {job_id}\n"
                f"  topic:  {topic}\n"
                f"  seeds:  {len(urls)} URL(s)\n"
                f"\nUse get_index_status('{job_id}') to monitor progress.\n"
                f"Use search_documentation(query, topic='{topic}') once done."
            )
        except Exception as e:
            return f"Error starting doc indexer: {e}"

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
            import memory_store as ms
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
            test_exclusion_sql = (
                ""
                if topic
                else (
                    "AND source NOT ILIKE '%test%' "
                    "AND source NOT ILIKE '%tmp%' "
                    "AND source NOT ILIKE '%scratch%' "
                    "AND source NOT ILIKE '%demo%' "
                    "AND source NOT ILIKE '%sample%' "
                    "AND source NOT ILIKE '%experimental%' "
                    "AND source NOT ILIKE '%staging%' "
                    "AND source NOT ILIKE '%draft%'"
                )
            )

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
            else:
                sql = f"""
                    SELECT url, metadata->>'title' AS title, chunk_index, content,
                           (1.0/(60 + ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector))) AS rrf_score,
                           metadata->'context_path' AS context_path
                    FROM doc_embeddings WHERE TRUE {topic_sql} {test_exclusion_sql}
                    ORDER BY embedding <=> %(vec)s::vector LIMIT %(pool)s
                """
                params = {"vec": vec_str, "k": k, "pool": k * 3}

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

    @mcp.tool()
    async def delete_documentation(
        topic: str,
        url_patterns: list | None = None,
        allow_all: bool = False,
        dry_run: bool = False,
    ) -> str:
        """
        Delete indexed documentation chunks for a topic, optionally filtered by URL patterns.

        Args:
            topic:        Topic/source label used during indexing (e.g. 'bfl').
            url_patterns: Optional list of URL patterns (SQL ILIKE). If a pattern
                          omits '%', it is wrapped as '%<pattern>%'.
            allow_all:    If True, allows deleting the entire topic when no
                          url_patterns are provided.
            dry_run:      If True, return counts and sample URLs without deleting.
        """
        try:
            import memory_store as ms

            await ms.open_pool()
            if not ms._pg_pool_available():
                return "Postgres pool not available — check LM_PROXY_PG_DSN in .env"

            patterns = [
                p.strip()
                for p in (url_patterns or [])
                if isinstance(p, str) and p.strip()
            ]
            if not patterns and not allow_all:
                return (
                    "Refusing to delete without url_patterns. "
                    "Provide patterns like ['%fal.ai/%'] or set allow_all=True."
                )

            clauses = ["source = %(topic)s"]
            params: dict[str, str] = {"topic": topic}

            if patterns:
                url_clauses = []
                for i, raw in enumerate(patterns):
                    pat = raw if "%" in raw else f"%{raw}%"
                    key = f"p{i}"
                    params[key] = pat
                    url_clauses.append(f"url ILIKE %({key})s")
                clauses.append("(" + " OR ".join(url_clauses) + ")")

            where_sql = " AND ".join(clauses)

            async with ms._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"SELECT count(*) FROM doc_embeddings WHERE {where_sql}", params
                    )
                    row = await cur.fetchone()
                    count = row[0] if row else 0

                    await cur.execute(
                        f"SELECT url FROM doc_embeddings WHERE {where_sql} "
                        "GROUP BY url ORDER BY url LIMIT 10",
                        params,
                    )
                    sample_urls: list[str] = []
                    async for r in cur:
                        sample_urls.append(r[0])

                    if dry_run:
                        preview = (
                            "\n".join(f"  - {u}" for u in sample_urls) or "  (none)"
                        )
                        return (
                            f"Dry run: {count} chunk(s) would be deleted.\n"
                            f"Sample URLs:\n{preview}"
                        )

                    await cur.execute(
                        f"DELETE FROM doc_embeddings WHERE {where_sql}", params
                    )
                    deleted = cur.rowcount if cur.rowcount is not None else count
                    await conn.commit()

            return f"Deleted {deleted} documentation chunk(s) for topic='{topic}'."
        except Exception as e:
            return f"Error deleting documentation: {e}"

    @mcp.tool()
    async def list_documentation_sources(topic: str = "", limit: int = 20) -> str:
        """
        List indexed documentation topics and domains.

        Args:
            topic: Optional topic/source to list domains for.
            limit: Max rows to return (default 20, max 100).
        """
        try:
            import memory_store as ms

            await ms.open_pool()
            if not ms._pg_pool_available():
                return "Postgres pool not available — check LM_PROXY_PG_DSN in .env"

            limit = max(1, min(int(limit), 100))

            async with ms._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    if topic:
                        await cur.execute(
                            """
                            SELECT count(*) AS chunks, count(DISTINCT url) AS urls
                            FROM doc_embeddings WHERE source = %(topic)s
                            """,
                            {"topic": topic},
                        )
                        row = await cur.fetchone()
                        total_chunks = row[0] if row else 0
                        total_urls = row[1] if row else 0

                        await cur.execute(
                            """
                            SELECT split_part(url, '/', 3) AS domain, count(*) AS chunks
                            FROM doc_embeddings
                            WHERE source = %(topic)s
                            GROUP BY domain
                            ORDER BY chunks DESC
                            LIMIT %(limit)s
                            """,
                            {"topic": topic, "limit": limit},
                        )
                        rows = []
                        async for r in cur:
                            rows.append((r[0], r[1]))

                        if not rows:
                            return f"No documentation found for topic='{topic}'."

                        lines = [
                            f"Documentation domains for topic='{topic}'",
                            f"  Chunks: {total_chunks}",
                            f"  URLs:   {total_urls}",
                            "",
                        ]
                        for domain, chunks in rows:
                            lines.append(f"- {domain}  ({chunks} chunks)")
                        return "\n".join(lines)

                    await cur.execute(
                        """
                        SELECT source,
                               count(*) AS chunks,
                               count(DISTINCT url) AS urls,
                               count(DISTINCT split_part(url, '/', 3)) AS domains
                        FROM doc_embeddings
                        GROUP BY source
                        ORDER BY chunks DESC
                        LIMIT %(limit)s
                        """,
                        {"limit": limit},
                    )
                    rows = []
                    async for r in cur:
                        rows.append((r[0], r[1], r[2], r[3]))

            if not rows:
                return "No documentation topics found."

            lines = ["Documentation topics (by chunk count)", ""]
            for source, chunks, urls, domains in rows:
                lines.append(
                    f"- {source}  ({chunks} chunks, {urls} urls, {domains} domains)"
                )
            lines.append("")
            lines.append("Tip: list_documentation_sources(topic='<name>') for domains.")
            return "\n".join(lines)

        except Exception as e:
            return f"Error listing documentation sources: {e}"

    @mcp.tool()
    async def research_and_index(topic: str, query: str, max_urls: int = 5) -> str:
        """
        Single-call research pipeline: web search → auto-select best URLs → crawl & index.

        Combines research_documentation + download_documentation into one step.
        Searches for documentation relevant to the topic/query, picks the top URLs
        from high-quality domains, and immediately starts background indexing.
        Use get_index_status(job_id) to monitor, search_documentation() once done.

        Args:
            topic:    Library or product name (e.g. 'swift call graph', 'neo4j').
            query:    Specific question or concept to find docs for.
            max_urls: Maximum number of URLs to index (default 5, max 10).
        """
        try:
            from duckduckgo_search import DDGS
            from urllib.parse import urlparse
            import time, uuid

            max_urls = min(int(max_urls), 10)

            # ── 1. Web search ─────────────────────────────────────────────────
            search_query = f"{topic} {query}"
            hits = await _tavily_search(search_query, max_results=20)
            if not hits:
                hits = await _tavily_search(
                    f"{topic} {query} documentation", max_results=20
                )

            if not hits:
                return (
                    f"No results found for: {topic} — {query}  (Is TAVILY_API_KEY set?)"
                )

            # ── 2. Filter + rank URLs ─────────────────────────────────────────
            # Prefer official docs, readthedocs, GitHub, swift.org, etc.
            PREFERRED = (
                "swift.org",
                "docs.",
                "readthedocs",
                "github.com",
                "developer.apple.com",
            )
            BLACKLIST = (
                "youtube.com",
                "reddit.com",
                "twitter.com",
                "stackoverflow.com/questions",
            )

            def _score(url: str) -> int:
                url = url.lower()
                if any(b in url for b in BLACKLIST):
                    return -1
                score = 0
                for p in PREFERRED:
                    if p in url:
                        score += 1
                return score

            seen_domains: set[str] = set()
            selected: list[str] = []
            for hit in sorted(
                hits, key=lambda h: _score(h.get("href", "")), reverse=True
            ):
                url = hit.get("url", "")
                domain = urlparse(url).netloc
                if not url or _score(url) < 0:
                    continue
                if domain not in seen_domains:
                    seen_domains.add(domain)
                    selected.append(url)
                if len(selected) >= max_urls:
                    break

            if not selected:
                return "No suitable documentation URLs found."

            # ── 3. Kick off indexing job ──────────────────────────────────────
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            job_id = str(uuid.uuid4())[:8]

            urls_file = os.path.join(base_dir, f"doc_{job_id}_urls.json")
            with open(urls_file, "w") as fh:
                json.dump(selected, fh)

            with _JOBS_LOCK:
                _JOBS[job_id] = {
                    "status": "running",
                    "project_id": f"doc:{topic}",
                    "project_path": f"docs://{topic}",
                    "file_count": len(selected),
                    "struct_rc": 0,
                    "sem_rc": None,
                    "logs": [],
                    "started_at": time.time(),
                    "finished_at": None,
                }

            doc_cmd = [
                sys.executable,
                os.path.join(base_dir, "doc_indexer.py"),
                "--urls-file",
                urls_file,
                "--topic",
                topic,
            ]
            proc = subprocess.Popen(
                doc_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            threading.Thread(
                target=_drain_proc_output,
                args=(proc, job_id, "[doc]", "sem_rc"),
                daemon=True,
            ).start()
            threading.Thread(
                target=_finalize_job, args=(job_id, urls_file), daemon=True
            ).start()

            url_list = "\n".join(f"  • {u}" for u in selected)
            return (
                f"Research + indexing started for '{topic}' — '{query}'\n\n"
                f"URLs selected ({len(selected)}):\n{url_list}\n\n"
                f"  job_id: {job_id}\n"
                f"  topic:  {topic}\n\n"
                f"Use get_index_status('{job_id}') to monitor.\n"
                f"Use search_documentation(query, topic='{topic}') once done."
            )
        except Exception as e:
            return f"Error in research_and_index: {e}"

"""tools/documentation.py — documentation research, crawling, and search tools."""
import os
import sys
import json
import threading
import subprocess
from mcp.server.fastmcp import FastMCP
from _jobs import _JOBS, _JOBS_LOCK, _drain_proc_output, _finalize_job


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
            from duckduckgo_search import DDGS
            import httpx
            from urllib.parse import urlparse

            search_query = f"{topic} {query} documentation site:docs OR site:github OR site:readthedocs"
            with DDGS() as ddgs:
                hits = list(ddgs.text(search_query, max_results=10))

            if not hits:
                return f"No results found for: {topic} — {query}"

            lines = [f"Documentation search: '{topic}' — '{query}'", ""]
            seen_domains = set()

            for hit in hits:
                url   = hit.get("href", "")
                title = hit.get("title", "")
                body  = hit.get("body", "")[:120].replace("\n", " ")
                lines += [f"• {title}", f"  {url}", f"  {body}…", ""]

                domain = urlparse(url).netloc
                if domain not in seen_domains:
                    seen_domains.add(domain)
                    llms_url = f"{urlparse(url).scheme}://{domain}/llms.txt"
                    try:
                        async with httpx.AsyncClient(timeout=5) as client:
                            r = await client.get(llms_url)
                            if r.status_code == 200:
                                lines += [f"  ✅ llms.txt found at {llms_url} (structured page list available)", ""]
                    except Exception:
                        pass

            lines += ["---", f"Found {len(hits)} results. Call download_documentation(urls=[...], topic='{topic}')"]
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
            job_id   = str(uuid.uuid4())[:8]

            urls_file = os.path.join(base_dir, f"doc_{job_id}_urls.json")
            with open(urls_file, "w") as fh:
                json.dump(list(urls), fh)

            with _JOBS_LOCK:
                _JOBS[job_id] = {
                    "status":       "running",
                    "project_id":   f"doc:{topic}",
                    "project_path": f"docs://{topic}",
                    "file_count":   len(urls),
                    "struct_rc":    0,
                    "sem_rc":       None,
                    "logs":         [],
                    "started_at":   time.time(),
                    "finished_at":  None,
                }

            doc_cmd = [
                sys.executable,
                os.path.join(base_dir, "doc_indexer.py"),
                "--urls-file", urls_file,
                "--topic",     topic,
            ]
            proc = subprocess.Popen(doc_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            threading.Thread(target=_drain_proc_output, args=(proc, job_id, "[doc]", "sem_rc"), daemon=True).start()
            threading.Thread(target=_finalize_job, args=(job_id, urls_file), daemon=True).start()

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

            vec_str   = "[" + ",".join(str(v) for v in query_vec) + "]"
            fetch     = min(k * 5, 100)
            topic_sql = "AND source = %(topic)s" if topic else ""

            if query.strip():
                sql = f"""
                    WITH semantic AS (
                        SELECT id, url, metadata->>'title' AS title, chunk_index, content,
                               metadata->'context_path' AS context_path,
                               ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rank
                        FROM doc_embeddings WHERE TRUE {topic_sql} LIMIT %(fetch)s
                    ),
                    keyword AS (
                        SELECT id, ROW_NUMBER() OVER (
                                   ORDER BY ts_rank(search_vec, plainto_tsquery('english', %(qt)s)) DESC
                               ) AS rank
                        FROM doc_embeddings
                        WHERE TRUE {topic_sql} AND search_vec @@ plainto_tsquery('english', %(qt)s)
                        LIMIT %(fetch)s
                    ),
                    fused AS (
                        SELECT s.url, s.title, s.chunk_index, s.content, s.context_path,
                               (1.0/(60+s.rank) + COALESCE(1.0/(60+k.rank), 0.0)) AS rrf_score
                        FROM semantic s LEFT JOIN keyword k ON s.id = k.id
                    )
                    SELECT url, title, chunk_index, content, rrf_score, context_path
                    FROM fused ORDER BY rrf_score DESC LIMIT %(k)s
                """
                params = {"vec": vec_str, "qt": query, "fetch": fetch, "k": k}
            else:
                sql = f"""
                    SELECT url, metadata->>'title' AS title, chunk_index, content,
                           (1.0/(60 + ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector))) AS rrf_score,
                           metadata->'context_path' AS context_path
                    FROM doc_embeddings WHERE TRUE {topic_sql}
                    ORDER BY embedding <=> %(vec)s::vector LIMIT %(k)s
                """
                params = {"vec": vec_str, "k": k}

            if topic:
                params["topic"] = topic

            results = []
            async with ms._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, params)
                    async for row in cur:
                        results.append({
                            "source_url":   row[0], "title": row[1],
                            "chunk_index":  row[2], "content": row[3],
                            "rrf_score":    row[4], "context_path": row[5] or [],
                        })

            if not results:
                tip = f" (topic='{topic}')" if topic else ""
                return f"No documentation found for: '{query}'{tip}\nRun download_documentation() first."

            lines = [f"Documentation search: '{query}'" + (f"  [topic={topic}]" if topic else ""), ""]
            for i, r in enumerate(results, 1):
                lines.append(f"[{i}] {r['title'] or r['source_url']}")
                lines.append(f"    URL:   {r['source_url']}")
                ctx = r.get("context_path") or []
                if isinstance(ctx, str):
                    try: ctx = json.loads(ctx)
                    except Exception: ctx = []
                if ctx:
                    lines.append(f"    Path:  {' > '.join(ctx)}")
                lines.append(f"    Score: {r['rrf_score']:.4f}")
                lines.append("")
                lines.append(f"    {r['content'][:400].strip().replace(chr(10), ' ')}…")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            return f"Error searching documentation: {e}"

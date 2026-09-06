"""tools/docs/search.py — documentation search tool."""

import json
from urllib.parse import urlparse

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
            from memory.docs_retrieval import search_docs, doc_type_from_result

            await ms.open_pool()
            if not ms._pg_pool_available():
                return "Postgres pool not available — check LM_PROXY_PG_DSN in .env"

            svc = get_embedding_service()
            vecs = await svc.embed_batch_async([query])
            query_vec = vecs[0]
            if not query_vec:
                return "Could not generate query embedding."

            results, trace = await search_docs(query, query_vec, topic=topic, k=k)

            if not results:
                tip = f" (topic='{topic}')" if topic else ""
                return f"No documentation found for: '{query}'{tip}\nRun download_documentation() first."

            if isinstance(trace, dict):
                try:
                    from memory.retrieval_telemetry import append_duplicate_telemetry_event

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

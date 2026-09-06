"""tools/docs/admin.py — documentation cleanup and reporting tools."""

from mcp.server.fastmcp import FastMCP
from memory.docs_retrieval import topic_family_patterns


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def delete_documentation(
        topic: str,
        url_patterns: list | None = None,
        allow_all: bool = False,
        dry_run: bool = False,
        older_than_days: int | None = None,
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
            older_than_days: Only delete chunks not refreshed within this many days.
        """
        try:
            import memory.store as ms

            await ms.open_pool()
            if not ms._pg_pool_available():
                return "Postgres pool not available — check LM_PROXY_PG_DSN in .env"

            patterns = [
                p.strip()
                for p in (url_patterns or [])
                if isinstance(p, str) and p.strip()
            ]
            if older_than_days is not None:
                older_than_days = int(older_than_days)
                if older_than_days < 1:
                    return "Error: older_than_days must be at least 1."
            if not patterns and older_than_days is None and not allow_all:
                return (
                    "Refusing to delete without url_patterns or older_than_days. "
                    "Provide a filter or set allow_all=True."
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
            if older_than_days is not None:
                params["older_than_seconds"] = older_than_days * 86400
                clauses.append(
                    "created_at < EXTRACT(EPOCH FROM NOW()) - %(older_than_seconds)s"
                )

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
            import memory.store as ms

            await ms.open_pool()
            if not ms._pg_pool_available():
                return "Postgres pool not available — check LM_PROXY_PG_DSN in .env"

            limit = max(1, min(int(limit), 100))

            async with ms._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    if topic:
                        patterns = topic_family_patterns(topic)
                        params = {"limit": limit}
                        if len(patterns) == 1 and "%" not in patterns[0]:
                            where_sql = "source = %(topic)s"
                            params["topic"] = patterns[0]
                        else:
                            clauses = []
                            for idx, pattern in enumerate(patterns):
                                key = f"topic_{idx}"
                                params[key] = pattern
                                if "%" in pattern:
                                    clauses.append(f"source ILIKE %({key})s")
                                else:
                                    clauses.append(f"source = %({key})s")
                            where_sql = "(" + " OR ".join(clauses) + ")"

                        await cur.execute(
                            f"""
                            SELECT count(*) AS chunks, count(DISTINCT url) AS urls
                            FROM doc_embeddings WHERE {where_sql}
                            """,
                            params,
                        )
                        row = await cur.fetchone()
                        total_chunks = row[0] if row else 0
                        total_urls = row[1] if row else 0

                        await cur.execute(
                            f"""
                            SELECT split_part(url, '/', 3) AS domain, count(*) AS chunks
                            FROM doc_embeddings
                            WHERE {where_sql}
                            GROUP BY domain
                            ORDER BY chunks DESC
                            LIMIT %(limit)s
                            """,
                            params,
                        )
                        rows = []
                        async for r in cur:
                            rows.append((r[0], r[1]))

                        await cur.execute(
                            f"""
                            SELECT source, count(*) AS chunks, count(DISTINCT url) AS urls
                            FROM doc_embeddings
                            WHERE {where_sql}
                            GROUP BY source
                            ORDER BY chunks DESC
                            LIMIT %(limit)s
                            """,
                            params,
                        )
                        topic_rows = []
                        async for r in cur:
                            topic_rows.append((r[0], r[1], r[2]))

                        if not rows:
                            return f"No documentation found for topic='{topic}'."

                        lines = [
                            f"Documentation domains for topic='{topic}'",
                            f"  Chunks: {total_chunks}",
                            f"  URLs:   {total_urls}",
                            "",
                        ]
                        if len(topic_rows) > 1:
                            lines.append("Topic members:")
                            for source_name, chunks, urls in topic_rows:
                                lines.append(f"- {source_name}  ({chunks} chunks, {urls} urls)")
                            lines.append("")
                            lines.append("Domains:")
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

"""Query-building and row-loading helpers for duplication detection."""

from __future__ import annotations


def build_queries(include_filter_sql: str) -> dict[str, str]:
    return {
        "exact": f"""
            WITH base AS (
                SELECT id, file_path, chunk_index, content, metadata,
                       regexp_replace(content, '^// File: .*?\\n', '', 'n') AS body
                FROM codebase_embeddings
                WHERE project_id = %(pid)s
                  AND char_length(content) >= %(min_chars)s
                  {include_filter_sql}
            ), norm AS (
                SELECT id, file_path, chunk_index, content, metadata,
                       md5(regexp_replace(body, '\\s+', ' ', 'g')) AS h
                FROM base
            ), dups AS (
                SELECT h, count(*) AS n
                FROM norm
                GROUP BY h
                HAVING count(*) > 1
            )
            SELECT n.h, n.file_path, n.chunk_index, n.content, n.metadata, d.n
            FROM norm n
            JOIN dups d ON n.h = d.h
            ORDER BY d.n DESC
            LIMIT %(limit)s
        """,
        "normalized": f"""
            WITH base AS (
                SELECT id, file_path, chunk_index, content, metadata,
                       regexp_replace(content, '^// File: .*?\\n', '', 'n') AS body
                FROM codebase_embeddings
                WHERE project_id = %(pid)s
                  AND char_length(content) >= %(min_chars)s
                  {include_filter_sql}
            ), norm AS (
                SELECT id, file_path, chunk_index, content, metadata,
                       md5(
                           regexp_replace(
                               regexp_replace(body, '\\b[0-9]+\\b', '<num>', 'g'),
                               '\\b[A-Za-z_][A-Za-z0-9_]*\\b',
                               '<id>',
                               'g'
                           )
                       ) AS h
                FROM base
            ), dups AS (
                SELECT h, count(*) AS n
                FROM norm
                GROUP BY h
                HAVING count(*) > 1
            )
            SELECT n.h, n.file_path, n.chunk_index, n.content, n.metadata, d.n
            FROM norm n
            JOIN dups d ON n.h = d.h
            ORDER BY d.n DESC
            LIMIT %(limit)s
        """,
        "semantic": f"""
            WITH base AS (
                SELECT id, file_path, chunk_index, content, metadata, embedding
                FROM codebase_embeddings
                WHERE project_id = %(pid)s
                  AND char_length(content) >= %(min_chars)s
                  {include_filter_sql}
                LIMIT %(sample_size)s
            ), pairs AS (
                SELECT b.id AS id_a,
                       b.file_path AS file_a,
                       b.chunk_index AS idx_a,
                       b.content AS content_a,
                       b.metadata AS meta_a,
                       n.id AS id_b,
                       n.file_path AS file_b,
                       n.chunk_index AS idx_b,
                       n.content AS content_b,
                       n.metadata AS meta_b,
                       (1 - (b.embedding <=> n.embedding)) AS sim
                FROM base b
                JOIN LATERAL (
                    SELECT id, file_path, chunk_index, content, metadata, embedding
                    FROM codebase_embeddings
                    WHERE project_id = %(pid)s
                      AND id <> b.id
                      AND file_path <> b.file_path
                      AND char_length(content) >= %(min_chars)s
                    ORDER BY b.embedding <=> embedding
                    LIMIT %(per_chunk)s
                ) n ON true
            )
            SELECT * FROM pairs
            WHERE sim >= %(min_sim)s
            ORDER BY sim DESC
            LIMIT %(limit)s
        """,
        "winnow_base": f"""
            SELECT id, file_path, chunk_index, content, metadata
            FROM codebase_embeddings
            WHERE project_id = %(pid)s
              AND char_length(content) >= %(min_chars)s
              {include_filter_sql}
        """,
        "winnow_count": f"""
            SELECT count(*) AS n
            FROM codebase_embeddings
            WHERE project_id = %(pid)s
              AND char_length(content) >= %(min_chars)s
              {include_filter_sql}
        """,
        "winnow_small": f"""
            SELECT id, file_path, chunk_index, content, metadata
            FROM codebase_embeddings
            WHERE project_id = %(pid)s
              AND char_length(content) >= %(min_chars)s
              AND char_length(content) <= %(max_chars)s
              {include_filter_sql}
        """,
    }


async def fetch_dict_rows(cur, sql: str, params: dict) -> list[dict]:
    await cur.execute(sql, params)
    rows = await cur.fetchall()
    col_names = [desc[0] for desc in cur.description]
    return [dict(zip(col_names, row)) for row in rows]


async def load_duplication_rows(
    cur,
    *,
    queries: dict[str, str],
    project_id: str,
    min_chars: int,
    winnow_min_chars: int,
    max_pairs: int,
    sample_size: int,
    per_chunk: int,
    min_similarity: float,
    include_exact: bool,
    include_normalized: bool,
    include_semantic: bool,
    include_winnow: bool,
    include_like_patterns: list[str],
    winnow_sample_size: int,
    path_allowed,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]], list[dict], list[dict]]:
    exact_groups: dict[str, list[dict]] = {}
    normalized_groups: dict[str, list[dict]] = {}
    semantic_rows: list[dict] = []
    winnow_rows: list[dict] = []

    if include_exact:
        params = {"pid": project_id, "min_chars": min_chars, "limit": max_pairs * 5}
        if include_like_patterns:
            params["include_paths"] = include_like_patterns
        rows = await fetch_dict_rows(cur, queries["exact"], params)
        for row in rows:
            if path_allowed(row["file_path"]):
                exact_groups.setdefault(row["h"], []).append(row)

    if include_normalized:
        params = {"pid": project_id, "min_chars": min_chars, "limit": max_pairs * 5}
        if include_like_patterns:
            params["include_paths"] = include_like_patterns
        rows = await fetch_dict_rows(cur, queries["normalized"], params)
        for row in rows:
            if path_allowed(row["file_path"]):
                normalized_groups.setdefault(row["h"], []).append(row)

    if include_semantic:
        await cur.execute("SET LOCAL hnsw.ef_search = 100")
        await cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        params = {
            "pid": project_id,
            "min_chars": min_chars,
            "sample_size": sample_size,
            "per_chunk": per_chunk,
            "min_sim": min_similarity,
            "limit": max_pairs * 3,
        }
        if include_like_patterns:
            params["include_paths"] = include_like_patterns
        semantic_rows = await fetch_dict_rows(cur, queries["semantic"], params)

    if include_winnow:
        params = {"pid": project_id, "min_chars": winnow_min_chars}
        if include_like_patterns:
            params["include_paths"] = include_like_patterns

        total_chunks = None
        try:
            await cur.execute(queries["winnow_count"], params)
            row = await cur.fetchone()
            total_chunks = row[0] if row else None
        except Exception:
            total_chunks = None

        effective_winnow_sample = winnow_sample_size
        if total_chunks is not None and total_chunks <= 5000:
            effective_winnow_sample = 0

        winnow_sql = queries["winnow_base"]
        if effective_winnow_sample and effective_winnow_sample > 0:
            winnow_sql = f"{winnow_sql} LIMIT %(limit)s"
            params["limit"] = max(100, effective_winnow_sample)
        winnow_rows = await fetch_dict_rows(cur, winnow_sql, params)

        if effective_winnow_sample and effective_winnow_sample > 0:
            try:
                small_params = {
                    "pid": project_id,
                    "min_chars": winnow_min_chars,
                    "max_chars": max(winnow_min_chars, 600),
                }
                if include_like_patterns:
                    small_params["include_paths"] = include_like_patterns
                small_rows = await fetch_dict_rows(cur, queries["winnow_small"], small_params)
                if small_rows:
                    seen_ids = {r["id"] for r in winnow_rows}
                    for row in small_rows:
                        if row.get("id") not in seen_ids:
                            winnow_rows.append(row)
            except Exception:
                pass

    return exact_groups, normalized_groups, semantic_rows, winnow_rows

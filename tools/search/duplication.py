"""tools/search/duplication.py — code duplication detection."""

import hashlib
import os
from mcp.server.fastmcp import FastMCP

from _helpers import get_memory_modules
from tools.search import core as search_core


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def find_code_duplication(
        project_path: str,
        min_similarity: float = 0.92,
        max_pairs: int = 50,
        min_tokens: int = 80,
        same_file_min_tokens: int = 20,
        max_same_file_pairs_per_file: int = 8,
        per_chunk: int = 5,
        sample_size: int = 500,
        exclude_paths: list | None = None,
        include_paths: list | None = None,
        include_exact: bool = True,
        include_normalized: bool = True,
        include_winnow: bool = True,
        include_semantic: bool = True,
        include_symbol_dupes: bool = True,
        cross_file_only: bool = False,
        prefer_cross_file: bool = True,
        winnow_k: int = 3,
        winnow_window: int = 5,
        winnow_min_overlap: float = 0.6,
        winnow_bucket_limit: int = 40,
        winnow_sample_size: int = 2000,
        winnow_normalize_identifiers: bool = True,
        winnow_min_tokens: int = 20,
        winnow_code_only: bool = True,
        winnow_min_fingerprints: int = 12,
        winnow_fallback_hashes: int = 6,
        winnow_force_all_hashes_max_fps: int = 25,
        winnow_small_token_threshold: int = 50,
        winnow_medium_token_threshold: int = 150,
        winnow_small_k: int = 5,
        winnow_small_w: int = 3,
        winnow_medium_k: int = 9,
        winnow_medium_w: int = 5,
        winnow_large_k: int = 15,
        winnow_large_w: int = 7,
        winnow_token_sim_threshold: float = 0.65,
        winnow_kgram_sim_threshold: float = 0.7,
    ) -> str:
        """
        Find near-duplicate code chunks within a single project using semantic embeddings.

        Args:
            project_path: Absolute path to the project root.
            min_similarity: Cosine similarity threshold (default 0.92).
            max_pairs: Max duplicate pairs to return (default 50).
            min_tokens: Minimum token estimate per chunk (approx by chars/4).
            same_file_min_tokens: Minimum token estimate for same-file pairs (default 20).
            max_same_file_pairs_per_file: Max same-file pairs per file (default 8).
            per_chunk: Nearest neighbors per chunk (default 5).
            sample_size: Base chunk sample size (default 500).
            exclude_paths: Optional glob patterns to exclude by file_path.
            include_paths: Optional glob patterns to include by file_path.
            include_winnow: Include token-fingerprint winnowing matches.
            winnow_k: Token shingle length for winnowing (default 3).
            winnow_window: Hash window size for winnowing (default 5).
            winnow_min_overlap: Min overlap ratio for winnow pairs (default 0.6).
            winnow_bucket_limit: Max bucket size for pair expansion (default 40).
            winnow_sample_size: Max chunks to analyze for winnowing (default 2000, 0 = no limit).
            winnow_normalize_identifiers: Collapse identifiers/numbers in winnowing.
            winnow_min_tokens: Min tokens for winnowing chunks (default 20).
            winnow_code_only: Only include likely code files for winnowing.
            winnow_min_fingerprints: Min fingerprints per chunk (default 12).
            winnow_fallback_hashes: Fallback hashes per chunk when all are common.
            winnow_force_all_hashes_max_fps: Force all hashes for small chunks.
            winnow_small_token_threshold: Tokens below this use fallback matching.
            winnow_medium_token_threshold: Tokens below this use medium params.
            winnow_small_k: k-gram size for small blocks (default 5).
            winnow_small_w: window size for small blocks (default 3).
            winnow_medium_k: k-gram size for medium blocks (default 9).
            winnow_medium_w: window size for medium blocks (default 5).
            winnow_large_k: k-gram size for large blocks (default 15).
            winnow_large_w: window size for large blocks (default 7).
            winnow_token_sim_threshold: Token Jaccard threshold for candidates.
            winnow_kgram_sim_threshold: k-gram Jaccard threshold for small blocks.
            Note: Winnowing guarantees detection only for matches of length
            t = w + k - 1. Small chunks below t require the k-gram/token
            fallback to avoid misses.
        """
        try:
            import fnmatch
            import itertools
            import re

            memory_store, _, _, _, _ = get_memory_modules()
            if not project_path:
                return "Error: provide a project path."

            await memory_store.open_pool()
            if not memory_store._pg_pool_available():
                return "Error: Postgres pool not available for semantic search."

            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            min_chars = max(0, int(min_tokens) * 4)
            winnow_min_chars = max(0, int(winnow_min_tokens) * 4)
            per_chunk = max(1, int(per_chunk))
            max_pairs = max(1, int(max_pairs))
            same_file_min_tokens = int(
                os.getenv(
                    "LM_PROXY_DUPLICATION_SAME_FILE_MIN_TOKENS",
                    same_file_min_tokens,
                )
            )
            same_file_min_tokens = max(1, same_file_min_tokens)
            max_same_file_pairs_per_file = int(
                os.getenv(
                    "LM_PROXY_DUPLICATION_MAX_SAME_FILE_PAIRS_PER_FILE",
                    max_same_file_pairs_per_file,
                )
            )
            max_same_file_pairs_per_file = max(1, max_same_file_pairs_per_file)
            sample_size = max(50, int(sample_size))
            winnow_k = max(2, int(winnow_k))
            winnow_window = max(1, int(winnow_window))
            winnow_bucket_limit = max(5, int(winnow_bucket_limit))
            winnow_min_fingerprints = max(1, int(winnow_min_fingerprints))
            winnow_fallback_hashes = max(0, int(winnow_fallback_hashes))
            winnow_force_all_hashes_max_fps = max(
                0, int(winnow_force_all_hashes_max_fps)
            )
            winnow_small_token_threshold = max(5, int(winnow_small_token_threshold))
            winnow_medium_token_threshold = max(
                winnow_small_token_threshold + 1,
                int(winnow_medium_token_threshold),
            )
            winnow_small_k = max(3, int(winnow_small_k))
            winnow_small_w = max(2, int(winnow_small_w))
            winnow_medium_k = max(winnow_small_k, int(winnow_medium_k))
            winnow_medium_w = max(winnow_small_w, int(winnow_medium_w))
            winnow_large_k = max(winnow_medium_k, int(winnow_large_k))
            winnow_large_w = max(winnow_medium_w, int(winnow_large_w))
            winnow_token_sim_threshold = max(0.0, float(winnow_token_sim_threshold))
            winnow_kgram_sim_threshold = max(0.0, float(winnow_kgram_sim_threshold))
            winnow_sample_size = int(winnow_sample_size)

            exclude_patterns = exclude_paths or []
            include_patterns = include_paths or []

            def _glob_to_like(pattern: str) -> str:
                pattern = pattern.replace("%", "\%")
                pattern = pattern.replace("_", "\_")
                pattern = pattern.replace("*", "%")
                pattern = pattern.replace("?", "_")
                return pattern

            include_like_patterns = [
                _glob_to_like(p) for p in include_patterns if isinstance(p, str)
            ]
            include_filter_sql = (
                " AND file_path LIKE ANY(%(include_paths)s)"
                if include_like_patterns
                else ""
            )

            def _path_allowed(file_path: str) -> bool:
                if not file_path:
                    return False
                if include_patterns:
                    if not any(fnmatch.fnmatch(file_path, p) for p in include_patterns):
                        return False
                if exclude_patterns:
                    if any(fnmatch.fnmatch(file_path, p) for p in exclude_patterns):
                        return False
                return True

            exact_sql = f"""
                WITH base AS (
                    SELECT id, file_path, chunk_index, content, metadata,
                           regexp_replace(content, '^// File: .*?\n', '', 'n') AS body
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
            """

            normalized_sql = f"""
                WITH base AS (
                    SELECT id, file_path, chunk_index, content, metadata,
                           regexp_replace(content, '^// File: .*?\n', '', 'n') AS body
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
            """

            semantic_sql = f"""
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
            """

            winnow_sql_base = f"""
                SELECT id, file_path, chunk_index, content, metadata
                FROM codebase_embeddings
                WHERE project_id = %(pid)s
                  AND char_length(content) >= %(min_chars)s
                  {include_filter_sql}
            """
            winnow_count_sql = f"""
                SELECT count(*) AS n
                FROM codebase_embeddings
                WHERE project_id = %(pid)s
                  AND char_length(content) >= %(min_chars)s
                  {include_filter_sql}
            """

            exact_groups: dict[str, list[dict]] = {}
            normalized_groups: dict[str, list[dict]] = {}
            semantic_rows: list[dict] = []
            winnow_rows: list[dict] = []

            async with memory_store._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    if include_exact:
                        exact_params = {
                            "pid": project_id,
                            "min_chars": min_chars,
                            "limit": max_pairs * 5,
                        }
                        if include_like_patterns:
                            exact_params["include_paths"] = include_like_patterns
                        await cur.execute(exact_sql, exact_params)
                        rows = await cur.fetchall()
                        col_names = [desc[0] for desc in cur.description]
                        rows = [dict(zip(col_names, row)) for row in rows]
                        for row in rows:
                            fp = row["file_path"]
                            if not _path_allowed(fp):
                                continue
                            exact_groups.setdefault(row["h"], []).append(row)

                    if include_normalized:
                        normalized_params = {
                            "pid": project_id,
                            "min_chars": min_chars,
                            "limit": max_pairs * 5,
                        }
                        if include_like_patterns:
                            normalized_params["include_paths"] = include_like_patterns
                        await cur.execute(normalized_sql, normalized_params)
                        rows = await cur.fetchall()
                        col_names = [desc[0] for desc in cur.description]
                        rows = [dict(zip(col_names, row)) for row in rows]
                        for row in rows:
                            fp = row["file_path"]
                            if not _path_allowed(fp):
                                continue
                            normalized_groups.setdefault(row["h"], []).append(row)

                    if include_semantic:
                        await cur.execute("SET LOCAL hnsw.ef_search = 100")
                        await cur.execute(
                            "SET LOCAL hnsw.iterative_scan = relaxed_order"
                        )
                        semantic_params = {
                            "pid": project_id,
                            "min_chars": min_chars,
                            "sample_size": sample_size,
                            "per_chunk": per_chunk,
                            "min_sim": min_similarity,
                            "limit": max_pairs * 3,
                        }
                        if include_like_patterns:
                            semantic_params["include_paths"] = include_like_patterns
                        await cur.execute(semantic_sql, semantic_params)
                        rows = await cur.fetchall()
                        col_names = [desc[0] for desc in cur.description]
                        semantic_rows = [dict(zip(col_names, row)) for row in rows]

                    if include_winnow:
                        winnow_sql = winnow_sql_base
                        params = {
                            "pid": project_id,
                            "min_chars": winnow_min_chars,
                        }
                        if include_like_patterns:
                            params["include_paths"] = include_like_patterns
                        total_chunks = None
                        try:
                            await cur.execute(winnow_count_sql, params)
                            row = await cur.fetchone()
                            total_chunks = row[0] if row else None
                        except Exception:
                            total_chunks = None

                        effective_winnow_sample = winnow_sample_size
                        if total_chunks is not None and total_chunks <= 5000:
                            effective_winnow_sample = 0

                        if effective_winnow_sample and effective_winnow_sample > 0:
                            winnow_sql = f"{winnow_sql} LIMIT %(limit)s"
                            params["limit"] = max(100, effective_winnow_sample)
                        await cur.execute(winnow_sql, params)
                        rows = await cur.fetchall()
                        col_names = [desc[0] for desc in cur.description]
                        winnow_rows = [dict(zip(col_names, row)) for row in rows]

                        if effective_winnow_sample and effective_winnow_sample > 0:
                            try:
                                await cur.execute(
                                    f"""
                                    SELECT id, file_path, chunk_index, content, metadata
                                    FROM codebase_embeddings
                                    WHERE project_id = %(pid)s
                                      AND char_length(content) >= %(min_chars)s
                                      AND char_length(content) <= %(max_chars)s
                                      {include_filter_sql}
                                    """,
                                    {
                                        "pid": project_id,
                                        "min_chars": winnow_min_chars,
                                        "max_chars": max(winnow_min_chars, 600),
                                        **(
                                            {"include_paths": include_like_patterns}
                                            if include_like_patterns
                                            else {}
                                        ),
                                    },
                                )
                                small_rows = await cur.fetchall()
                                col_names = [desc[0] for desc in cur.description]
                                small_rows = [
                                    dict(zip(col_names, row)) for row in small_rows
                                ]
                                if small_rows:
                                    seen_ids = {r["id"] for r in winnow_rows}
                                    for row in small_rows:
                                        if row.get("id") not in seen_ids:
                                            winnow_rows.append(row)
                            except Exception:
                                pass

            lines: list[str] = []

            def _pick_pair(
                group: list[dict], cross_file: bool
            ) -> tuple[dict, dict] | None:
                if len(group) < 2:
                    return None
                if cross_file:
                    for i in range(len(group)):
                        for j in range(i + 1, len(group)):
                            if group[i]["file_path"] != group[j]["file_path"]:
                                return group[i], group[j]
                return group[0], group[1]

            def _tokenize(text: str) -> list[str]:
                if not text:
                    return []
                return re.findall(
                    r"[A-Za-z_][A-Za-z0-9_]*|\d+|==|!=|<=|>=|->|[{}()\[\];,.:+\-*/%<>=]",
                    text,
                )

            def _same_file_allowed(
                file_path: str, content: str, counts: dict[str, int]
            ) -> bool:
                if counts.get(file_path, 0) >= max_same_file_pairs_per_file:
                    return False
                if same_file_min_tokens > 0:
                    if len(_tokenize(content)) < same_file_min_tokens:
                        return False
                return True

            def _emit_pairs(
                title: str,
                groups: list[list[dict]],
                cross_file: bool,
                same_file_counts: dict[str, int] | None = None,
            ) -> None:
                if not groups:
                    lines.append(f"{title}: none")
                    return
                lines.append(f"{title} ({len(groups)})")
                count = 0
                for group in groups:
                    pair = _pick_pair(group, cross_file=cross_file)
                    if not pair:
                        continue
                    a, b = pair
                    if not cross_file and same_file_counts is not None:
                        file_key = a["file_path"]
                        if not _same_file_allowed(
                            file_key, a.get("content") or "", same_file_counts
                        ):
                            continue
                    meta_a = a.get("metadata") or {}
                    meta_b = b.get("metadata") or {}
                    a_start = meta_a.get("start_line")
                    b_start = meta_b.get("start_line")
                    a_line = f":{a_start}" if isinstance(a_start, int) else ""
                    b_line = f":{b_start}" if isinstance(b_start, int) else ""
                    preview_a = (a["content"] or "").strip().splitlines()[0][:200]
                    preview_b = (b["content"] or "").strip().splitlines()[0][:200]
                    lines.append(
                        f"- {a['file_path']}{a_line} ↔ {b['file_path']}{b_line}"
                    )
                    lines.append(f"  A: {preview_a}")
                    lines.append(f"  B: {preview_b}")
                    if not cross_file and same_file_counts is not None:
                        same_file_counts[file_key] = (
                            same_file_counts.get(file_key, 0) + 1
                        )
                    count += 1
                    if count >= max_pairs:
                        break

            def _node_type_jaccard(meta_a: dict, meta_b: dict) -> float:
                types_a = meta_a.get("node_types") or []
                types_b = meta_b.get("node_types") or []
                if not types_a or not types_b:
                    return 0.0
                set_a = set(types_a)
                set_b = set(types_b)
                denom = len(set_a | set_b)
                if denom == 0:
                    return 0.0
                return len(set_a & set_b) / denom

            def _winnow_fingerprints(
                tokens: list[str], k: int, window: int
            ) -> set[int]:
                if len(tokens) < k:
                    return set()
                hashes: list[int] = []
                for i in range(len(tokens) - k + 1):
                    gram = " ".join(tokens[i : i + k])
                    h = int(hashlib.md5(gram.encode()).hexdigest()[:16], 16)
                    hashes.append(h)
                if not hashes:
                    return set()
                if len(hashes) <= window:
                    return {min(hashes)}
                fingerprints: set[int] = set()
                min_hash = None
                min_pos = -1
                for i in range(len(hashes) - window + 1):
                    window_hashes = hashes[i : i + window]
                    current_min = min(window_hashes)
                    if min_hash != current_min or min_pos < i:
                        min_hash = current_min
                        min_pos = i + window_hashes.index(current_min)
                        fingerprints.add(min_hash)
                return fingerprints

            def _kgrams(tokens: list[str], k: int) -> set[tuple[str, ...]]:
                if len(tokens) < k:
                    return set()
                return {tuple(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}

            def _normalize_tokens(tokens: list[str]) -> list[str]:
                if not tokens:
                    return []
                normalized: list[str] = []
                for tok in tokens:
                    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tok):
                        normalized.append("<id>")
                    elif re.match(r"^\d+$", tok):
                        normalized.append("<num>")
                    else:
                        normalized.append(tok)
                return normalized

            def _is_code_file(file_path: str, metadata: dict | None) -> bool:
                if not file_path:
                    return False
                if metadata and isinstance(metadata, dict):
                    lang = metadata.get("language")
                    if isinstance(lang, str) and lang:
                        if lang.lower() in {
                            "json",
                            "markdown",
                            "md",
                            "yaml",
                            "toml",
                            "ini",
                            "text",
                        }:
                            return False
                _, ext = os.path.splitext(file_path.lower())
                if ext in {
                    ".md",
                    ".json",
                    ".yaml",
                    ".yml",
                    ".toml",
                    ".ini",
                    ".txt",
                    ".csv",
                    ".lock",
                    ".env",
                }:
                    return False
                return True

            if include_exact:
                exact_items = [g for g in exact_groups.values() if len(g) > 1]
                cross_items = [
                    g for g in exact_items if len({r["file_path"] for r in g}) > 1
                ]
                same_items = [
                    g for g in exact_items if len({r["file_path"] for r in g}) == 1
                ]
                same_file_counts: dict[str, int] = {}
                lines.append("Exact duplicate chunks")
                if cross_file_only:
                    _emit_pairs("Cross-file", cross_items, cross_file=True)
                else:
                    if prefer_cross_file:
                        _emit_pairs("Cross-file", cross_items, cross_file=True)
                        _emit_pairs(
                            "Same-file",
                            same_items,
                            cross_file=False,
                            same_file_counts=same_file_counts,
                        )
                    else:
                        _emit_pairs(
                            "Same-file",
                            same_items,
                            cross_file=False,
                            same_file_counts=same_file_counts,
                        )
                        _emit_pairs("Cross-file", cross_items, cross_file=True)

            if include_normalized:
                norm_items = [g for g in normalized_groups.values() if len(g) > 1]
                cross_items = [
                    g for g in norm_items if len({r["file_path"] for r in g}) > 1
                ]
                same_items = [
                    g for g in norm_items if len({r["file_path"] for r in g}) == 1
                ]
                same_file_counts: dict[str, int] = {}
                lines.append("\nNormalized duplicates (identifiers/numbers collapsed)")
                if cross_file_only:
                    _emit_pairs("Cross-file", cross_items, cross_file=True)
                else:
                    if prefer_cross_file:
                        _emit_pairs("Cross-file", cross_items, cross_file=True)
                        _emit_pairs(
                            "Same-file",
                            same_items,
                            cross_file=False,
                            same_file_counts=same_file_counts,
                        )
                    else:
                        _emit_pairs(
                            "Same-file",
                            same_items,
                            cross_file=False,
                            same_file_counts=same_file_counts,
                        )
                        _emit_pairs("Cross-file", cross_items, cross_file=True)

            if include_semantic:
                seen = set()
                results = []
                for row in semantic_rows:
                    file_a = row["file_a"]
                    file_b = row["file_b"]
                    if not _path_allowed(file_a) or not _path_allowed(file_b):
                        continue
                    pair_key = tuple(sorted([row["id_a"], row["id_b"]]))
                    if pair_key in seen:
                        continue
                    seen.add(pair_key)
                    results.append(row)
                    if len(results) >= max_pairs:
                        break

                if results:
                    lines.append(
                        "\nNear-duplicate chunks (cross-file only, "
                        f"min_similarity={min_similarity}, min_tokens≈{min_tokens})"
                    )
                    for row in results:
                        sim = row["sim"]
                        meta_a = row.get("meta_a") or {}
                        meta_b = row.get("meta_b") or {}
                        a_start = meta_a.get("start_line")
                        b_start = meta_b.get("start_line")
                        a_line = f":{a_start}" if isinstance(a_start, int) else ""
                        b_line = f":{b_start}" if isinstance(b_start, int) else ""
                        preview_a = (
                            (row["content_a"] or "").strip().splitlines()[0][:200]
                        )
                        preview_b = (
                            (row["content_b"] or "").strip().splitlines()[0][:200]
                        )
                        lines.append(
                            f"- {row['file_a']}{a_line} ↔ {row['file_b']}{b_line}  (sim={sim:.3f})"
                        )
                        lines.append(f"  A: {preview_a}")
                        lines.append(f"  B: {preview_b}")
                else:
                    lines.append("\nNo near-duplicate chunks found.")

            if include_winnow:
                winnow_pairs: list[tuple[dict, dict, float, float]] = []
                chunk_meta: dict[int, dict] = {}
                chunk_tokens: dict[int, list[str]] = {}
                chunk_token_set: dict[int, set[str]] = {}
                chunk_kgrams: dict[int, set[tuple[str, ...]]] = {}
                chunk_fps_by_scale: dict[int, dict[str, set[int]]] = {}
                fp_index_selected: dict[str, dict[int, set[int]]] = {}
                fp_counts: dict[str, dict[int, int]] = {}

                scales: list[tuple[str, int, int]] = [
                    ("small", winnow_small_k, winnow_small_w),
                    ("medium", winnow_medium_k, winnow_medium_w),
                    ("large", winnow_large_k, winnow_large_w),
                ]

                for row in winnow_rows:
                    fp = row["file_path"]
                    if not _path_allowed(fp):
                        continue
                    if winnow_code_only and not _is_code_file(fp, row.get("metadata")):
                        continue
                    content = row.get("content") or ""
                    content = re.sub(r"^// File: .*?\n", "", content)
                    tokens = _tokenize(content)
                    if winnow_normalize_identifiers:
                        tokens = _normalize_tokens(tokens)
                    if not tokens:
                        continue

                    cid = row["id"]
                    chunk_meta[cid] = row
                    chunk_tokens[cid] = tokens
                    chunk_token_set[cid] = set(tokens)

                    if len(tokens) < winnow_small_token_threshold:
                        kgrams = _kgrams(tokens, min(winnow_small_k, len(tokens)))
                        if kgrams:
                            chunk_kgrams[cid] = kgrams
                        continue

                    chunk_fps_by_scale[cid] = {}
                    for label, k, window in scales:
                        if len(tokens) < k:
                            continue
                        fps = _winnow_fingerprints(tokens, k, window)
                        if not fps or len(fps) < winnow_min_fingerprints:
                            continue
                        chunk_fps_by_scale[cid][label] = fps
                        fp_counts.setdefault(label, {})
                        for h in fps:
                            fp_counts[label][h] = fp_counts[label].get(h, 0) + 1

                    if not chunk_fps_by_scale[cid]:
                        kgrams = _kgrams(tokens, min(winnow_small_k, len(tokens)))
                        if kgrams:
                            chunk_kgrams[cid] = kgrams

                kgram_index: dict[tuple[str, ...], set[int]] = {}
                for cid, grams in chunk_kgrams.items():
                    for gram in grams:
                        kgram_index.setdefault(gram, set()).add(cid)

                candidate_pairs: dict[tuple[int, int], dict] = {}
                for gram, ids in kgram_index.items():
                    if len(ids) < 2:
                        continue
                    for a, b in itertools.combinations(sorted(ids), 2):
                        candidate_pairs.setdefault((a, b), {"kgram": True})

                for label, _k, _w in scales:
                    for cid, fps in chunk_fps_by_scale.items():
                        fps_set = fps.get(label)
                        if not fps_set:
                            continue
                        if (
                            winnow_force_all_hashes_max_fps > 0
                            and len(fps_set) <= winnow_force_all_hashes_max_fps
                        ):
                            filtered = set(fps_set)
                        else:
                            filtered = {
                                h
                                for h in fps_set
                                if fp_counts.get(label, {}).get(h, 0)
                                <= winnow_bucket_limit
                            }
                            if not filtered and winnow_fallback_hashes > 0:
                                filtered = set(sorted(fps_set)[:winnow_fallback_hashes])
                        for h in filtered:
                            fp_index_selected.setdefault(label, {}).setdefault(
                                h, set()
                            ).add(cid)

                    pair_counts: dict[tuple[int, int], int] = {}
                    for h, ids in fp_index_selected.get(label, {}).items():
                        ids = sorted(ids)
                        if len(ids) < 2:
                            continue
                        for a, b in itertools.combinations(ids, 2):
                            pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1

                    for (a, b), shared in pair_counts.items():
                        entry = candidate_pairs.setdefault((a, b), {"winnow": {}})
                        entry.setdefault("winnow", {})[label] = shared

                best_pairs: dict[tuple, tuple[dict, dict, float]] = {}
                for (a, b), info in candidate_pairs.items():
                    row_a = chunk_meta.get(a)
                    row_b = chunk_meta.get(b)
                    if not row_a or not row_b:
                        continue
                    key_a = (row_a.get("file_path"), row_a.get("chunk_index"))
                    key_b = (row_b.get("file_path"), row_b.get("chunk_index"))
                    if key_a == key_b:
                        continue
                    if key_a[0] == key_b[0] and cross_file_only:
                        continue

                    token_jaccard = 0.0
                    if winnow_token_sim_threshold > 0:
                        ta = chunk_token_set.get(a, set())
                        tb = chunk_token_set.get(b, set())
                        if ta and tb:
                            token_jaccard = len(ta & tb) / max(1, len(ta | tb))

                    kgram_jaccard = 0.0
                    if info.get("kgram"):
                        ga = chunk_kgrams.get(a, set())
                        gb = chunk_kgrams.get(b, set())
                        if ga and gb:
                            kgram_jaccard = len(ga & gb) / max(1, len(ga | gb))

                    max_overlap = 0.0
                    for label, shared in info.get("winnow", {}).items():
                        fps_a = chunk_fps_by_scale.get(a, {}).get(label)
                        fps_b = chunk_fps_by_scale.get(b, {}).get(label)
                        if not fps_a or not fps_b:
                            continue
                        denom = min(len(fps_a), len(fps_b))
                        if denom == 0:
                            continue
                        overlap = shared / denom
                        if overlap > max_overlap:
                            max_overlap = overlap

                    if (
                        max_overlap < winnow_min_overlap
                        and token_jaccard < winnow_token_sim_threshold
                        and kgram_jaccard < winnow_kgram_sim_threshold
                    ):
                        continue

                    base_score = max(max_overlap, token_jaccard, kgram_jaccard)
                    struct_score = _node_type_jaccard(
                        row_a.get("metadata") or {}, row_b.get("metadata") or {}
                    )
                    score = base_score * (0.5 + 0.5 * struct_score)
                    pair_key = tuple(sorted([key_a, key_b]))
                    existing = best_pairs.get(pair_key)
                    if not existing or score > existing[2]:
                        best_pairs[pair_key] = (row_a, row_b, score, struct_score)

                winnow_pairs = list(best_pairs.values())
                winnow_pairs.sort(
                    key=lambda x: (
                        x[2],
                        min(
                            len(chunk_tokens.get(x[0]["id"], [])),
                            len(chunk_tokens.get(x[1]["id"], [])),
                        ),
                    ),
                    reverse=True,
                )
                if winnow_pairs:
                    lines.append(
                        "\nWinnowed duplicates (multi-scale fingerprints + token fallback)"
                    )
                    cross_pairs = [
                        p
                        for p in winnow_pairs
                        if p[0]["file_path"] != p[1]["file_path"]
                    ]
                    same_pairs = [
                        p
                        for p in winnow_pairs
                        if p[0]["file_path"] == p[1]["file_path"]
                    ]

                    def _emit_winnow(
                        title: str,
                        pairs: list[tuple[dict, dict, float, float]],
                        same_file_counts: dict[str, int] | None = None,
                    ) -> None:
                        if not pairs:
                            lines.append(f"{title}: none")
                            return
                        lines.append(f"{title} ({len(pairs)})")
                        count = 0
                        for row_a, row_b, overlap, struct_score in pairs:
                            if count >= max_pairs:
                                break
                            if same_file_counts is not None and row_a.get(
                                "file_path"
                            ) == row_b.get("file_path"):
                                file_key = row_a.get("file_path") or ""
                                if not _same_file_allowed(
                                    file_key,
                                    row_a.get("content") or "",
                                    same_file_counts,
                                ):
                                    continue
                            meta_a = row_a.get("metadata") or {}
                            meta_b = row_b.get("metadata") or {}
                            a_start = meta_a.get("start_line")
                            b_start = meta_b.get("start_line")
                            a_line = f":{a_start}" if isinstance(a_start, int) else ""
                            b_line = f":{b_start}" if isinstance(b_start, int) else ""
                            preview_a = (
                                (row_a.get("content") or "")
                                .strip()
                                .splitlines()[0][:200]
                            )
                            preview_b = (
                                (row_b.get("content") or "")
                                .strip()
                                .splitlines()[0][:200]
                            )
                            lines.append(
                                f"- {row_a['file_path']}{a_line} ↔ {row_b['file_path']}{b_line}  "
                                f"(score={overlap:.2f}, struct={struct_score:.2f})"
                            )
                            lines.append(f"  A: {preview_a}")
                            lines.append(f"  B: {preview_b}")
                            if same_file_counts is not None and row_a.get(
                                "file_path"
                            ) == row_b.get("file_path"):
                                same_file_counts[file_key] = (
                                    same_file_counts.get(file_key, 0) + 1
                                )
                            count += 1

                    if cross_file_only:
                        _emit_winnow("Cross-file", cross_pairs)
                    else:
                        if prefer_cross_file:
                            _emit_winnow("Cross-file", cross_pairs)
                            _emit_winnow(
                                "Same-file",
                                same_pairs,
                                same_file_counts={},
                            )
                        else:
                            _emit_winnow(
                                "Same-file",
                                same_pairs,
                                same_file_counts={},
                            )
                            _emit_winnow("Cross-file", cross_pairs)
                else:
                    lines.append("\nNo winnowed duplicate chunks found.")

            if include_symbol_dupes and not include_patterns:
                try:
                    import graph_bootstrap

                    driver = await graph_bootstrap.require_driver()
                    if driver:
                        async with driver.session(
                            database=graph_bootstrap._NEO4J_DB
                        ) as session:
                            records = await search_core._execute_read(
                                session,
                                """
                                MATCH (s {project_id: $pid})
                                WHERE s:Function OR s:Method
                                WITH s.name AS name, collect(DISTINCT s.filepath) AS files
                                WHERE name IS NOT NULL AND size(files) > 1
                                RETURN name, size(files) AS count, files[0..6] AS files
                                ORDER BY count DESC
                                LIMIT 10
                                """,
                                op="dup_symbol_names",
                                pid=project_id,
                            )
                        if records:
                            lines.append("\nPotential duplicate symbol names:")
                            for rec in records:
                                lines.append(f"- {rec['name']}  (files={rec['count']})")
                                for fp in rec["files"]:
                                    lines.append(f"  {fp}")
                except Exception:
                    pass

            return "\n".join(lines)

        except Exception as exc:
            return f"Error: {exc}"

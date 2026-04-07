"""tools/search/duplication.py — code duplication detection."""

import os
from mcp.server.fastmcp import FastMCP

from _helpers import get_memory_modules, get_project_id
from tools.brain.search import core as search_core
from tools.brain.search import duplication_helpers as dup_helpers
from tools.brain.search import duplication_queries as dup_queries
from tools.brain.search import duplication_report as dup_report


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def find_code_duplication(
        workspace_id: str,
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
            workspace_id: Logical workspace ID or absolute path to the project root.
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
            import itertools
            import re
            from _helpers import WorkspaceRegistry

            memory_store, _, _, _, _ = get_memory_modules()
            if not workspace_id:
                return "Error: provide a workspace ID or path."

            await memory_store.open_pool()
            if not memory_store._pg_pool_available():
                return "Error: Postgres pool not available for semantic search."

            project_id = WorkspaceRegistry.resolve_id(workspace_id) or get_project_id(workspace_id)
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

            include_like_patterns = [
                dup_helpers.glob_to_like(p) for p in include_patterns if isinstance(p, str)
            ]
            include_filter_sql = (
                " AND file_path LIKE ANY(%(include_paths)s)"
                if include_like_patterns
                else ""
            )

            def _path_allowed(file_path: str) -> bool:
                return dup_helpers.path_allowed(
                    file_path,
                    include_patterns=include_patterns,
                    exclude_patterns=exclude_patterns,
                )

            queries = dup_queries.build_queries(include_filter_sql)

            async with memory_store._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    (
                        exact_groups,
                        normalized_groups,
                        semantic_rows,
                        winnow_rows,
                    ) = await dup_queries.load_duplication_rows(
                        cur,
                        queries=queries,
                        project_id=project_id,
                        min_chars=min_chars,
                        winnow_min_chars=winnow_min_chars,
                        max_pairs=max_pairs,
                        sample_size=sample_size,
                        per_chunk=per_chunk,
                        min_similarity=min_similarity,
                        include_exact=include_exact,
                        include_normalized=include_normalized,
                        include_semantic=include_semantic,
                        include_winnow=include_winnow,
                        include_like_patterns=include_like_patterns,
                        winnow_sample_size=winnow_sample_size,
                        path_allowed=_path_allowed,
                    )

            lines: list[str] = []

            def _same_file_allowed(
                file_path: str, content: str, counts: dict[str, int]
            ) -> bool:
                return dup_helpers.same_file_allowed(
                    file_path,
                    content,
                    counts,
                    max_same_file_pairs_per_file=max_same_file_pairs_per_file,
                    same_file_min_tokens=same_file_min_tokens,
                )

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
                    dup_report.append_group_pairs(
                        lines,
                        title="Cross-file",
                        groups=cross_items,
                        cross_file=True,
                        max_pairs=max_pairs,
                        same_file_allowed=_same_file_allowed,
                    )
                else:
                    if prefer_cross_file:
                        dup_report.append_group_pairs(
                            lines,
                            title="Cross-file",
                            groups=cross_items,
                            cross_file=True,
                            max_pairs=max_pairs,
                            same_file_allowed=_same_file_allowed,
                        )
                        dup_report.append_group_pairs(
                            lines,
                            title="Same-file",
                            groups=same_items,
                            cross_file=False,
                            max_pairs=max_pairs,
                            same_file_allowed=_same_file_allowed,
                            same_file_counts=same_file_counts,
                        )
                    else:
                        dup_report.append_group_pairs(
                            lines,
                            title="Same-file",
                            groups=same_items,
                            cross_file=False,
                            max_pairs=max_pairs,
                            same_file_allowed=_same_file_allowed,
                            same_file_counts=same_file_counts,
                        )
                        dup_report.append_group_pairs(
                            lines,
                            title="Cross-file",
                            groups=cross_items,
                            cross_file=True,
                            max_pairs=max_pairs,
                            same_file_allowed=_same_file_allowed,
                        )

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
                    dup_report.append_group_pairs(
                        lines,
                        title="Cross-file",
                        groups=cross_items,
                        cross_file=True,
                        max_pairs=max_pairs,
                        same_file_allowed=_same_file_allowed,
                    )
                else:
                    if prefer_cross_file:
                        dup_report.append_group_pairs(
                            lines,
                            title="Cross-file",
                            groups=cross_items,
                            cross_file=True,
                            max_pairs=max_pairs,
                            same_file_allowed=_same_file_allowed,
                        )
                        dup_report.append_group_pairs(
                            lines,
                            title="Same-file",
                            groups=same_items,
                            cross_file=False,
                            max_pairs=max_pairs,
                            same_file_allowed=_same_file_allowed,
                            same_file_counts=same_file_counts,
                        )
                    else:
                        dup_report.append_group_pairs(
                            lines,
                            title="Same-file",
                            groups=same_items,
                            cross_file=False,
                            max_pairs=max_pairs,
                            same_file_allowed=_same_file_allowed,
                            same_file_counts=same_file_counts,
                        )
                        dup_report.append_group_pairs(
                            lines,
                            title="Cross-file",
                            groups=cross_items,
                            cross_file=True,
                            max_pairs=max_pairs,
                            same_file_allowed=_same_file_allowed,
                        )

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

                dup_report.append_semantic_pairs(
                    lines,
                    results=results,
                    max_pairs=max_pairs,
                )

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
                    if winnow_code_only and not dup_helpers.is_code_file(
                        fp, row.get("metadata")
                    ):
                        continue
                    content = row.get("content") or ""
                    content = re.sub(r"^// File: .*?\n", "", content)
                    tokens = dup_helpers.tokenize(content)
                    if winnow_normalize_identifiers:
                        tokens = dup_helpers.normalize_tokens(tokens)
                    if not tokens:
                        continue

                    cid = row["id"]
                    chunk_meta[cid] = row
                    chunk_tokens[cid] = tokens
                    chunk_token_set[cid] = set(tokens)

                    if len(tokens) < winnow_small_token_threshold:
                        kgrams = dup_helpers.kgrams(
                            tokens, min(winnow_small_k, len(tokens))
                        )
                        if kgrams:
                            chunk_kgrams[cid] = kgrams
                        continue

                    chunk_fps_by_scale[cid] = {}
                    for label, k, window in scales:
                        if len(tokens) < k:
                            continue
                        fps = dup_helpers.winnow_fingerprints(tokens, k, window)
                        if not fps or len(fps) < winnow_min_fingerprints:
                            continue
                        chunk_fps_by_scale[cid][label] = fps
                        fp_counts.setdefault(label, {})
                        for h in fps:
                            fp_counts[label][h] = fp_counts[label].get(h, 0) + 1

                    if not chunk_fps_by_scale[cid]:
                        kgrams = dup_helpers.kgrams(
                            tokens, min(winnow_small_k, len(tokens))
                        )
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
                    struct_score = dup_helpers.node_type_jaccard(
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

                    if cross_file_only:
                        dup_report.append_winnow_pairs(
                            lines,
                            title="Cross-file",
                            pairs=cross_pairs,
                            max_pairs=max_pairs,
                            same_file_allowed=_same_file_allowed,
                        )
                    else:
                        if prefer_cross_file:
                            dup_report.append_winnow_pairs(
                                lines,
                                title="Cross-file",
                                pairs=cross_pairs,
                                max_pairs=max_pairs,
                                same_file_allowed=_same_file_allowed,
                            )
                            dup_report.append_winnow_pairs(
                                lines,
                                title="Same-file",
                                pairs=same_pairs,
                                max_pairs=max_pairs,
                                same_file_allowed=_same_file_allowed,
                                same_file_counts={},
                            )
                        else:
                            dup_report.append_winnow_pairs(
                                lines,
                                title="Same-file",
                                pairs=same_pairs,
                                max_pairs=max_pairs,
                                same_file_allowed=_same_file_allowed,
                                same_file_counts={},
                            )
                            dup_report.append_winnow_pairs(
                                lines,
                                title="Cross-file",
                                pairs=cross_pairs,
                                max_pairs=max_pairs,
                                same_file_allowed=_same_file_allowed,
                            )
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
                        dup_report.append_duplicate_symbol_names(lines, records)
                except Exception:
                    pass

            return "\n".join(lines)

        except Exception as exc:
            return f"Error: {exc}"

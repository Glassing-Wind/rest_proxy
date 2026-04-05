"""tools/search/semantic.py — semantic + keyword search tool."""

import os
import json
from mcp.server.fastmcp import FastMCP

from _helpers import get_memory_modules, get_project_id
from tools.search import core as search_core


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def search_codebase(
        project_paths: list,
        query: str,
        k: int = 5,
        include_metadata: bool = False,
        dedupe_files: bool = True,
        include_debug: bool = False,
        max_per_file: int = 0,
        max_per_dir: int = 2,
        meta_boost: float = 0.005,
        mode: str = "precise",
        fallback: str = "none",
        fallback_ratio: float = 0.4,
        fallback_max: int = 12,
        fallback_glob: str = "",
        exclude_tests: bool = True,
        languages: list | None = None,
        min_imports: int = 0,
        min_symbols: int = 0,
        require_diagnostics: bool = False,
        require_context: bool = False,
        include_paths: list | None = None,
        exclude_paths: list | None = None,
    ) -> str:
        """
        Perform a hybrid semantic search over one or more codebases simultaneously.
        Results are ranked by relevance using RRF (vector + full-text).

        Pass a single-element list for single-project search, or multiple paths
        for cross-project search — results are merged and annotated with their
        source project in the multi-project case.

        Args:
            project_paths: List of absolute paths to project roots to search across.
            query: Natural language or code snippet to search for.
            k: Total number of results to return (default 5).
            include_metadata: Show metadata lines in results (default False).
            dedupe_files: Collapse results to one chunk per file (default True).
            include_debug: Include clone-dedup debug entry in output (default False).
            max_per_file: Max results per file when dedupe is off (default 0 = disable).
            max_per_dir: Max results per top-level directory (default 2, 0=disable).
            meta_boost: Additive boost per metadata field present (default 0.005).
            mode: "precise" (default) or "broad" to expand coverage when query is exploratory.
            fallback: "none" (default) or "grep" to add exact-match paths when results are overly concentrated.
            fallback_ratio: Trigger fallback when unique files / results <= ratio (default 0.4).
            fallback_max: Maximum fallback file paths to show (default 12).
            fallback_glob: Optional glob filter for fallback grep (e.g., "*.ts").
            exclude_tests: Exclude test files from results (default True).
            languages: Optional allowlist of languages to include.
            min_imports: Require at least N file imports in metadata.
            min_symbols: Require at least N file symbols in metadata.
            require_diagnostics: Only return chunks with diagnostics.
            require_context: Only return chunks with a non-empty context_path.
            include_paths: Optional list of glob patterns to include (file_path).
            exclude_paths: Optional list of glob patterns to exclude (file_path).
        """
        try:
            import asyncio
            import sys
            import fnmatch
            from embedding_service import get_embedding_service
            from tools.search import fallbacks as search_fallbacks

            memory_store, _, _, _, _ = get_memory_modules()

            if not project_paths:
                return "Error: provide at least one project path."

            multi = len(project_paths) > 1

            svc = get_embedding_service()
            vecs = await svc.embed_batch_async([query])
            query_vector = vecs[0]
            if not query_vector:
                return "Error: Could not generate embedding for query."

            vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
            fetch = min(k * 10, 150)

            await memory_store.open_pool()

            pid_to_name: dict[str, str] = {}
            pid_to_path: dict[str, str] = {}
            pids = []
            for p in project_paths:
                pid = get_project_id(p)
                pids.append(pid)
                pid_to_name[pid] = p.rstrip("/").split("/")[-1]
                pid_to_path[pid] = p

            def _format_meta(meta: dict) -> list[str]:
                if not isinstance(meta, dict):
                    return []
                parts: list[str] = []
                language = meta.get("language")
                if language:
                    parts.append(f"lang={language}")
                imports = meta.get("file_imports")
                if isinstance(imports, list) and imports:
                    parts.append(f"imports={len(imports)}")
                symbols = meta.get("file_symbols")
                if isinstance(symbols, list) and symbols:
                    parts.append(f"symbols={len(symbols)}")
                node_types = meta.get("node_types")
                if isinstance(node_types, list) and node_types:
                    parts.append(f"node_types={len(node_types)}")
                diagnostics = meta.get("file_diagnostics") or {}
                if isinstance(diagnostics, dict):
                    diag_count = diagnostics.get("count")
                    if isinstance(diag_count, int) and diag_count > 0:
                        parts.append(f"diagnostics={diag_count}")
                metrics = meta.get("file_metrics") or {}
                if isinstance(metrics, dict):
                    lines = metrics.get("total_lines")
                    if isinstance(lines, int):
                        parts.append(f"lines={lines}")
                ctx = meta.get("context_path")
                ctx_line = ""
                if isinstance(ctx, list) and ctx:
                    ctx_line = "context=" + " > ".join(str(c) for c in ctx[:6])
                out = []
                if parts:
                    out.append("meta: " + ", ".join(parts))
                if ctx_line:
                    out.append(ctx_line)
                return out

            async def _search_project(pid: str) -> list[dict]:
                async with memory_store._pg_pool.connection() as conn:
                    await conn.execute("BEGIN")
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "SET LOCAL hnsw.ef_search = 100"
                        )  # resets when connection returns to pool
                        await cur.execute(
                            "SET LOCAL hnsw.iterative_scan = relaxed_order"
                        )  # auto-expands past project_id filter
                        await cur.execute(
                            """\
                            WITH semantic AS (
                                SELECT file_path, chunk_index, content, project_id, metadata,
                                       ROW_NUMBER() OVER (
                                           ORDER BY embedding <=> %(vec)s::vector
                                       ) AS sem_rank
                                FROM codebase_embeddings
                                WHERE project_id = %(pid)s
                                LIMIT %(fetch)s
                            ),
                            keyword AS (
                                SELECT file_path, chunk_index,
                                       ROW_NUMBER() OVER (
                                           ORDER BY ts_rank(search_vec,
                                               websearch_to_tsquery('english', %(qt)s)) DESC
                                       ) AS kw_rank
                                FROM codebase_embeddings
                                WHERE project_id = %(pid)s
                                  AND search_vec @@ websearch_to_tsquery('english', %(qt)s)
                                LIMIT %(fetch)s
                            )
                            SELECT s.file_path, s.chunk_index, s.content, s.project_id, s.metadata,
                                   (2.0/(60+s.sem_rank)
                                    + COALESCE(1.0/(60+k.kw_rank), 0.0)) AS rrf
                            FROM semantic s
                            LEFT JOIN keyword k
                              ON s.file_path = k.file_path
                              AND s.chunk_index = k.chunk_index
                            ORDER BY rrf DESC
                            LIMIT %(fetch)s
                        """,
                            {"vec": vec_str, "pid": pid, "qt": query, "fetch": fetch},
                        )
                        rows = await cur.fetchall()
                        return [
                            {
                                "file_path": r[0],
                                "chunk_index": r[1],
                                "content": r[2],
                                "project_id": r[3],
                                "metadata": r[4],
                                "rrf": r[5],
                            }
                            for r in rows
                        ]

            def _meta_score(meta: dict) -> int:
                if not isinstance(meta, dict):
                    return 0
                score = 0
                for key in (
                    "file_imports",
                    "file_symbols",
                    "node_types",
                    "file_metrics",
                    "file_diagnostics",
                    "context_path",
                ):
                    val = meta.get(key)
                    if isinstance(val, list) and val:
                        score += 1
                    elif isinstance(val, dict) and val:
                        score += 1
                return score

            def _passes_filters(meta: dict) -> bool:
                if not isinstance(meta, dict):
                    return False
                if languages:
                    lang = meta.get("language")
                    if not lang or lang not in languages:
                        return False
                if min_imports > 0:
                    imports = meta.get("file_imports")
                    if not isinstance(imports, list) or len(imports) < min_imports:
                        return False
                if min_symbols > 0:
                    symbols = meta.get("file_symbols")
                    if not isinstance(symbols, list) or len(symbols) < min_symbols:
                        return False
                if require_diagnostics:
                    diagnostics = meta.get("file_diagnostics") or {}
                    if (
                        not isinstance(diagnostics, dict)
                        or diagnostics.get("count", 0) <= 0
                    ):
                        return False
                if require_context:
                    ctx = meta.get("context_path")
                    if not isinstance(ctx, list) or not ctx:
                        return False
                return True

            def _path_allowed(file_path: str) -> bool:
                if not file_path:
                    return True
                if include_paths:
                    if not any(
                        fnmatch.fnmatch(file_path, pat) for pat in include_paths
                    ):
                        return False
                if exclude_paths:
                    file_lower = file_path.lower()
                    if any(
                        fnmatch.fnmatch(file_path, pat)
                        or fnmatch.fnmatch(file_lower, pat.lower())
                        for pat in exclude_paths
                    ):
                        return False
                return True

            all_results: list[dict] = []
            batch = await asyncio.gather(*[_search_project(pid) for pid in pid_to_name])
            for chunk in batch:
                all_results.extend(chunk)

            if not all_results:
                projects = ", ".join(f"'{n}'" for n in pid_to_name.values())
                return f"No matching code found in {projects}.\nEnsure projects are indexed with index_workspace()."

            if mode not in {"precise", "broad"}:
                mode = "precise"

            if exclude_tests:
                exclude_paths = (exclude_paths or []) + [
                    "*test*",
                    "*tests*",
                    "*Test*",
                    "*Tests*",
                ]
                exclude_paths = list(dict.fromkeys(exclude_paths))

            if exclude_tests:
                exclude_paths = (exclude_paths or []) + [
                    "*test*",
                    "*tests*",
                    "*Test*",
                    "*Tests*",
                ]

            if mode == "broad":
                if max_per_dir == 2:
                    max_per_dir = 4
                if meta_boost == 0.005:
                    meta_boost = 0.0
                if fallback == "none":
                    fallback = "grep"
                if max_per_file == 0:
                    max_per_file = 2

            filters_active = any(
                [
                    languages,
                    min_imports > 0,
                    min_symbols > 0,
                    require_diagnostics,
                    require_context,
                    include_paths,
                    exclude_paths,
                ]
            )
            clone_dedup = os.getenv("LM_PROXY_CLONE_DEDUP", "0").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if filters_active or clone_dedup or meta_boost > 0:
                include_metadata = True

            if include_metadata:
                for r in all_results:
                    meta = r.get("metadata")
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    r_meta = meta if isinstance(meta, dict) else {}
                    r["_meta"] = r_meta
                    r["meta_score"] = _meta_score(r_meta)
                if filters_active:
                    all_results = [
                        r
                        for r in all_results
                        if _passes_filters(r.get("_meta", {}))
                        and _path_allowed(r.get("file_path", ""))
                    ]
                for r in all_results:
                    base_score = r.get("rrf", 0.0)
                    try:
                        base_score = float(base_score)
                    except (TypeError, ValueError):
                        base_score = 0.0
                    if meta_boost > 0:
                        r["rank_score"] = base_score + (
                            r.get("meta_score", 0) * meta_boost
                        )
                    else:
                        r["rank_score"] = base_score
                all_results.sort(
                    key=lambda r: (
                        r.get("rank_score", r["rrf"]),
                        r.get("meta_score", 0),
                    ),
                    reverse=True,
                )
            else:
                all_results.sort(key=lambda r: r["rrf"], reverse=True)

            if clone_dedup:
                try:
                    import graph_bootstrap

                    driver = await graph_bootstrap.require_driver()
                    if driver:
                        for r in all_results:
                            meta = r.get("_meta")
                            if not isinstance(meta, dict):
                                raw = r.get("metadata")
                                if isinstance(raw, str):
                                    try:
                                        meta = json.loads(raw)
                                    except Exception:
                                        meta = {}
                                elif isinstance(raw, dict):
                                    meta = raw
                                else:
                                    meta = {}
                                r["_meta"] = meta

                        by_project: dict[str, list[dict]] = {}
                        for idx, r in enumerate(all_results):
                            fp = r.get("file_path")
                            line = (r.get("_meta") or {}).get("start_line")
                            pid = r.get("project_id")
                            if not fp or not isinstance(line, int) or not pid:
                                continue
                            by_project.setdefault(pid, []).append(
                                {"idx": idx, "fp": fp, "line": line}
                            )

                        clone_map: dict[int, str | None] = {}
                        for pid, items in by_project.items():
                            async with driver.session(
                                database=graph_bootstrap._NEO4J_DB
                            ) as session:
                                records = await search_core._execute_read(
                                    session,
                                    """
                                    UNWIND $items AS item
                                    MATCH (f:File {project_id:$pid, filepath:item.fp})-[:CONTAINS]->(s)
                                    WHERE (s:Function OR s:Method OR s:Class OR s:Struct)
                                      AND s.start_line <= item.line AND s.end_line >= item.line
                                    OPTIONAL MATCH (s)-[:MEMBER_OF_CLONE_GROUP]->(g:CloneGroup)
                                    WITH item, s, g
                                    ORDER BY (s.end_line - s.start_line) ASC
                                    WITH item, collect(g.id)[0] AS gid
                                    RETURN item.idx AS idx, gid
                                    """,
                                    items=items,
                                    pid=pid,
                                    op="clone_dedup_map",
                                )
                            for rec in records:
                                clone_map[int(rec["idx"])] = rec.get("gid")

                        file_group_map: dict[str, str] = {}
                        file_group_source = (
                            os.getenv("LM_PROXY_FILE_CLONE_SOURCE", "function")
                            .strip()
                            .lower()
                        )
                        if file_group_source not in {"chunk", "function", "hybrid"}:
                            file_group_source = "function"

                        for pid, items in by_project.items():
                            async with driver.session(
                                database=graph_bootstrap._NEO4J_DB
                            ) as session:
                                file_records = []
                                if file_group_source == "chunk":
                                    file_records = await search_core._execute_read(
                                        session,
                                        """
                                        UNWIND $items AS item
                                        MATCH (f:File {project_id:$pid, filepath:item.fp})
                                        OPTIONAL MATCH (f)-[:MEMBER_OF_FILE_CLONE_GROUP]->(g:FileCloneGroup)
                                        RETURN item.fp AS fp, collect(g.id)[0] AS gid
                                        """,
                                        items=items,
                                        pid=pid,
                                        op="clone_dedup_file_map",
                                    )

                                func_records = []
                                if file_group_source in {"function", "hybrid"}:
                                    func_records = await search_core._execute_read(
                                        session,
                                        """
                                        UNWIND $items AS item
                                        MATCH (f:File {project_id:$pid, filepath:item.fp})-[:CONTAINS]->(s)
                                        WHERE (s:Function OR s:Method OR s:Class OR s:Struct)
                                          AND s.start_line <= item.line AND s.end_line >= item.line
                                        OPTIONAL MATCH (s)-[:MEMBER_OF_CLONE_GROUP]->(g:CloneGroup)
                                        WITH item, collect(DISTINCT g.id) AS gids
                                        RETURN item.fp AS fp, gids
                                        """,
                                        items=items,
                                        pid=pid,
                                        op="clone_dedup_file_map_function",
                                    )

                                func_group_map: dict[str, str] = {}
                                for row in func_records:
                                    fp = row.get("fp")
                                    gids = [g for g in (row.get("gids") or []) if g]
                                    if not fp or not gids:
                                        continue
                                    gids.sort()
                                    import hashlib
                                    gid = hashlib.md5(
                                        "|".join(gids).encode()
                                    ).hexdigest()[:12]
                                    func_group_map[fp] = gid

                                if func_group_map:
                                    for fp, gid in func_group_map.items():
                                        file_group_map[fp] = gid

                                for row in file_records:
                                    fp = row.get("fp")
                                    gid = row.get("gid")
                                    if fp and gid and fp not in file_group_map:
                                        file_group_map[fp] = gid

                        debug_clone = os.getenv(
                            "LM_PROXY_CLONE_DEBUG", "0"
                        ).strip().lower() in {
                            "1",
                            "true",
                            "yes",
                            "on",
                        }

                        debug_lines: list[str] = []
                        if debug_clone:
                            debug_lines.append(
                                f"clone_dedup file_groups={len(file_group_map)}"
                            )

                        if file_group_map:
                            seen_file_gids: set[str] = set()
                            deduped_by_file: list[dict] = []
                            for r in all_results:
                                fp = r.get("file_path")
                                file_gid = file_group_map.get(fp) if fp else None
                                if debug_clone and fp:
                                    debug_lines.append(
                                        f"clone_dedup file={fp} gid={file_gid}"
                                    )
                                if file_gid:
                                    if file_gid in seen_file_gids:
                                        continue
                                    seen_file_gids.add(file_gid)
                                deduped_by_file.append(r)
                            all_results = deduped_by_file

                        seen_gids: set[str] = set()
                        deduped: list[dict] = []
                        for idx, r in enumerate(all_results):
                            gid = clone_map.get(idx)
                            if gid:
                                if gid in seen_gids:
                                    continue
                                seen_gids.add(gid)
                            deduped.append(r)
                        all_results = deduped
                        if debug_clone and debug_lines and include_debug:
                            all_results.insert(
                                0,
                                {
                                    "file_path": "[clone_dedup_debug]",
                                    "content": "\n".join(debug_lines[:20]),
                                    "rrf": 1.0,
                                    "project_id": pid,
                                },
                            )
                except Exception:
                    pass

            if dedupe_files:
                seen_files: set[str] = set()
                deduped_files: list[dict] = []
                for r in all_results:
                    fp = r.get("file_path")
                    if not fp or fp in seen_files:
                        continue
                    seen_files.add(fp)
                    deduped_files.append(r)
                all_results = deduped_files

            if max_per_file and max_per_file > 0:
                per_file_counts: dict[str, int] = {}
                capped: list[dict] = []
                for r in all_results:
                    fp = r.get("file_path") or ""
                    if not fp:
                        continue
                    count = per_file_counts.get(fp, 0)
                    if count >= max_per_file:
                        continue
                    per_file_counts[fp] = count + 1
                    capped.append(r)
                all_results = capped

            if max_per_dir and max_per_dir > 0:
                dir_counts: dict[str, int] = {}
                diversified: list[dict] = []
                for r in all_results:
                    fp = r.get("file_path") or ""
                    norm = fp.replace("\\", "/")
                    top = (
                        norm.split("/")[0]
                        if "/" in norm
                        else os.path.dirname(norm) or "."
                    )
                    if dir_counts.get(top, 0) >= max_per_dir:
                        continue
                    dir_counts[top] = dir_counts.get(top, 0) + 1
                    diversified.append(r)
                all_results = diversified
            top = all_results[:k]

            lines = []
            if multi:
                lines.append(
                    f"Cross-project search: '{query}'  ({len(pid_to_name)} projects)\n"
                )

            for i, r in enumerate(top, 1):
                proj = pid_to_name.get(r["project_id"], r["project_id"])
                if multi:
                    lines.append(
                        f"[{i}] [{proj}] {r['file_path']}  (score: {r['rrf']:.4f})"
                    )
                else:
                    lines.append(f"--- {r['file_path']} (Score: {r['rrf']:.4f}) ---")
                if include_metadata:
                    meta = r.get("_meta")
                    if not isinstance(meta, dict):
                        meta = r.get("metadata")
                        if isinstance(meta, str):
                            try:
                                meta = json.loads(meta)
                            except Exception:
                                meta = {}
                    lines.extend(_format_meta(meta if isinstance(meta, dict) else {}))
                lines.append(r["content"].strip())
                lines.append("")

            if fallback == "grep" and top:
                unique_files = len(
                    {r.get("file_path") for r in top if r.get("file_path")}
                )
                ratio = unique_files / max(1, len(top))
                if unique_files <= 1 or ratio <= fallback_ratio:
                    fallback_lines: list[str] = []
                    debug_tokens = (
                        search_fallbacks.extract_fallback_tokens(query)
                        if include_debug
                        else []
                    )
                    for pid, proj_name in pid_to_name.items():
                        proj_root = pid_to_path.get(pid)
                        if not proj_root:
                            continue
                        matches, dbg = await search_fallbacks.run_fallback_grep(
                            proj_root,
                            query,
                            fallback_glob,
                            fallback_max,
                        )
                        if include_debug and dbg:
                            dbg_info = ", ".join(
                                f"{k}={v}" for k, v in dbg.items() if v is not None
                            )
                            if dbg_info:
                                fallback_lines.append(f"- [debug] {dbg_info}")
                        if not matches:
                            continue
                        for fp in matches:
                            label = f"[{proj_name}] {fp}" if multi else fp
                            fallback_lines.append(f"- {label}")
                    if include_debug:
                        token_text = (
                            ", ".join(debug_tokens) if debug_tokens else "(none)"
                        )
                        lines.append(f"Fallback grep tokens: {token_text}")
                        if fallback_glob:
                            lines.append(f"Fallback grep glob: {fallback_glob}")
                        rg_hint = os.getenv("LM_PROXY_RG_PATH") or "(auto)"
                        lines.append(f"Fallback grep rg path: {rg_hint}")
                        lines.append(f"Debug sys.executable: {sys.executable}")
                        lines.append(f"Debug PATH: {os.getenv('PATH', '')}")
                    if fallback_lines:
                        lines.append("Fallback (grep):")
                        lines.extend(fallback_lines)
                    if include_debug or fallback_lines:
                        lines.append("")

            return "\n".join(lines)
        except Exception as e:
            return f"Error searching codebase: {str(e)}"

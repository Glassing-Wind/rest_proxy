"""tools/code_search.py — codebase search and cross-reference tools."""

import hashlib
import os
from neo4j import unit_of_work
import json
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules


def register(mcp: FastMCP) -> None:

    _TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
    _TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
    _TX_METADATA_BASE = {"source": "lm_proxy", "tool": "code_search"}

    async def _execute_read(session, cypher: str, op: str | None = None, **params):
        metadata = dict(_TX_METADATA_BASE)
        op_value = op or "read"
        if _TX_OP_PREFIX:
            op_value = f"{_TX_OP_PREFIX}.{op_value}"
        metadata["op"] = op_value

        @unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)
        async def _tx(tx):
            result = await tx.run(cypher, **params)
            return await result.data()

        if hasattr(session, "execute_read"):
            return await session.execute_read(_tx)
        return await _tx(session)

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
            for p in project_paths:
                pid = hashlib.md5(p.encode()).hexdigest()[:12]
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
                    if any(fnmatch.fnmatch(file_path, pat) for pat in exclude_paths):
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
                                records = await _execute_read(
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
                                    file_records = await _execute_read(
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
                                    func_records = await _execute_read(
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

            def _extract_fallback_tokens(text: str) -> list[str]:
                import re

                tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text)
                return [t for t in tokens if len(t) >= 3]

            async def _run_fallback_grep(project_root: str) -> tuple[list[str], dict]:
                import asyncio
                import shutil

                tokens = _extract_fallback_tokens(query)
                if not tokens:
                    return [], {"error": "no_tokens"}
                pattern = "|".join(sorted(set(tokens)))
                rg_path = os.getenv("LM_PROXY_RG_PATH") or shutil.which("rg")
                if not rg_path:
                    return [], {"error": "rg_not_found"}
                cmd = [rg_path, "-l", pattern]
                if fallback_glob:
                    root = fallback_glob
                    if any(ch in fallback_glob for ch in "*?["):
                        root = fallback_glob.split("*")[0]
                        if root.endswith("/"):
                            root = root[:-1]
                        if not root:
                            root = "."
                    cmd.append(root)
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        cwd=project_root,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout, stderr = await asyncio.wait_for(
                            proc.communicate(), timeout=8.0
                        )
                    except asyncio.TimeoutError:
                        proc.kill()
                        return [], {"error": "timeout"}
                except Exception:
                    return [], {"error": "spawn_failed"}
                if proc.returncode not in (0, 1):
                    err_text = stderr.decode("utf-8", errors="ignore")
                    return [], {
                        "error": "rg_failed",
                        "code": proc.returncode,
                        "stderr": err_text[:200],
                    }
                output = stdout.decode("utf-8", errors="ignore")
                paths = [p.strip() for p in output.splitlines() if p.strip()]
                if fallback_glob:
                    paths = [p for p in paths if fnmatch.fnmatch(p, fallback_glob)]
                return paths[: max(0, fallback_max)], {
                    "code": proc.returncode,
                    "count": len(paths),
                }

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
                        _extract_fallback_tokens(query) if include_debug else []
                    )
                    for pid, proj_name in pid_to_name.items():
                        proj_root = pid_to_path.get(pid)
                        if not proj_root:
                            continue
                        matches, dbg = await _run_fallback_grep(proj_root)
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

    @mcp.tool()
    async def trace_symbol_cross_project(
        symbol_name: str,
        source_project: str,
        target_project: str,
    ) -> str:
        """
        Trace a symbol from its definition in one project to its usages in another.

        Ideal for gRPC-style workflows: find where a service, message, or function
        is *defined* server-side and how it is *called or referenced* client-side.

        Combines two sources per side:
        - Neo4j structural graph (definition location, call-edges)
        - Postgres semantic search (text occurrences in chunks)

        Args:
            symbol_name:     Exact name of the symbol to trace (e.g. 'GenerateImageRequest').
            source_project:  Absolute path to the project where the symbol is defined.
            target_project:  Absolute path to the project that consumes/calls the symbol.
        """
        try:
            import asyncio
            from embedding_service import get_embedding_service

            memory_store, _, _, _, _ = get_memory_modules()

            src_id = hashlib.md5(source_project.encode()).hexdigest()[:12]
            tgt_id = hashlib.md5(target_project.encode()).hexdigest()[:12]
            src_name = source_project.rstrip("/").split("/")[-1]
            tgt_name = target_project.rstrip("/").split("/")[-1]

            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            # ── 1. Definition in source project ──────────────────────────────
            definition: dict = {}
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    """
                    MATCH (s {name: $name, project_id: $pid})
                    WHERE s:Function OR s:Class OR s:Struct OR s:Trait
                       OR s:Enum OR s:Method OR s:Protocol
                    OPTIONAL MATCH (s)<-[:CONTAINS]-(f:File)
                    RETURN labels(s)[0]  AS kind,
                           s.filepath    AS filepath,
                           s.start_line  AS start_line,
                           s.end_line    AS end_line,
                           s.signature   AS signature
                    LIMIT 1
                """,
                    name=symbol_name,
                    pid=src_id,
                    op="trace_symbol_definition",
                )
                if records:
                    definition = dict(records[0])

            # ── 2. Call-graph usages in target project ────────────────────────
            graph_usages: list[str] = []
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    """
                    MATCH (target {name: $name})
                    WHERE target:Function OR target:Class OR target:Struct
                       OR target:Method   OR target:Trait OR target:Protocol
                    MATCH (caller {project_id: $tpid})-[:CALLS|CALLS_INFERRED]->(target)
                    RETURN DISTINCT
                           caller.name      AS caller_name,
                           caller.filepath  AS caller_file,
                           caller.start_line AS caller_line,
                           labels(caller)[0] AS caller_kind
                    ORDER BY caller.filepath, caller.start_line
                    LIMIT 20
                """,
                    name=symbol_name,
                    tpid=tgt_id,
                    op="trace_symbol_graph_usages",
                )
                for rec in records:
                    name = rec["caller_name"] or "(file scope)"
                    fp = rec["caller_file"] or "?"
                    line = f":{rec['caller_line']}" if rec["caller_line"] else ""
                    graph_usages.append(
                        f"  {name}{line}  [{rec['caller_kind'] or 'Node'}]  in {fp}"
                    )

            # ── 3. Semantic text hits in target project ───────────────────────
            sem_usages: list[str] = []
            await memory_store.open_pool()

            svc = get_embedding_service()
            vecs = await svc.embed_batch_async([symbol_name])
            query_vector = vecs[0]

            async def _fetch_semantic():
                vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
                async with memory_store._pg_pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            WITH sem AS (
                                SELECT file_path, chunk_index, content,
                                       ROW_NUMBER() OVER (
                                           ORDER BY embedding <=> %(vec)s::vector
                                       ) AS sem_rank
                                FROM codebase_embeddings
                                WHERE project_id = %(pid)s
                                LIMIT 40
                            ),
                            kw AS (
                                SELECT file_path, chunk_index,
                                       ROW_NUMBER() OVER (
                                           ORDER BY ts_rank(search_vec,
                                               plainto_tsquery('english', %(qt)s)) DESC
                                       ) AS kw_rank
                                FROM codebase_embeddings
                                WHERE project_id = %(pid)s
                                  AND content ILIKE %(ilike)s
                                LIMIT 40
                            )
                            SELECT s.file_path, s.chunk_index, s.content,
                                   (1.0/(60+s.sem_rank) + COALESCE(1.0/(60+k.kw_rank), 0.0)) AS rrf
                            FROM sem s LEFT JOIN kw k
                              ON s.file_path = k.file_path AND s.chunk_index = k.chunk_index
                            WHERE s.content ILIKE %(ilike)s
                            ORDER BY rrf DESC LIMIT 5
                        """,
                            {
                                "vec": vec_str,
                                "pid": tgt_id,
                                "qt": symbol_name,
                                "ilike": f"%{symbol_name}%",
                            },
                        )
                        return await cur.fetchall()

            async def _fetch_src_preview():
                if not definition.get("filepath"):
                    return []
                async with memory_store._pg_pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            SELECT content FROM codebase_embeddings
                            WHERE project_id = %s AND file_path = %s
                            ORDER BY chunk_index LIMIT 2
                        """,
                            (src_id, definition["filepath"]),
                        )
                        return await cur.fetchall()

            sem_rows, src_rows = await asyncio.gather(
                _fetch_semantic(), _fetch_src_preview()
            )

            for fp, idx, content, rrf in sem_rows:
                sem_usages.append(
                    f"  [chunk {idx}]  {fp}  (score: {rrf:.4f})\n"
                    f"    {content[:200].strip().replace(chr(10), ' ')}…"
                )

            # ── 4. Assemble output ────────────────────────────────────────────
            lines = [
                f"## Cross-project trace: `{symbol_name}`",
                f"   {src_name}  →  {tgt_name}",
                "",
            ]

            if definition:
                lines += [
                    f"### Definition  [{src_name}]",
                    f"  Kind:      {definition.get('kind', '?')}",
                    f"  File:      {definition.get('filepath', '?')}  "
                    f"L{definition.get('start_line', '?')}–{definition.get('end_line', '?')}",
                ]
                if definition.get("signature"):
                    lines.append(f"  Signature: {definition['signature']}")
                if src_rows:
                    preview = "\n".join(r[0][:400] for r in src_rows)
                    lines += ["", f"```\n{preview.strip()}\n```"]
            else:
                lines.append(f"⚠️  `{symbol_name}` not found in Neo4j for [{src_name}].")
                lines.append(
                    "   (Symbol may be in an un-indexed file or a different casing.)"
                )

            lines += ["", f"### Usages  [{tgt_name}]"]

            if graph_usages:
                lines.append(f"**Call-graph hits** ({len(graph_usages)}):")
                lines.extend(graph_usages)
            else:
                lines.append("  No direct call-graph edges found.")

            if sem_usages:
                lines += ["", f"**Semantic text hits** ({len(sem_usages)}):"]
                lines.extend(sem_usages)
            else:
                lines.append("  No semantic text hits found.")

            if not graph_usages and not sem_usages:
                lines += [
                    "",
                    f"💡 `{symbol_name}` appears to be defined in [{src_name}] but not yet referenced in [{tgt_name}].",
                    f"   Try `search_multi_project` with a broader semantic query.",
                ]

            return "\n".join(lines)
        except Exception as e:
            return f"Error tracing cross-project symbol: {str(e)}"

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
                            records = await _execute_read(
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

    @mcp.tool()
    async def query_graph(cypher_query: str) -> str:
        """
        Execute a raw Cypher query on the Neo4j structural graph.
        Useful for complex relationship analysis.

        Args:
            cypher_query: The Cypher query string.
        """
        try:
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            if not driver:
                return "Error: Could not connect to Neo4j."
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                data = await _execute_read(session, cypher_query, op="query_graph")
            if not data:
                return "No results found."
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error querying graph: {str(e)}"

    @mcp.tool()
    async def find_definitions(symbol_name: str) -> str:
        """
        Search for the definition of a class, function, or struct across ALL indexed projects.
        Ideal for cross-project dependency discovery.

        Args:
            symbol_name: Name of the symbol to find.
        """
        try:
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            cypher = """
            MATCH (n)
            WHERE (n:Class OR n:Function) AND n.name = $name
            RETURN n.project_id AS project, n.filepath AS file,
                   n.start_line AS line, labels(n)[0] AS type
            """
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                output = [f"Found {symbol_name} in the following locations:"]
                records = await _execute_read(
                    session, cypher, name=symbol_name, op="find_definitions"
                )
                for record in records:
                    loc = record["file"] or "unknown"
                    line = record["line"]
                    loc_str = f"{loc}:{line}" if line is not None else loc
                    output.append(
                        f"- [{record['type']}] Project: {record['project']}, File: {loc_str}"
                    )
            if len(output) == 1:
                return f"Symbol '{symbol_name}' not found in any indexed project."
            return "\n".join(output)
        except Exception as e:
            return f"Error finding definition: {str(e)}"

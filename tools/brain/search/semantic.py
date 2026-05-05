"""tools/search/semantic.py — semantic + keyword search tool."""

import json
import os
from mcp.server.fastmcp import FastMCP

from _helpers import get_memory_modules, get_project_id
from proxy.logging import debug_log
from tools.brain.search import core as search_core
from tools.brain.search import semantic_helpers as sem_helpers


async def _load_cargo_crate_rows(driver, neo4j_db: str, project_ids: list[str]) -> dict[str, list[dict]]:
    if not driver or not project_ids:
        return {}
    project_ids = [pid for pid in project_ids if pid]
    if not project_ids:
        return {}
    async with driver.session(database=neo4j_db) as session:
        schema_rows = await search_core._execute_read(
            session,
            """
            CALL db.labels() YIELD label
            RETURN collect(label) AS labels
            """,
            op="search_codebase_cargo_schema_labels",
        )
        labels = set(schema_rows[0].get("labels") or []) if schema_rows else set()
        if "CargoCrate" not in labels:
            return {}
        rows_by_pid: dict[str, list[dict]] = {}
        for pid in project_ids:
            rows = await search_core._execute_read(
                session,
                """
                MATCH (c:CargoCrate {project_id:$p})-[:DEFINED_IN_FILE]->(mf:File {project_id:$p})
                RETURN c.name AS crate,
                       c.crate_name AS crate_name,
                       mf.filepath AS manifest_path
                ORDER BY size(mf.filepath) DESC, c.name
                """,
                p=pid,
                op="search_codebase_cargo_crates",
            )
            if rows:
                rows_by_pid[pid] = rows
        return rows_by_pid


async def _load_rescue_rows(
    conn,
    *,
    pid: str,
    file_paths: list[str],
    member_exprs: list[str] | None = None,
) -> list[dict]:
    if not file_paths:
        return []
    member_exprs = [
        str(expr).strip().lower()
        for expr in (member_exprs or [])
        if str(expr).strip()
    ]
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH scored AS (
                SELECT file_path, chunk_index, content, project_id, metadata,
                       CASE
                           WHEN cardinality(%(member_exprs)s::text[]) > 0
                                AND EXISTS (
                                    SELECT 1
                                    FROM jsonb_array_elements_text(
                                        CASE
                                            WHEN jsonb_typeof(metadata->'member_usages') = 'array'
                                            THEN metadata->'member_usages'
                                            ELSE '[]'::jsonb
                                        END
                                    ) AS expr(value)
                                    WHERE lower(expr.value) = ANY(%(member_exprs)s)
                                )
                           THEN 1
                           ELSE 0
                       END AS exact_member_usage_hit
                FROM codebase_embeddings
                WHERE project_id = %(pid)s
                  AND file_path = ANY(%(paths)s)
            ),
            ranked AS (
                SELECT file_path, chunk_index, content, project_id, metadata, exact_member_usage_hit,
                       ROW_NUMBER() OVER (
                           PARTITION BY file_path
                           ORDER BY exact_member_usage_hit DESC, chunk_index ASC
                       ) AS chunk_rank
                FROM scored
            )
            SELECT file_path, chunk_index, content, project_id, metadata, exact_member_usage_hit
            FROM ranked
            WHERE chunk_rank <= 8
            ORDER BY file_path, exact_member_usage_hit DESC, chunk_index
            """,
            {"pid": pid, "paths": file_paths, "member_exprs": member_exprs},
        )
        rows = await cur.fetchall()
    return [
        {
            "file_path": r[0],
            "chunk_index": r[1],
            "content": r[2],
            "project_id": r[3],
            "metadata": r[4],
            "implementation_exact_member_usage_hit": int(r[5] or 0),
            "rrf": 0.0,
            "_definition_rescue": True,
        }
        for r in rows
    ]


async def _load_path_hint_rows(
    conn,
    *,
    pid: str,
    path_hints: list[str],
    identifier_exprs: list[str] | None = None,
    max_files: int = 12,
) -> list[dict]:
    normalized_hints = [
        f"%{str(hint).strip().lower()}%"
        for hint in (path_hints or [])
        if str(hint).strip()
    ]
    identifier_exprs = [
        str(expr).strip().lower()
        for expr in (identifier_exprs or [])
        if str(expr).strip()
    ]
    if not normalized_hints:
        return []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH matched_files AS (
                SELECT file_path,
                       MAX(
                           CASE
                               WHEN lower(file_path) LIKE ANY(%(patterns)s::text[]) THEN 1
                               ELSE 0
                           END
                       ) AS path_hint_hit,
                       MIN(
                           CASE
                               WHEN lower(file_path) LIKE '%%/src/main.rs'
                                 OR lower(file_path) LIKE '%%/src/main.py'
                                 OR lower(file_path) LIKE '%%/src/main.ts'
                                 OR lower(file_path) LIKE '%%/src/main.tsx'
                                 OR lower(file_path) LIKE '%%/src/main.js'
                                 OR lower(file_path) LIKE '%%/src/main.jsx'
                                 THEN 0
                               WHEN lower(file_path) LIKE '%%/src/lib.rs'
                                 OR lower(file_path) LIKE '%%/__init__.py'
                                 THEN 1
                               WHEN lower(file_path) ~ '\\.(rs|py|ts|tsx|js|jsx|go|java|swift|rb|php|cs)$'
                                 THEN 2
                               WHEN lower(file_path) LIKE '%%cargo.toml'
                                 OR lower(file_path) LIKE '%%package.json'
                                 THEN 4
                               WHEN lower(file_path) ~ '\\.(md|markdown|mdx|json|ya?ml|toml|plist|xml|svg|png|jpg|jpeg|gif|ico)$'
                                 THEN 6
                               WHEN lower(file_path) LIKE '%%/.gitignore'
                                 THEN 7
                               ELSE 5
                           END
                       ) AS code_rank
                FROM codebase_embeddings
                WHERE project_id = %(pid)s
                  AND lower(file_path) LIKE ANY(%(patterns)s::text[])
                GROUP BY file_path
                ORDER BY path_hint_hit DESC, code_rank ASC, file_path
                LIMIT %(max_files)s
            ),
            ranked AS (
                SELECT c.file_path, c.chunk_index, c.content, c.project_id, c.metadata,
                       m.path_hint_hit, m.code_rank,
                       CASE
                           WHEN cardinality(%(identifier_exprs)s::text[]) > 0
                                AND EXISTS (
                                    SELECT 1
                                    FROM unnest(%(identifier_exprs)s::text[]) AS expr(value)
                                    WHERE lower(c.content) LIKE ('%%' || expr.value || '%%')
                                )
                           THEN 1
                           ELSE 0
                       END AS exact_identifier_text_hit,
                       ROW_NUMBER() OVER (
                           PARTITION BY c.file_path
                           ORDER BY
                               CASE
                                   WHEN cardinality(%(identifier_exprs)s::text[]) > 0
                                        AND EXISTS (
                                            SELECT 1
                                            FROM unnest(%(identifier_exprs)s::text[]) AS expr(value)
                                            WHERE lower(c.content) LIKE ('%%' || expr.value || '%%')
                                        )
                                   THEN 1
                                   ELSE 0
                               END DESC,
                               CASE
                                   WHEN lower(coalesce(c.metadata->'context_path'->>0, '')) LIKE '%%view'
                                     AND lower(coalesce(c.metadata->'context_path'->>1, '')) = 'body'
                                   THEN 1
                                   ELSE 0
                               END DESC,
                               c.chunk_index ASC
                       ) AS chunk_rank
                FROM codebase_embeddings c
                JOIN matched_files m
                  ON m.file_path = c.file_path
                WHERE c.project_id = %(pid)s
            )
            SELECT file_path, chunk_index, content, project_id, metadata, path_hint_hit, exact_identifier_text_hit
            FROM ranked
            WHERE chunk_rank <= 20
            ORDER BY exact_identifier_text_hit DESC, path_hint_hit DESC, code_rank ASC, file_path, chunk_index
            """,
            {
                "pid": pid,
                "patterns": normalized_hints,
                "identifier_exprs": identifier_exprs,
                "max_files": max_files,
            },
        )
        rows = await cur.fetchall()
    return [
        {
            "file_path": r[0],
            "chunk_index": r[1],
            "content": r[2],
            "project_id": r[3],
            "metadata": r[4],
            "implementation_path_hint_hit": int(r[5] or 0),
            "implementation_exact_identifier_text_hit": int(r[6] or 0),
            "rrf": 0.0,
            "_definition_rescue": True,
        }
        for r in rows
    ]


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def rerank_retrieval_results(
        query: str,
        results: list[dict],
        mode: str = "code",
        experiments: dict | None = None,
        include_debug: bool = False,
    ) -> str:
        """
        Rerank a caller-supplied ranked result list using rest_proxy duplicate-aware retrieval policy.

        Args:
            query: Retrieval query that produced the ranked candidates.
            results: Ranked candidate list. Each item should include content plus file_path or source_url.
            mode: Retrieval corpus mode: "code" or "docs".
            experiments: Optional duplicate-policy overrides.
            include_debug: Include compact duplicate decision trace.
        """
        try:
            mode_norm = (mode or "code").strip().lower()
            if mode_norm not in {"code", "docs"}:
                return json.dumps({"error": "Invalid mode. Use 'code' or 'docs'."}, indent=2)
            if not isinstance(results, list):
                return json.dumps({"error": "results must be a list of dict items."}, indent=2)
            contract = sem_helpers.rerank_retrieval_results_contract(
                results,
                query=query,
                mode=mode_norm,
                experiments=experiments,
                include_debug=include_debug,
            )
            return json.dumps(contract, indent=2, sort_keys=True)
        except Exception as e:
            return json.dumps({"error": f"Error reranking retrieval results: {str(e)}"}, indent=2)

    @mcp.tool()
    async def analyze_duplicate_results(
        query: str,
        results: list[dict],
        mode: str = "code",
    ) -> str:
        """
        Analyze duplicate structure for a ranked result list without reranking it.

        Args:
            query: Retrieval query that produced the ranked candidates.
            results: Ranked candidate list. Each item should include content plus file_path or source_url.
            mode: Retrieval corpus mode: "code" or "docs".
        """
        try:
            mode_norm = (mode or "code").strip().lower()
            if mode_norm not in {"code", "docs"}:
                return json.dumps({"error": "Invalid mode. Use 'code' or 'docs'."}, indent=2)
            if not isinstance(results, list):
                return json.dumps({"error": "results must be a list of dict items."}, indent=2)
            contract = sem_helpers.analyze_duplicate_results_contract(
                results,
                query=query,
                mode=mode_norm,
            )
            return json.dumps(contract, indent=2, sort_keys=True)
        except Exception as e:
            return json.dumps({"error": f"Error analyzing duplicate results: {str(e)}"}, indent=2)

    @mcp.tool()
    async def trace_code_ranking(
        query: str,
        results: list[dict],
    ) -> str:
        """
        Build a code-ranking trace for implementation-intent queries.

        Args:
            query: Retrieval query to classify and trace.
            results: Candidate result rows to score and explain.
        """
        try:
            if not isinstance(results, list):
                return json.dumps({"error": "results must be a list of dict items."}, indent=2)
            trace = sem_helpers.build_implementation_ranking_trace(results, query)
            return json.dumps(trace, indent=2, sort_keys=True)
        except Exception as e:
            return json.dumps({"error": f"Error tracing code ranking: {str(e)}"}, indent=2)

    @mcp.tool()
    async def search_codebase(
        workspace_ids: list | None = None,
        query: str | None = None,
        workspace_id: str | None = None,
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
        languages: list|None = None,
        min_imports: int = 0,
        min_symbols: int = 0,
        require_diagnostics: bool = False,
        require_context: bool = False,
        crate_contains: str | None = None,
        include_paths: list|None = None,
        exclude_paths: list|None = None,
    ) -> str:
        """
        Perform a hybrid semantic search over one or more codebases simultaneously.
        Results are ranked by relevance using RRF (vector + full-text).

        Pass a single-element list for single-project search, or multiple IDs
        for cross-project search — results are merged and annotated with their
        source project in the multi-project case.

        Args:
            workspace_ids: List of logical workspace IDs or absolute paths to search across.
            workspace_id: Single logical workspace ID or absolute path for one-project search.
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
            crate_contains: Optional Cargo crate filter for Rust workspace files.
            include_paths: Optional list of glob patterns to include (file_path).
            exclude_paths: Optional list of glob patterns to exclude (file_path).
        """
        try:
            import asyncio
            import sys
            from embedding_service import get_embedding_service
            from tools.brain.search import fallbacks as search_fallbacks
            from _helpers import WorkspaceRegistry, get_workspace_path

            memory_store, _, _, _, _ = get_memory_modules()

            if not isinstance(query, str) or not query.strip():
                return "Error: provide a non-empty query."

            normalized_workspace_ids: list = list(workspace_ids or [])
            if workspace_id is not None:
                normalized_workspace_ids.append(workspace_id)
            if not normalized_workspace_ids:
                return "Error: provide at least one workspace ID or path."
            deduped_workspace_ids: list = []
            for value in normalized_workspace_ids:
                if value in deduped_workspace_ids:
                    continue
                deduped_workspace_ids.append(value)
            workspace_ids = deduped_workspace_ids

            multi = len(workspace_ids) > 1

            impl_intent = sem_helpers.implementation_query_intent(query)
            impl_query_class = sem_helpers.implementation_query_class(query)
            exact_identifiers = sorted(sem_helpers.implementation_query_exact_identifiers(query))
            member_exprs = sorted(sem_helpers.implementation_query_member_exprs(query))
            path_hints = sem_helpers.implementation_query_path_hints(query)
            inferred_filename_hints = sem_helpers.implementation_inferred_filename_hints(query)
            if inferred_filename_hints:
                path_hints = sorted(set(path_hints) | set(inferred_filename_hints))
            explicit_runtime_entrypoints = sem_helpers.implementation_expected_runtime_entrypoint_paths(
                query
            )

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
            for w_id in workspace_ids:
                pid = WorkspaceRegistry.resolve_id(w_id) or get_project_id(w_id)
                path = get_workspace_path(w_id)
                pids.append(pid)
                pid_to_name[pid] = (path or w_id).rstrip("/").split("/")[-1]
                pid_to_path[pid] = path

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
                                  AND (
                                      NOT %(impl_intent)s
                                      OR (
                                          lower(file_path) NOT LIKE 'node-types/%%'
                                          AND lower(file_path) NOT LIKE '%%/node-types/%%'
                                          AND lower(file_path) NOT LIKE 'grammars/%%'
                                          AND lower(file_path) NOT LIKE '%%/grammars/%%'
                                          AND lower(file_path) NOT LIKE '%%-grammar.json'
                                          AND lower(file_path) NOT LIKE '%%_grammar.json'
                                          AND lower(file_path) NOT LIKE '%%/grammar.json'
                                      )
                                  )
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
                                  AND (
                                      NOT %(impl_intent)s
                                      OR (
                                          lower(file_path) NOT LIKE 'node-types/%%'
                                          AND lower(file_path) NOT LIKE '%%/node-types/%%'
                                          AND lower(file_path) NOT LIKE 'grammars/%%'
                                          AND lower(file_path) NOT LIKE '%%/grammars/%%'
                                          AND lower(file_path) NOT LIKE '%%-grammar.json'
                                          AND lower(file_path) NOT LIKE '%%_grammar.json'
                                          AND lower(file_path) NOT LIKE '%%/grammar.json'
                                      )
                                  )
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
                            {
                                "vec": vec_str,
                                "pid": pid,
                                "qt": query,
                                "fetch": fetch,
                                "impl_intent": impl_intent,
                            },
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

            all_results: list[dict] = []
            batch = await asyncio.gather(*[_search_project(pid) for pid in pid_to_name])
            for chunk in batch:
                all_results.extend(chunk)

            if not all_results:
                if impl_intent and (path_hints or explicit_runtime_entrypoints):
                    rescue_results: list[dict] = []
                    if explicit_runtime_entrypoints:
                        for pid in pid_to_name:
                            async with memory_store._pg_pool.connection() as conn:
                                await conn.execute("BEGIN")
                                exact_rows = await _load_rescue_rows(
                                    conn,
                                    pid=pid,
                                    file_paths=explicit_runtime_entrypoints,
                                    member_exprs=member_exprs,
                                )
                            for r in exact_rows:
                                r_meta = sem_helpers.coerce_meta(r)
                                r["_meta"] = r_meta
                                r["meta_score"] = sem_helpers.meta_score(r_meta)
                                r["doc_like"] = sem_helpers.is_doc_like_path(r.get("file_path"))
                                r["low_signal_parser_data"] = sem_helpers.is_low_signal_parser_data_path(
                                    r.get("file_path")
                                )
                                r["low_signal_binding_surface"] = sem_helpers.is_low_signal_binding_surface_path(
                                    r.get("file_path")
                                )
                                sem_helpers.enrich_implementation_result(
                                    r,
                                    query=query,
                                    query_class=impl_query_class,
                                    base_score=float(r.get("rrf", 0.0) or 0.0),
                                    meta_boost=0.0,
                                    base_bonus=0.22,
                                )
                            rescue_results.extend(exact_rows)
                    if path_hints:
                        for pid in pid_to_name:
                            async with memory_store._pg_pool.connection() as conn:
                                await conn.execute("BEGIN")
                                rescue_rows = await _load_path_hint_rows(
                                    conn,
                                    pid=pid,
                                    path_hints=path_hints,
                                    identifier_exprs=exact_identifiers,
                                    max_files=min(max(fallback_max, 8), 16),
                                )
                            for r in rescue_rows:
                                r_meta = sem_helpers.coerce_meta(r)
                                r["_meta"] = r_meta
                                r["meta_score"] = sem_helpers.meta_score(r_meta)
                                r["doc_like"] = sem_helpers.is_doc_like_path(r.get("file_path"))
                                r["low_signal_parser_data"] = sem_helpers.is_low_signal_parser_data_path(
                                    r.get("file_path")
                                )
                                r["low_signal_binding_surface"] = sem_helpers.is_low_signal_binding_surface_path(
                                    r.get("file_path")
                                )
                                sem_helpers.enrich_implementation_result(
                                    r,
                                    query=query,
                                    query_class=impl_query_class,
                                    base_score=float(r.get("rrf", 0.0) or 0.0),
                                    meta_boost=0.0,
                                    base_bonus=0.14 if int(r.get("implementation_exact_identifier_text_hit", 0) or 0) > 0 else 0.025,
                                )
                            rescue_results.extend(rescue_rows)
                    if rescue_results:
                        all_results = rescue_results
                        all_results.sort(key=sem_helpers.implementation_rank_tuple)

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

            if impl_intent:
                exclude_paths = (exclude_paths or []) + sem_helpers.implementation_noise_exclude_patterns(query)

            if (
                impl_intent
                and sem_helpers.implementation_query_prefers_provider_wiring(query)
                and not include_paths
            ):
                provider_tokens = sorted(sem_helpers.implementation_provider_query_tokens(query))
                if provider_tokens:
                    include_paths = [
                        "providers/__init__.py",
                        "*/providers/__init__.py",
                    ]
                    for token in provider_tokens:
                        include_paths.extend(
                            [
                                f"providers/{token}.py",
                                f"*/providers/{token}.py",
                            ]
                        )
                else:
                    include_paths = ["providers/*", "*/providers/*"]
            if mode == "broad":
                if max_per_dir == 2:
                    max_per_dir = 4
                if meta_boost == 0.005:
                    meta_boost = 0.0
                if fallback == "none":
                    fallback = "grep"
                if max_per_file == 0:
                    max_per_file = 2

            if impl_intent and sem_helpers.implementation_query_relaxes_dir_cap(query):
                max_per_dir = 0

            filters_active = any(
                [
                    languages,
                    min_imports > 0,
                    min_symbols > 0,
                    require_diagnostics,
                    require_context,
                    crate_contains,
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

            cargo_rows_by_pid: dict[str, list[dict]] = {}
            if all_results and (crate_contains or include_metadata):
                try:
                    import graph_bootstrap

                    driver = await graph_bootstrap.require_driver()
                    cargo_rows_by_pid = await _load_cargo_crate_rows(
                        driver, graph_bootstrap._NEO4J_DB, list(pid_to_name.keys())
                    )
                except Exception:
                    cargo_rows_by_pid = {}
                if cargo_rows_by_pid:
                    grouped: dict[str, list[dict]] = {}
                    for result in all_results:
                        pid = result.get("project_id")
                        if pid:
                            grouped.setdefault(pid, []).append(result)
                    for pid, rows in grouped.items():
                        sem_helpers.attach_cargo_crate_meta(rows, cargo_rows_by_pid.get(pid) or [])

            if include_metadata:
                for r in all_results:
                    r_meta = sem_helpers.coerce_meta(r)
                    r["_meta"] = r_meta
                    r["meta_score"] = sem_helpers.meta_score(r_meta)
                if filters_active:
                    all_results = [
                        r
                        for r in all_results
                        if sem_helpers.passes_filters(
                            r.get("_meta", {}),
                            languages=languages,
                            min_imports=min_imports,
                            min_symbols=min_symbols,
                            require_diagnostics=require_diagnostics,
                            require_context=require_context,
                        )
                        and sem_helpers.path_allowed(
                            r.get("file_path", ""),
                            include_paths=include_paths,
                            exclude_paths=exclude_paths,
                        )
                    ]
                if crate_contains:
                    all_results = sem_helpers.filter_by_cargo_crate(all_results, crate_contains)
                for r in all_results:
                    base_score = r.get("rrf", 0.0)
                    try:
                        base_score = float(base_score)
                    except (TypeError, ValueError):
                        base_score = 0.0
                    if impl_intent:
                        sem_helpers.enrich_implementation_result(
                            r,
                            query=query,
                            query_class=impl_query_class,
                            base_score=base_score,
                            meta_boost=meta_boost,
                        )
                    else:
                        is_doc_like = sem_helpers.is_doc_like_path(r.get("file_path"))
                        is_low_signal_parser_data = sem_helpers.is_low_signal_parser_data_path(
                            r.get("file_path")
                        )
                        is_low_signal_binding_surface = sem_helpers.is_low_signal_binding_surface_path(
                            r.get("file_path")
                        )
                        doc_penalty = 0.05 if is_doc_like else 0.0
                        parser_data_penalty = 0.08 if is_low_signal_parser_data else 0.0
                        binding_surface_penalty = 0.06 if is_low_signal_binding_surface else 0.0
                        r["doc_like"] = is_doc_like
                        r["low_signal_parser_data"] = is_low_signal_parser_data
                        r["low_signal_binding_surface"] = is_low_signal_binding_surface
                        r["rank_score"] = (
                            base_score
                            + (r.get("meta_score", 0) * meta_boost if meta_boost > 0 else 0.0)
                            - doc_penalty
                            - parser_data_penalty
                            - binding_surface_penalty
                        )
                all_results.sort(key=sem_helpers.implementation_rank_tuple)
            else:
                if impl_intent:
                    for r in all_results:
                        r["doc_like"] = sem_helpers.is_doc_like_path(r.get("file_path"))
                        r["low_signal_parser_data"] = sem_helpers.is_low_signal_parser_data_path(
                            r.get("file_path")
                        )
                    all_results.sort(key=sem_helpers.implementation_rank_tuple)
                else:
                    all_results.sort(key=lambda r: r["rrf"], reverse=True)

            if impl_intent:
                code_results = [
                    r
                    for r in all_results
                    if not r.get("doc_like")
                    and not r.get("low_signal_parser_data")
                    and not r.get("low_signal_binding_surface")
                ]
                parser_results = [
                    r for r in all_results if r.get("low_signal_parser_data") and not r.get("doc_like")
                ]
                binding_results = [
                    r
                    for r in all_results
                    if r.get("low_signal_binding_surface") and not r.get("doc_like")
                ]
                doc_results = [r for r in all_results if r.get("doc_like")]
                if code_results:
                    all_results = code_results
                elif parser_results:
                    all_results = parser_results
                elif binding_results:
                    all_results = binding_results
                else:
                    all_results = doc_results

            if impl_intent:
                non_parser_candidates = [
                    r for r in all_results if not sem_helpers.is_low_signal_parser_data_path(r.get("file_path"))
                ]
                if non_parser_candidates:
                    all_results = non_parser_candidates

            if impl_intent and path_hints and all_results:
                top_probe = all_results[: min(5, len(all_results))]
                has_definition_hit = any(
                    int(r.get("implementation_definition_hit", 0) or 0) > 0
                    or int(r.get("implementation_api_entrypoint_hit", 0) or 0) > 0
                    for r in top_probe
                )
                has_path_hint_hit = any(
                    sem_helpers.implementation_path_hint_hit(
                        r.get("file_path"),
                        path_hints=path_hints,
                    )
                    > 0
                    for r in top_probe
                )
                has_runtime_entrypoint_hit = any(
                    int(r.get("implementation_runtime_main_entrypoint_hit", 0) or 0) > 0 for r in top_probe
                )
                rescue_results: list[dict] = []
                if explicit_runtime_entrypoints and not has_runtime_entrypoint_hit:
                    for pid in pid_to_name:
                        async with memory_store._pg_pool.connection() as conn:
                            await conn.execute("BEGIN")
                            exact_rows = await _load_rescue_rows(
                                conn,
                                pid=pid,
                                file_paths=explicit_runtime_entrypoints,
                                member_exprs=member_exprs,
                            )
                        for r in exact_rows:
                            r_meta = sem_helpers.coerce_meta(r)
                            r["_meta"] = r_meta
                            r["meta_score"] = sem_helpers.meta_score(r_meta)
                            r["doc_like"] = sem_helpers.is_doc_like_path(r.get("file_path"))
                            r["low_signal_parser_data"] = sem_helpers.is_low_signal_parser_data_path(
                                r.get("file_path")
                            )
                            r["low_signal_binding_surface"] = sem_helpers.is_low_signal_binding_surface_path(
                                r.get("file_path")
                            )
                            sem_helpers.enrich_implementation_result(
                                r,
                                query=query,
                                query_class=impl_query_class,
                                base_score=float(r.get("rrf", 0.0) or 0.0),
                                meta_boost=0.0,
                                base_bonus=0.22,
                            )
                        rescue_results.extend(exact_rows)
                if not has_path_hint_hit:
                    for pid in pid_to_name:
                        async with memory_store._pg_pool.connection() as conn:
                            await conn.execute("BEGIN")
                            rescue_rows = await _load_path_hint_rows(
                                conn,
                                pid=pid,
                                path_hints=path_hints,
                                identifier_exprs=exact_identifiers,
                                max_files=min(max(fallback_max, 8), 16),
                            )
                        for r in rescue_rows:
                            r_meta = sem_helpers.coerce_meta(r)
                            r["_meta"] = r_meta
                            r["meta_score"] = sem_helpers.meta_score(r_meta)
                            is_doc_like = sem_helpers.is_doc_like_path(r.get("file_path"))
                            is_low_signal_parser_data = sem_helpers.is_low_signal_parser_data_path(
                                r.get("file_path")
                            )
                            is_low_signal_binding_surface = sem_helpers.is_low_signal_binding_surface_path(
                                r.get("file_path")
                            )
                            r["doc_like"] = is_doc_like
                            r["low_signal_parser_data"] = is_low_signal_parser_data
                            r["low_signal_binding_surface"] = is_low_signal_binding_surface
                            sem_helpers.enrich_implementation_result(
                                r,
                                query=query,
                                query_class=impl_query_class,
                                base_score=float(r.get("rrf", 0.0) or 0.0),
                                meta_boost=0.0,
                                base_bonus=0.14 if int(r.get("implementation_exact_identifier_text_hit", 0) or 0) > 0 else 0.025,
                            )
                        rescue_results.extend(rescue_rows)
                if rescue_results:
                    existing_keys = {
                        (r.get("project_id"), r.get("file_path"), r.get("chunk_index"))
                        for r in all_results
                    }
                    for r in rescue_results:
                        key = (r.get("project_id"), r.get("file_path"), r.get("chunk_index"))
                        if key not in existing_keys:
                            all_results.append(r)
                    all_results.sort(key=sem_helpers.implementation_rank_tuple)
                    top_probe = all_results[: min(5, len(all_results))]
                    has_definition_hit = any(
                        int(r.get("implementation_definition_hit", 0) or 0) > 0
                        or int(r.get("implementation_api_entrypoint_hit", 0) or 0) > 0
                        for r in top_probe
                    )
                if sem_helpers.query_class_prefers_definitions(impl_query_class) and not has_definition_hit:
                    rescue_results: list[dict] = []
                    for pid, proj_name in pid_to_name.items():
                        proj_root = pid_to_path.get(pid)
                        if not proj_root:
                            continue
                        matches, _dbg = await search_fallbacks.run_definition_fallback_grep(
                            proj_root,
                            query,
                            fallback_glob,
                            min(fallback_max, 8),
                        )
                        if not matches:
                            continue
                        async with memory_store._pg_pool.connection() as conn:
                            await conn.execute("BEGIN")
                            rescue_rows = await _load_rescue_rows(
                                conn,
                                pid=pid,
                                file_paths=matches,
                                member_exprs=member_exprs,
                            )
                        for r in rescue_rows:
                            r_meta = sem_helpers.coerce_meta(r)
                            r["_meta"] = r_meta
                            r["meta_score"] = sem_helpers.meta_score(r_meta)
                            is_doc_like = sem_helpers.is_doc_like_path(r.get("file_path"))
                            is_low_signal_parser_data = sem_helpers.is_low_signal_parser_data_path(
                                r.get("file_path")
                            )
                            is_low_signal_binding_surface = sem_helpers.is_low_signal_binding_surface_path(
                                r.get("file_path")
                            )
                            r["doc_like"] = is_doc_like
                            r["low_signal_parser_data"] = is_low_signal_parser_data
                            r["low_signal_binding_surface"] = is_low_signal_binding_surface
                            sem_helpers.enrich_implementation_result(
                                r,
                                query=query,
                                query_class=impl_query_class,
                                base_score=float(r.get("rrf", 0.0) or 0.0),
                                meta_boost=0.0,
                                base_bonus=0.02,
                            )
                        rescue_results.extend(rescue_rows)
                    if rescue_results:
                        existing_keys = {
                            (r.get("project_id"), r.get("file_path"), r.get("chunk_index"))
                            for r in all_results
                        }
                        for r in rescue_results:
                            key = (r.get("project_id"), r.get("file_path"), r.get("chunk_index"))
                            if key not in existing_keys:
                                all_results.append(r)
                        all_results.sort(key=sem_helpers.implementation_rank_tuple)

            if impl_intent and sem_helpers.query_class_prefers_usage(impl_query_class) and all_results:
                top_probe = all_results[: min(5, len(all_results))]
                has_usage_site_member_hit = any(
                    sem_helpers.implementation_exact_member_usage_site_hit(r)
                    for r in top_probe
                )
                if member_exprs and not has_usage_site_member_hit:
                    rescue_results: list[dict] = []
                    for pid, proj_name in pid_to_name.items():
                        proj_root = pid_to_path.get(pid)
                        if not proj_root:
                            continue
                        matches, _dbg = await search_fallbacks.run_member_usage_fallback_grep(
                            proj_root,
                            member_exprs,
                            fallback_glob,
                            min(max(fallback_max, 32), 64),
                        )
                        if not matches:
                            continue
                        async with memory_store._pg_pool.connection() as conn:
                            await conn.execute("BEGIN")
                            rescue_rows = await _load_rescue_rows(
                                conn,
                                pid=pid,
                                file_paths=matches,
                                member_exprs=member_exprs,
                            )
                        for r in rescue_rows:
                            r_meta = sem_helpers.coerce_meta(r)
                            r["_meta"] = r_meta
                            r["meta_score"] = sem_helpers.meta_score(r_meta)
                            r["doc_like"] = sem_helpers.is_doc_like_path(r.get("file_path"))
                            r["low_signal_parser_data"] = sem_helpers.is_low_signal_parser_data_path(
                                r.get("file_path")
                            )
                            r["low_signal_binding_surface"] = sem_helpers.is_low_signal_binding_surface_path(
                                r.get("file_path")
                            )
                            sem_helpers.enrich_implementation_result(
                                r,
                                query=query,
                                query_class=impl_query_class,
                                base_score=float(r.get("rrf", 0.0) or 0.0),
                                meta_boost=0.0,
                                base_bonus=0.03,
                            )
                        rescue_results.extend(rescue_rows)
                    if rescue_results:
                        existing_keys = {
                            (r.get("project_id"), r.get("file_path"), r.get("chunk_index"))
                            for r in all_results
                        }
                        for r in rescue_results:
                            key = (r.get("project_id"), r.get("file_path"), r.get("chunk_index"))
                            if key not in existing_keys:
                                all_results.append(r)
                        all_results.sort(key=sem_helpers.implementation_rank_tuple)
                exact_member_hits = [
                    r for r in all_results if int(r.get("implementation_exact_member_usage_hit", 0) or 0) > 0
                ]
                if member_exprs and exact_member_hits:
                    exact_usage_site_hits = [
                        r for r in exact_member_hits if sem_helpers.implementation_exact_member_usage_site_hit(r)
                    ]
                    exact_non_site_hits = [
                        r for r in exact_member_hits if not sem_helpers.implementation_exact_member_usage_site_hit(r)
                    ]
                    prefer_tests = sem_helpers.usage_query_prefers_test_results(query)
                    prefer_examples = sem_helpers.usage_query_prefers_example_results(query)
                    if not prefer_tests:
                        example_hits = []
                        test_hits = []
                        other_hits = []
                        for r in exact_usage_site_hits:
                            chunk_role = sem_helpers.implementation_chunk_role(
                                sem_helpers.coerce_meta(r),
                                r.get("file_path"),
                            )
                            if chunk_role == "example_usage":
                                example_hits.append(r)
                            elif chunk_role == "test_usage":
                                test_hits.append(r)
                            else:
                                other_hits.append(r)
                        example_hits.sort(key=sem_helpers.implementation_rank_tuple)
                        other_hits.sort(key=sem_helpers.implementation_rank_tuple)
                        test_hits.sort(key=sem_helpers.implementation_rank_tuple)
                        if prefer_examples:
                            exact_usage_site_hits = example_hits + test_hits + other_hits
                        else:
                            exact_usage_site_hits = example_hits + other_hits + test_hits
                    else:
                        exact_usage_site_hits.sort(key=sem_helpers.implementation_rank_tuple)
                    exact_non_site_hits.sort(key=sem_helpers.implementation_rank_tuple)
                    non_exact_hits = [
                        r
                        for r in all_results
                        if int(r.get("implementation_exact_member_usage_hit", 0) or 0) <= 0
                    ]
                    non_exact_hits.sort(key=sem_helpers.implementation_rank_tuple)
                    all_results = exact_usage_site_hits + exact_non_site_hits + non_exact_hits

            if include_metadata and all_results and (include_paths or exclude_paths):
                all_results = [
                    r
                    for r in all_results
                    if sem_helpers.path_allowed(
                        r.get("file_path", ""),
                        include_paths=include_paths,
                        exclude_paths=exclude_paths,
                    )
                ]

            if clone_dedup:
                try:
                    import graph_bootstrap

                    driver = await graph_bootstrap.require_driver()
                    if driver:
                        for r in all_results:
                            meta = r.get("_meta")
                            if not isinstance(meta, dict):
                                meta = sem_helpers.coerce_meta(r)

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

            duplicate_trace_enabled = include_debug or os.getenv(
                "LM_PROXY_DUPLICATE_TRACE", "0"
            ).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            duplicate_telemetry_enabled = sem_helpers.duplicate_telemetry_enabled()
            duplicate_experiments = sem_helpers.duplicate_experiment_flags_with_query_class(
                "code",
                impl_query_class if impl_intent else None,
            )
            duplicate_trace: dict | None = None

            if dedupe_files:
                if duplicate_trace_enabled or duplicate_telemetry_enabled or any(duplicate_experiments.values()):
                    duplicate_trace = sem_helpers.rerank_retrieval_results_contract(
                        all_results,
                        query=query,
                        mode="code",
                        experiments=duplicate_experiments,
                        include_debug=include_debug or duplicate_trace_enabled,
                    )
                    reranked = duplicate_trace.get("results") if isinstance(duplicate_trace, dict) else None
                    if isinstance(reranked, list) and reranked:
                        all_results = reranked
                if impl_intent:
                    all_results.sort(key=sem_helpers.implementation_rank_tuple)
                all_results = sem_helpers.dedupe_files(all_results)

            if impl_intent:
                all_results.sort(key=sem_helpers.implementation_rank_tuple)
                if (
                    sem_helpers.implementation_query_prefers_dispatchers(query)
                    and "profile" not in sem_helpers.implementation_query_symbols(query)
                    and "profile" not in sem_helpers.implementation_query_exact_identifiers(query)
                ):
                    non_profile_results: list[dict] = []
                    for r in all_results:
                        meta = sem_helpers.coerce_meta(r)
                        file_roles = sem_helpers.implementation_file_roles(meta)
                        chunk_role = sem_helpers.implementation_chunk_role(meta, r.get("file_path"))
                        norm = (r.get("file_path") or "").replace("\\", "/").lower()
                        if (
                            "/profiles/" in norm
                            or "profile_surface" in file_roles
                            or chunk_role == "profile_definition"
                        ):
                            continue
                        non_profile_results.append(r)
                    if non_profile_results:
                        all_results = non_profile_results
                if sem_helpers.implementation_query_prefers_dispatchers(query) and path_hints:
                    exact_dispatcher_identifiers = sem_helpers.implementation_query_exact_identifiers(query)
                    top_probe = all_results[: min(3, len(all_results))]
                    has_strong_dispatcher = any(
                        int(r.get("implementation_dispatcher_priority", 0) or 0) >= 5
                        and (
                            {
                                str(symbol).strip().lower()
                                for symbol in (sem_helpers.coerce_meta(r).get("declared_symbols") or [])
                                if str(symbol).strip()
                            }
                            & exact_dispatcher_identifiers
                        )
                        for r in top_probe
                    )
                    if not has_strong_dispatcher:
                        rescue_results: list[dict] = []
                        for pid in pid_to_name:
                            async with memory_store._pg_pool.connection() as conn:
                                await conn.execute("BEGIN")
                                rescue_rows = await _load_path_hint_rows(
                                    conn,
                                    pid=pid,
                                    path_hints=path_hints,
                                    identifier_exprs=exact_identifiers,
                                    max_files=min(max(fallback_max, 8), 16),
                                )
                            for r in rescue_rows:
                                r_meta = sem_helpers.coerce_meta(r)
                                r["_meta"] = r_meta
                                r["meta_score"] = sem_helpers.meta_score(r_meta)
                                r["doc_like"] = sem_helpers.is_doc_like_path(r.get("file_path"))
                                r["low_signal_parser_data"] = sem_helpers.is_low_signal_parser_data_path(
                                    r.get("file_path")
                                )
                                r["low_signal_binding_surface"] = sem_helpers.is_low_signal_binding_surface_path(
                                    r.get("file_path")
                                )
                                sem_helpers.enrich_implementation_result(
                                    r,
                                    query=query,
                                    query_class=impl_query_class,
                                    base_score=float(r.get("rrf", 0.0) or 0.0),
                                    meta_boost=0.0,
                                    base_bonus=0.14
                                    if int(r.get("implementation_exact_identifier_text_hit", 0) or 0) > 0
                                    else 0.025,
                                )
                                declared_symbols = {
                                    str(symbol).strip().lower()
                                    for symbol in (r_meta.get("declared_symbols") or [])
                                    if str(symbol).strip()
                                }
                                if (
                                    int(r.get("implementation_dispatcher_priority", 0) or 0) < 5
                                    or not (declared_symbols & exact_dispatcher_identifiers)
                                ):
                                    continue
                                rescue_results.append(r)
                        if rescue_results:
                            replacement_files = {
                                (r.get("project_id"), r.get("file_path"))
                                for r in rescue_results
                            }
                            all_results = [
                                r
                                for r in all_results
                                if (r.get("project_id"), r.get("file_path")) not in replacement_files
                            ]
                            for r in rescue_results:
                                all_results.append(r)
                            all_results.sort(key=sem_helpers.implementation_rank_tuple)
                    strong_dispatchers: list[dict] = []
                    other_results: list[dict] = []
                    for r in all_results:
                        meta = sem_helpers.coerce_meta(r)
                        file_roles = sem_helpers.implementation_file_roles(meta)
                        chunk_role = sem_helpers.implementation_chunk_role(meta, r.get("file_path"))
                        declared_symbols = {
                            str(symbol).strip().lower()
                            for symbol in (meta.get("declared_symbols") or [])
                            if str(symbol).strip()
                        }
                        if (
                            int(r.get("implementation_dispatcher_priority", 0) or 0) >= 5
                            and (declared_symbols & exact_dispatcher_identifiers)
                            and "profile_surface" not in file_roles
                            and chunk_role != "profile_definition"
                        ):
                            strong_dispatchers.append(r)
                        else:
                            other_results.append(r)
                    if strong_dispatchers:
                        strong_dispatchers.sort(key=sem_helpers.implementation_rank_tuple)
                        other_results.sort(key=sem_helpers.implementation_rank_tuple)
                        all_results = strong_dispatchers + other_results
                if sem_helpers.implementation_query_prefers_request_routing(query) and path_hints:
                    top_probe = all_results[: min(3, len(all_results))]
                    has_strong_routing_surface = any(
                        int(r.get("implementation_controller_entity_hit", 0) or 0) >= 2
                        or int(r.get("implementation_request_handler_priority", 0) or 0) >= 2
                        or int(r.get("implementation_routing_priority", 0) or 0) >= 2
                        for r in top_probe
                    )
                    if not has_strong_routing_surface:
                        routing_rescue_results: list[dict] = []
                        for pid in pid_to_name:
                            async with memory_store._pg_pool.connection() as conn:
                                await conn.execute("BEGIN")
                                rescue_rows = await _load_path_hint_rows(
                                    conn,
                                    pid=pid,
                                    path_hints=path_hints,
                                    identifier_exprs=exact_identifiers,
                                    max_files=min(max(fallback_max, 8), 16),
                                )
                            for r in rescue_rows:
                                r_meta = sem_helpers.coerce_meta(r)
                                r["_meta"] = r_meta
                                r["meta_score"] = sem_helpers.meta_score(r_meta)
                                r["doc_like"] = sem_helpers.is_doc_like_path(r.get("file_path"))
                                r["low_signal_parser_data"] = sem_helpers.is_low_signal_parser_data_path(
                                    r.get("file_path")
                                )
                                r["low_signal_binding_surface"] = sem_helpers.is_low_signal_binding_surface_path(
                                    r.get("file_path")
                                )
                                sem_helpers.enrich_implementation_result(
                                    r,
                                    query=query,
                                    query_class=impl_query_class,
                                    base_score=float(r.get("rrf", 0.0) or 0.0),
                                    meta_boost=0.0,
                                    base_bonus=0.12
                                    if int(r.get("implementation_path_hint_hit", 0) or 0) > 0
                                    else 0.02,
                                )
                                if (
                                    int(r.get("implementation_controller_entity_hit", 0) or 0) < 2
                                    and int(r.get("implementation_request_handler_priority", 0) or 0) < 2
                                    and int(r.get("implementation_routing_priority", 0) or 0) < 2
                                ):
                                    continue
                                routing_rescue_results.append(r)
                        if routing_rescue_results:
                            replacement_files = {
                                (r.get("project_id"), r.get("file_path"))
                                for r in routing_rescue_results
                            }
                            all_results = [
                                r
                                for r in all_results
                                if (r.get("project_id"), r.get("file_path")) not in replacement_files
                            ]
                            all_results.extend(routing_rescue_results)
                            all_results.sort(key=sem_helpers.implementation_rank_tuple)
                    strong_routing_results: list[dict] = []
                    other_results: list[dict] = []
                    for r in all_results:
                        meta = sem_helpers.coerce_meta(r)
                        file_roles = sem_helpers.implementation_file_roles(meta)
                        if (
                            "controller_surface" in file_roles
                            and (
                                int(r.get("implementation_controller_entity_hit", 0) or 0) >= 2
                                or int(r.get("implementation_request_handler_priority", 0) or 0) >= 2
                                or int(r.get("implementation_routing_priority", 0) or 0) >= 2
                            )
                        ):
                            strong_routing_results.append(r)
                        else:
                            other_results.append(r)
                    if strong_routing_results:
                        strong_routing_results.sort(key=sem_helpers.implementation_rank_tuple)
                        other_results.sort(key=sem_helpers.implementation_rank_tuple)
                        all_results = strong_routing_results + other_results

            all_results = sem_helpers.cap_per_file(all_results, max_per_file)
            all_results = sem_helpers.cap_per_dir(all_results, max_per_dir)
            top = all_results[:k]

            lines = sem_helpers.render_results(
                all_results,
                query=query,
                k=k,
                multi=multi,
                pid_to_name=pid_to_name,
                include_metadata=include_metadata,
            )

            if duplicate_trace and (include_debug or duplicate_trace_enabled):
                lines.extend(sem_helpers.summarize_trace_for_debug(duplicate_trace))
                lines.append("")
            if duplicate_trace and duplicate_telemetry_enabled:
                telemetry = duplicate_trace.get("telemetry") if isinstance(duplicate_trace, dict) else {}
                if isinstance(telemetry, dict):
                    sem_helpers.append_duplicate_telemetry_event(
                        duplicate_trace,
                        query=query,
                        tool="search_codebase",
                        mode="code",
                    )
                    debug_log(
                        "duplicate_rerank_telemetry",
                        query=query[:200],
                        mode=telemetry.get("mode"),
                        query_class=telemetry.get("query_class"),
                        exact_suppressions=telemetry.get("exact_suppressions"),
                        experimental_suppressions=telemetry.get("experimental_suppressions"),
                        relation_counts=telemetry.get("relation_counts"),
                        group_sizes=telemetry.get("group_sizes"),
                        topk_redundancy_before=telemetry.get("topk_redundancy_before"),
                        topk_redundancy_after=telemetry.get("topk_redundancy_after"),
                        kept_group_multi_member_count=telemetry.get("kept_group_multi_member_count"),
                        multi_representative_group_count=telemetry.get("multi_representative_group_count"),
                        query_distinct_multi_rep_count=telemetry.get("query_distinct_multi_rep_count"),
                        canonical_doc_preference_success=telemetry.get("canonical_doc_preference_success"),
                        version_sensitive_query=telemetry.get("version_sensitive_query"),
                        best_answer_loss_suspect=telemetry.get("best_answer_loss_suspect"),
                        regression_alerts=telemetry.get("regression_alerts"),
                        experiments=duplicate_trace.get("experiments"),
                    )

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

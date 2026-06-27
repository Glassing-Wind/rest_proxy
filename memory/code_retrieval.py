"""memory/code_retrieval.py — core codebase search retrieval and ranking policy."""

from __future__ import annotations

import os
import asyncio
from typing import Any

from neo4j import unit_of_work

from _helpers import get_project_id, WorkspaceRegistry, get_workspace_path
from memory import retrieval_fallbacks as search_fallbacks
from memory import retrieval_duplicates
from memory import retrieval_metadata
from memory import retrieval_policy as sem_helpers
from memory import retrieval_telemetry
from runtime_logging import debug_log


async def _execute_graph_read(session, cypher: str, op: str, **params):
    timeout = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
    op_prefix = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
    metadata = {
        "source": "lm_proxy",
        "tool": "code_search",
        "op": f"{op_prefix}.{op}" if op_prefix else op,
    }

    @unit_of_work(timeout=timeout, metadata=metadata)
    async def _tx(tx):
        result = await tx.run(cypher, **params)
        return await result.data()

    if hasattr(session, "execute_read"):
        return await session.execute_read(_tx)
    return await _tx(session)


async def _load_cargo_crate_rows(driver, neo4j_db: str, project_ids: list[str]) -> dict[str, list[dict]]:
    if not driver or not project_ids:
        return {}
    project_ids = [pid for pid in project_ids if pid]
    if not project_ids:
        return {}
    async with driver.session(database=neo4j_db) as session:
        schema_rows = await _execute_graph_read(
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
            rows = await _execute_graph_read(
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
            "implementation_path_hint_hit": int((r[5] if len(r) > 5 else 0) or 0),
            "implementation_exact_identifier_text_hit": int((r[6] if len(r) > 6 else 0) or 0),
            "rrf": 0.0,
            "_definition_rescue": True,
        }
        for r in rows
    ]


async def search_codebase_core(
    workspace_ids: list[str],
    query: str,
    query_vector: list[float],
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
    languages: list[str] | None = None,
    min_imports: int = 0,
    min_symbols: int = 0,
    require_diagnostics: bool = False,
    require_context: bool = False,
    crate_contains: str | None = None,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> dict[str, Any]:
    """
    Perform core hybrid semantic + full-text search on codebase_embeddings table.
    Handles precise/broad search modes, path-hint definition rescue, filtering by scope,
    winnowing duplicate-aware reranking, and exact fallback grep.
    """
    import memory.store as memory_store
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

    vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
    fetch = min(k * 10, 150)

    async def _search_project(pid: str) -> list[dict]:
        async with memory_store._pg_pool.connection() as conn:
            await conn.execute("BEGIN")
            async with conn.cursor() as cur:
                await cur.execute("SET LOCAL hnsw.ef_search = 100")
                await cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
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
                        r_meta = retrieval_metadata.coerce_meta(r)
                        r["_meta"] = r_meta
                        r["meta_score"] = retrieval_metadata.meta_score(r_meta)
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
                        r_meta = retrieval_metadata.coerce_meta(r)
                        r["_meta"] = r_meta
                        r["meta_score"] = retrieval_metadata.meta_score(r_meta)
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
        return {
            "results": [],
            "all_results": [],
            "pid_to_name": pid_to_name,
            "pid_to_path": pid_to_path,
            "multi": multi,
            "duplicate_trace": None,
            "fallback_lines": [],
        }

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
                retrieval_metadata.attach_cargo_crate_meta(rows, cargo_rows_by_pid.get(pid) or [])

    if include_metadata:
        for r in all_results:
            r_meta = retrieval_metadata.coerce_meta(r)
            r["_meta"] = r_meta
            r["meta_score"] = retrieval_metadata.meta_score(r_meta)
        if filters_active:
            all_results = [
                r
                for r in all_results
                if retrieval_metadata.passes_filters(
                    r.get("_meta", {}),
                    languages=languages,
                    min_imports=min_imports,
                    min_symbols=min_symbols,
                    require_diagnostics=require_diagnostics,
                    require_context=require_context,
                )
                and retrieval_metadata.path_allowed(
                    r.get("file_path", ""),
                    include_paths=include_paths,
                    exclude_paths=exclude_paths,
                )
            ]
        if crate_contains:
            all_results = retrieval_metadata.filter_by_cargo_crate(all_results, crate_contains)
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
                sem_helpers.apply_result_surface_flags(r)
                is_doc_like = r["doc_like"]
                is_low_signal_parser_data = r["low_signal_parser_data"]
                is_low_signal_binding_surface = r["low_signal_binding_surface"]
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
                sem_helpers.apply_result_surface_flags(r)
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
            r for r in all_results if not r.get("low_signal_parser_data")
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
                    r_meta = retrieval_metadata.coerce_meta(r)
                    r["_meta"] = r_meta
                    r["meta_score"] = retrieval_metadata.meta_score(r_meta)
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
                    r_meta = retrieval_metadata.coerce_meta(r)
                    r["_meta"] = r_meta
                    r["meta_score"] = retrieval_metadata.meta_score(r_meta)
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
                    r_meta = retrieval_metadata.coerce_meta(r)
                    r["_meta"] = r_meta
                    r["meta_score"] = retrieval_metadata.meta_score(r_meta)
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
                    r_meta = retrieval_metadata.coerce_meta(r)
                    r["_meta"] = r_meta
                    r["meta_score"] = retrieval_metadata.meta_score(r_meta)
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
                        retrieval_metadata.coerce_meta(r),
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
            if retrieval_metadata.path_allowed(
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
                        meta = retrieval_metadata.coerce_meta(r)

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
                        records = await _execute_graph_read(
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
                            file_records = await _execute_graph_read(
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
                            func_records = await _execute_graph_read(
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
                            "project_id": pids[0] if pids else "",
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
    duplicate_telemetry_enabled = retrieval_telemetry.duplicate_telemetry_enabled()
    duplicate_experiments = retrieval_telemetry.duplicate_experiment_flags_with_query_class(
        "code",
        impl_query_class if impl_intent else None,
    )
    duplicate_trace: dict | None = None

    if dedupe_files:
        pre_duplicate_results = list(all_results)
        if duplicate_trace_enabled or duplicate_telemetry_enabled or any(duplicate_experiments.values()):
            duplicate_trace = retrieval_duplicates.rerank_retrieval_results_contract(
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
        pre_dispatcher_recovery_results = list(all_results)
    else:
        pre_duplicate_results = list(all_results)
        pre_dispatcher_recovery_results = list(all_results)

    if impl_intent:
        all_results.sort(key=sem_helpers.implementation_rank_tuple)
        dispatcher_rescue_applied = False
        if (
            sem_helpers.implementation_query_prefers_dispatchers(query)
            and "profile" not in sem_helpers.implementation_query_symbols(query)
            and "profile" not in sem_helpers.implementation_query_exact_identifiers(query)
        ):
            non_profile_results: list[dict] = []
            for r in all_results:
                meta = retrieval_metadata.coerce_meta(r)
                if sem_helpers.implementation_is_profile_candidate(r.get("file_path"), meta):
                    continue
                non_profile_results.append(r)
            if non_profile_results:
                all_results = non_profile_results
        if sem_helpers.implementation_query_prefers_dispatchers(query) and path_hints:
            pass
        dispatcher_contract_trace = sem_helpers.dispatcher_contract_telemetry(
            query=query,
            query_class=impl_query_class,
            semantic_candidates=pre_duplicate_results,
            ranked_candidates=pre_dispatcher_recovery_results,
            final_results=all_results,
            rescue_applied=dispatcher_rescue_applied,
        )
        if dispatcher_contract_trace:
            retrieval_telemetry.append_dispatcher_telemetry_event(
                dispatcher_contract_trace,
                query=query,
                tool="search_codebase",
                topic="",
            )
            debug_log(
                "dispatcher_contract_telemetry",
                query=query,
                query_class=dispatcher_contract_trace.get("query_class"),
                diagnosis=dispatcher_contract_trace.get("diagnosis"),
                rescue_applied=dispatcher_contract_trace.get("rescue_applied"),
                exact_identifiers=dispatcher_contract_trace.get("exact_identifiers"),
                semantic_exact_match_count=dispatcher_contract_trace.get("semantic_exact_match_count"),
                semantic_contract_match_count=dispatcher_contract_trace.get("semantic_contract_match_count"),
                ranked_exact_match_count=dispatcher_contract_trace.get("ranked_exact_match_count"),
                ranked_contract_match_count=dispatcher_contract_trace.get("ranked_contract_match_count"),
                final_exact_match_count=dispatcher_contract_trace.get("final_exact_match_count"),
                final_contract_match_count=dispatcher_contract_trace.get("final_contract_match_count"),
                semantic_top=dispatcher_contract_trace.get("semantic_top"),
                ranked_top=dispatcher_contract_trace.get("ranked_top"),
                final_top=dispatcher_contract_trace.get("final_top"),
                dispatcher_anchor_contract_capability=dispatcher_contract_trace.get(
                    "dispatcher_anchor_contract_capability"
                ),
                dispatcher_anchor_contract_version=dispatcher_contract_trace.get(
                    "dispatcher_anchor_contract_version"
                ),
            )
        pre_routing_partition_results = list(all_results)
        routing_partition_applied = False
        routing_signal_trace = sem_helpers.routing_signal_telemetry(
            query=query,
            query_class=impl_query_class,
            semantic_candidates=pre_duplicate_results,
            ranked_candidates=pre_routing_partition_results,
            final_results=all_results,
            partition_applied=routing_partition_applied,
        )
        if routing_signal_trace:
            retrieval_telemetry.append_routing_telemetry_event(
                routing_signal_trace,
                query=query,
                tool="search_codebase",
                topic="",
            )
            debug_log(
                "routing_signal_telemetry",
                query=query,
                query_class=routing_signal_trace.get("query_class"),
                diagnosis=routing_signal_trace.get("diagnosis"),
                partition_applied=routing_signal_trace.get("partition_applied"),
                semantic_signal_match_count=routing_signal_trace.get("semantic_signal_match_count"),
                ranked_signal_match_count=routing_signal_trace.get("ranked_signal_match_count"),
                final_signal_match_count=routing_signal_trace.get("final_signal_match_count"),
                semantic_top=routing_signal_trace.get("semantic_top"),
                ranked_top=routing_signal_trace.get("ranked_top"),
                final_top=routing_signal_trace.get("final_top"),
            )

    all_results = retrieval_metadata.cap_per_file(all_results, max_per_file)
    all_results = retrieval_metadata.cap_per_dir(all_results, max_per_dir)
    top = all_results[:k]

    fallback_lines: list[str] = []
    debug_tokens: list[str] = []
    rg_hint = os.getenv("LM_PROXY_RG_PATH") or "(auto)"

    if fallback == "grep" and top:
        unique_files = len(
            {r.get("file_path") for r in top if r.get("file_path")}
        )
        ratio = unique_files / max(1, len(top))
        if unique_files <= 1 or ratio <= fallback_ratio:
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
                    fallback_lines.append(label)

    return {
        "results": top,
        "all_results": all_results,
        "pid_to_name": pid_to_name,
        "pid_to_path": pid_to_path,
        "multi": multi,
        "include_metadata": include_metadata,
        "duplicate_trace": duplicate_trace,
        "duplicate_trace_enabled": duplicate_trace_enabled,
        "duplicate_telemetry_enabled": duplicate_telemetry_enabled,
        "fallback_lines": fallback_lines,
        "fallback_tokens": debug_tokens,
        "fallback_glob": fallback_glob,
        "fallback_rg_hint": rg_hint,
    }

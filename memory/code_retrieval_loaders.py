"""Database loading helpers for code retrieval."""

from __future__ import annotations

import os

from neo4j import unit_of_work


async def execute_graph_read(session, cypher: str, op: str, **params):
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


async def load_cargo_crate_rows(driver, neo4j_db: str, project_ids: list[str]) -> dict[str, list[dict]]:
    if not driver or not project_ids:
        return {}
    project_ids = [pid for pid in project_ids if pid]
    if not project_ids:
        return {}
    async with driver.session(database=neo4j_db) as session:
        schema_rows = await execute_graph_read(
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
            rows = await execute_graph_read(
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


async def load_rescue_rows(
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


async def load_path_hint_rows(
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


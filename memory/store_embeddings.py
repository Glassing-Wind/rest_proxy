"""memory/store_embeddings.py — embedding persistence helpers."""

import json
import os
import time
from typing import Any, Dict, List, Optional

import graph_bootstrap
from memory import store_core

try:
    import tree_sitter_language_pack as ts_pack
except Exception:
    ts_pack = None


_PG_COPY_INGEST = os.getenv("LM_PROXY_PG_COPY_INGEST", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_LEAN_INGEST_METADATA = os.getenv("LM_PROXY_LEAN_INGEST_METADATA", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_DEFERRED_LINK_BATCH_SIZE = max(
    1, int(os.getenv("LM_PROXY_DEFER_NEO4J_LINK_BATCH_SIZE", "4096"))
)

_CODEBASE_EMBEDDINGS_STAGE_SQL = """\
CREATE TEMP TABLE IF NOT EXISTS codebase_embeddings_stage (
    chunk_id TEXT,
    project_id TEXT,
    file_path TEXT,
    ref_type TEXT,
    chunk_index INTEGER,
    content TEXT,
    embedding_text TEXT,
    metadata_text TEXT,
    created_at_epoch DOUBLE PRECISION
) ON COMMIT DROP
"""

_CODEBASE_EMBEDDINGS_STAGE_COPY_SQL = """\
COPY codebase_embeddings_stage
  (chunk_id, project_id, file_path, ref_type, chunk_index,
   content, embedding_text, metadata_text, created_at_epoch)
FROM STDIN
"""

_CODEBASE_EMBEDDINGS_STAGE_INSERT_SQL = """\
INSERT INTO codebase_embeddings
  (chunk_id, project_id, file_path, ref_type, chunk_index,
   content, embedding, metadata, created_at)
SELECT
  chunk_id,
  project_id,
  file_path,
  ref_type,
  chunk_index,
  content,
  embedding_text::vector,
  metadata_text::jsonb,
  created_at_epoch
FROM codebase_embeddings_stage
ON CONFLICT (chunk_id) DO NOTHING
"""


async def _neo4j_link_embedding_refs_batch(
    session,
    *,
    session_id: str,
    ref_ids: list[str],
    project_id: str,
) -> None:
    started_at = time.perf_counter()
    cypher = """\
MATCH (s:Session {id: $session_id})
MATCH (p:Project {id: $project_id})
UNWIND $items AS item
MERGE (m:Node {id: item.id})
ON CREATE SET m:Chunk, m.project_id = item.project_id
MERGE (s)-[:HAS_EMBEDDING]->(m)
MERGE (p)-[:HAS_EMBEDDING]->(m)
"""
    await store_core._neo4j_write(
        session,
        cypher,
        "link_embedding_refs_batch",
        session_id=session_id,
        project_id=project_id,
        items=[{"id": rid, "project_id": project_id} for rid in ref_ids],
    )
    store_core._debug(
        "graph_link_embedding_refs_batch",
        session_id=session_id,
        project_id=project_id,
        count=len(ref_ids),
        elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
    )


async def _neo4j_ensure_embedding_link_context(
    session,
    *,
    session_id: str,
    project_id: str,
) -> None:
    cypher = """\
MERGE (:Session {id: $session_id})
MERGE (:Project {id: $project_id})
"""
    await store_core._neo4j_write(
        session,
        cypher,
        "ensure_embedding_link_context",
        session_id=session_id,
        project_id=project_id,
    )


async def link_embedding_refs(
    session_id: str,
    project_id: str,
    ref_ids: list[str],
    *,
    batch_size: int = _DEFERRED_LINK_BATCH_SIZE,
) -> int:
    """Link existing embedding refs into Neo4j in large batches."""
    if not ref_ids or not store_core._pool_available():
        return 0
    driver = graph_bootstrap.get_driver()
    if not driver:
        return 0

    unique_ids = list(dict.fromkeys(rid for rid in ref_ids if rid))
    if not unique_ids:
        return 0

    linked = 0
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
        await _neo4j_ensure_embedding_link_context(
            s,
            session_id=session_id,
            project_id=project_id,
        )
        for start in range(0, len(unique_ids), max(1, batch_size)):
            group = unique_ids[start : start + max(1, batch_size)]
            await _neo4j_link_embedding_refs_batch(
                s,
                session_id=session_id,
                ref_ids=group,
                project_id=project_id,
            )
            linked += len(group)
    return linked


def _compact_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        compacted = {
            key: _compact_metadata_value(item)
            for key, item in value.items()
            if item not in (None, "", [], {})
        }
        return compacted
    if isinstance(value, list):
        compacted = [_compact_metadata_value(item) for item in value if item not in (None, "", [], {})]
        return compacted
    return value


def _prepare_batch_for_storage(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not _LEAN_INGEST_METADATA:
        return batch
    prepared: List[Dict[str, Any]] = []
    for item in batch:
        meta = item.get("metadata")
        if isinstance(meta, dict):
            compacted = _compact_metadata_value(meta)
            if compacted is not meta:
                row = dict(item)
                row["metadata"] = compacted
                prepared.append(row)
                continue
        prepared.append(item)
    return prepared


def _can_use_pg_copy(cursor: Any) -> bool:
    return bool(
        _PG_COPY_INGEST
        and ts_pack
        and hasattr(ts_pack, "build_codebase_embedding_rows")
        and hasattr(cursor, "copy")
    )


async def _copy_codebase_embeddings_batch(
    cursor: Any,
    batch: List[Dict[str, Any]],
    project_id: str,
) -> int:
    prepared_batch = _prepare_batch_for_storage(batch)
    started = time.perf_counter()
    rows = ts_pack.build_codebase_embedding_rows(
        prepared_batch,
        project_id,
        expected_dim=store_core._EXPECTED_EMBEDDING_DIM,
        created_at=time.time(),
    )
    if not rows:
        return 0

    await cursor.execute(_CODEBASE_EMBEDDINGS_STAGE_SQL, ())
    async with cursor.copy(_CODEBASE_EMBEDDINGS_STAGE_COPY_SQL) as copy:
        for row in rows:
            await copy.write_row(row)
    await cursor.execute(_CODEBASE_EMBEDDINGS_STAGE_INSERT_SQL, ())
    store_core._debug(
        "pg_copy_codebase_embeddings_batch",
        project_id=project_id,
        count=len(rows),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return len(rows)


async def insert_codebase_embedding(
    chunk_id: str,
    project_id: str,
    file_path: str,
    chunk_index: int,
    content: str,
    vector: List[float],
    ref_type: str = "code_chunk",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Upsert a codebase chunk into Postgres codebase_embeddings.
    Uses chunk_id as the stable conflict key — safe to call on re-index.
    Returns True on success.
    """
    if not store_core._pg_pool_available():
        return False
    if not ts_pack or not hasattr(ts_pack, "execute_codebase_embedding_upsert"):
        store_core._debug("pg_insert_ce_missing_ts_pack", project_id=project_id)
        return False
    try:
        batch = [
            {
                "ref_id": chunk_id,
                "ref_type": ref_type,
                "text": content,
                "vector": vector,
                "metadata": dict(metadata or {}, file=file_path, chunk_index=chunk_index),
            }
        ]
        async with store_core._pg_pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                started = time.perf_counter()
                if _can_use_pg_copy(cur):
                    written = await _copy_codebase_embeddings_batch(cur, batch, project_id)
                else:
                    written = await ts_pack.execute_codebase_embedding_upsert(
                        cur,
                        _prepare_batch_for_storage(batch),
                        project_id,
                        expected_dim=store_core._EXPECTED_EMBEDDING_DIM,
                        created_at=time.time(),
                    )
                    store_core._debug(
                        "pg_upsert_codebase_embedding_batch",
                        project_id=project_id,
                        count=written,
                        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                    )
        return written > 0
    except Exception as exc:
        store_core._debug(
            "pg_insert_ce_error",
            project_id=project_id,
            file_path=file_path,
            error=str(exc),
        )
        return False


async def insert_embeddings_batch(
    session_id: str,
    project_id: str,
    batch: List[Dict[str, Any]],
    project_path: Optional[str] = None,
    *,
    link_refs: bool = True,
) -> int:
    """
    Insert a batch of codebase chunks.

    PRIMARY STORE: Postgres codebase_embeddings
      Single executemany call per batch — one round-trip instead of N.
      ON CONFLICT (chunk_id) DO NOTHING — safe for re-index.

    SECONDARY: Neo4j lightweight Chunk reference node
      No embedding, no text — just id + project_id for graph traversal.
    """
    if not store_core._ENABLE_EMBEDDINGS or not batch:
        return 0

    try:
        import hashlib

        if project_path and not project_id:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]

        # ── Phase 1: Postgres batch upsert (single connection, one round-trip) ─
        written = 0
        if store_core._pg_pool_available():
            if ts_pack and hasattr(ts_pack, "execute_codebase_embedding_upsert"):
                async with store_core._pg_pool.connection() as conn:  # type: ignore[union-attr]
                    async with conn.cursor() as cur:
                        started = time.perf_counter()
                        if _can_use_pg_copy(cur):
                            written = await _copy_codebase_embeddings_batch(cur, batch, project_id)
                        else:
                            written = await ts_pack.execute_codebase_embedding_upsert(
                                cur,
                                _prepare_batch_for_storage(batch),
                                project_id,
                                expected_dim=store_core._EXPECTED_EMBEDDING_DIM,
                                created_at=time.time(),
                            )
                            store_core._debug(
                                "pg_upsert_codebase_embeddings_batch",
                                project_id=project_id,
                                count=written,
                                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                            )
            else:
                return 0
        else:
            # No Postgres — fall back to Neo4j full write
            return await _neo4j_insert_embeddings_batch(session_id, project_id, batch)

        # ── Phase 2: Neo4j lightweight Chunk reference nodes ─────────────────
        # No embedding or text stored — just the id for graph traversal.
        if link_refs:
            await link_embedding_refs(
                session_id,
                project_id,
                [it["ref_id"] for it in batch],
            )

        store_core._debug(
            "insert_embeddings_batch", session_id=session_id, count=written
        )
        return written

    except Exception as exc:
        store_core._debug(
            "insert_embeddings_batch_error", session_id=session_id, error=str(exc)
        )
        return 0


async def _neo4j_insert_embeddings_batch(
    session_id: str,
    project_id: str,
    batch: List[Dict[str, Any]],
) -> int:
    """Fallback: write full chunks to Neo4j when Postgres is unavailable."""
    if (
        not store_core._ENABLE_EMBEDDINGS
        or not store_core._pool_available()
        or not batch
    ):
        return 0
    try:
        driver = graph_bootstrap.get_driver()
        if not driver:
            return 0
        created_at = time.time()
        items = [
            {
                "ref_id": b["ref_id"],
                "ref_type": b.get("ref_type", "code_chunk"),
                "text": b["text"],
                "vector": b["vector"],
                "metadata": json.dumps(b.get("metadata", {})),
            }
            for b in batch
        ]
        chunk_cypher = """\
UNWIND $items AS item
MERGE (m:Node {id: item.ref_id})
ON CREATE SET
    m:Chunk, m:MemoryEmbedding,
    m.session_id  = $session_id,
    m.project_id  = $project_id,
    m.ref_type    = item.ref_type,
    m.text        = item.text,
    m.embedding   = item.vector,
    m.metadata    = item.metadata,
    m.created_at  = $created_at
"""
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
            await store_core._neo4j_write(
                s,
                chunk_cypher,
                "neo4j_insert_embeddings_batch",
                items=items,
                session_id=session_id,
                project_id=project_id,
                created_at=created_at,
            )
        return len(batch)
    except Exception as exc:
        store_core._debug("neo4j_fallback_error", session_id=session_id, error=str(exc))
        return 0


async def insert_embedding(
    session_id: str,
    ref_id: str,
    ref_type: str,
    compact_text: str,
    vector: List[float],
    metadata: Optional[Dict[str, Any]] = None,
    project_path: Optional[str] = None,
) -> Optional[str]:
    """Insert a vector embedding record into Neo4j; links to optional Project."""
    row_ids = await insert_memory_embeddings_batch(
        [
            {
                "session_id": session_id,
                "ref_id": ref_id,
                "ref_type": ref_type,
                "compact_text": compact_text,
                "vector": vector,
                "metadata": metadata,
                "project_path": project_path,
            }
        ]
    )
    return row_ids[0] if row_ids else None


async def insert_memory_embeddings_batch(
    rows: List[Dict[str, Any]],
) -> List[str]:
    """Insert multiple memory embedding records into Neo4j in one UNWIND write."""
    if not store_core._ENABLE_EMBEDDINGS or not store_core._pool_available() or not rows:
        return []

    started_at = time.perf_counter()
    prepared_rows: list[dict[str, Any]] = []
    for row in rows:
        vector = row.get("vector")
        if not isinstance(vector, list) or not vector:
            store_core._debug(
                "graph_insert_embedding_invalid_vector",
                session_id=row.get("session_id"),
                ref_type=row.get("ref_type"),
            )
            continue
        actual_dim = len(vector)
        if actual_dim != store_core._EXPECTED_EMBEDDING_DIM:
            store_core._debug(
                "graph_insert_embedding_dim_mismatch",
                session_id=row.get("session_id"),
                ref_type=row.get("ref_type"),
                expected_dim=store_core._EXPECTED_EMBEDDING_DIM,
                actual_dim=actual_dim,
            )
            continue

        project_id = None
        project_path = row.get("project_path")
        if project_path:
            import hashlib

            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]

        metadata = dict(row.get("metadata") or {})
        prepared_rows.append(
            {
                "ref_id": row["ref_id"],
                "session_id": row["session_id"],
                "project_id": project_id,
                "ref_type": row["ref_type"],
                "text": row["compact_text"],
                "vector": vector,
                "metadata": json.dumps(metadata),
                "created_at": row.get("created_at", time.time()),
                "file_path": metadata.get("file"),
            }
        )

    if not prepared_rows:
        return []

    cypher = """
    UNWIND $rows AS row
    MERGE (m:Chunk {id: row.ref_id})
    SET m:MemoryEmbedding,
        m.session_id = row.session_id,
        m.project_id = row.project_id,
        m.ref_type = row.ref_type,
        m.text = row.text,
        m.embedding = row.vector,
        m.metadata = row.metadata,
        m.created_at = row.created_at
    WITH m, row
    MERGE (s:Session {id: row.session_id})
    MERGE (s)-[:HAS_EMBEDDING]->(m)
    WITH m, row
    WHERE row.project_id IS NOT NULL
    MERGE (p:Project {id: row.project_id})
    MERGE (p)-[:HAS_EMBEDDING]->(m)
    WITH m, p, row
    FOREACH (_ IN CASE WHEN row.file_path IS NOT NULL THEN [1] ELSE [] END |
        MERGE (f:File {id: row.project_id + ":" + row.file_path})
        MERGE (p)-[:HAS_FILE]->(f)
        MERGE (f)-[:HAS_CHUNK]->(m)
    )
    """
    try:
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await store_core._neo4j_write(
                session,
                cypher,
                "insert_memory_embeddings_batch",
                rows=prepared_rows,
            )
        store_core._debug(
            "graph_insert_memory_embeddings_batch",
            count=len(prepared_rows),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return [row["ref_id"] for row in prepared_rows]
    except Exception as exc:
        store_core._debug("graph_insert_memory_embeddings_batch_error", error=str(exc))
        return []

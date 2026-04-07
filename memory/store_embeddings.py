"""memory/store_embeddings.py — embedding persistence helpers."""

import json
import time
from typing import Any, Dict, List, Optional

import graph_bootstrap
from memory import store_core

try:
    import tree_sitter_language_pack as ts_pack
except Exception:
    ts_pack = None


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
                written = await ts_pack.execute_codebase_embedding_upsert(
                    cur,
                    batch,
                    project_id,
                    expected_dim=store_core._EXPECTED_EMBEDDING_DIM,
                    created_at=time.time(),
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
                        written = await ts_pack.execute_codebase_embedding_upsert(
                            cur,
                            batch,
                            project_id,
                            expected_dim=store_core._EXPECTED_EMBEDDING_DIM,
                            created_at=time.time(),
                        )
            else:
                return 0
        else:
            # No Postgres — fall back to Neo4j full write
            return await _neo4j_insert_embeddings_batch(session_id, project_id, batch)

        # ── Phase 2: Neo4j lightweight Chunk reference nodes ─────────────────
        # No embedding or text stored — just the id for graph traversal.
        if store_core._pool_available():
            driver = graph_bootstrap.get_driver()
            if driver:
                ref_ids = [it["ref_id"] for it in batch]
                items_bolt = [{"id": rid, "project_id": project_id} for rid in ref_ids]

                chunk_ref_cypher = """\
UNWIND $items AS item
MERGE (c:Node {id: item.id})
ON CREATE SET c:Chunk, c.project_id = item.project_id
"""
                async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
                    await s.run(chunk_ref_cypher, items=items_bolt)

                # Session + Project linking (once per batch)
                node_cypher = """\
MERGE (s:Session {id: $session_id})
MERGE (p:Project {id: $project_id})
"""
                edge_cypher = """\
MATCH (s:Session {id: $session_id})
MATCH (p:Project {id: $project_id})
WITH s, p
UNWIND $ref_ids AS rid
MATCH (m:Node {id: rid})
MERGE (s)-[:HAS_EMBEDDING]->(m)
MERGE (p)-[:HAS_EMBEDDING]->(m)
"""
                async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
                    await s.run(
                        node_cypher, session_id=session_id, project_id=project_id
                    )
                async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
                    await s.run(
                        edge_cypher,
                        session_id=session_id,
                        project_id=project_id,
                        ref_ids=ref_ids,
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
            await s.run(
                chunk_cypher,
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
    if not store_core._ENABLE_EMBEDDINGS or not store_core._pool_available():
        return None

    if not isinstance(vector, list) or not vector:
        store_core._debug(
            "graph_insert_embedding_invalid_vector",
            session_id=session_id,
            ref_type=ref_type,
        )
        return None

    actual_dim = len(vector)
    if actual_dim != store_core._EXPECTED_EMBEDDING_DIM:
        store_core._debug(
            "graph_insert_embedding_dim_mismatch",
            session_id=session_id,
            ref_type=ref_type,
            expected_dim=store_core._EXPECTED_EMBEDDING_DIM,
            actual_dim=actual_dim,
        )
        return None
    try:
        import hashlib

        project_id = None
        if project_path:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]

        cypher = """
        MERGE (m:Chunk {id: $ref_id})
        SET m:MemoryEmbedding,
            m.session_id = $session_id,
            m.project_id = $project_id,
            m.ref_type = $ref_type,
            m.text = $text,
            m.embedding = $vector,
            m.metadata = $metadata,
            m.created_at = $created_at
        WITH m
        MERGE (s:Session {id: $session_id})
        MERGE (s)-[:HAS_EMBEDDING]->(m)
        WITH m
        WHERE $project_id IS NOT NULL
        MERGE (p:Project {id: $project_id})
        MERGE (p)-[:HAS_EMBEDDING]->(m)
        WITH m, p
        // Link to File if metadata contains it
        FOREACH (_ IN CASE WHEN $file_path IS NOT NULL THEN [1] ELSE [] END |
            MERGE (f:File {id: $project_id + ":" + $file_path})
            MERGE (p)-[:HAS_FILE]->(f)
            MERGE (f)-[:HAS_CHUNK]->(m)
        )
        """
        driver = graph_bootstrap.get_driver()
        if not driver:
            return None

        file_path = metadata.get("file") if metadata else None

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await store_core._neo4j_write(
                session,
                cypher,
                "insert_embedding",
                ref_id=ref_id,
                session_id=session_id,
                project_id=project_id,
                ref_type=ref_type,
                text=compact_text,
                vector=vector,
                metadata=json.dumps(metadata or {}),
                created_at=time.time(),
                file_path=file_path,
            )
        store_core._debug(
            "graph_insert_embedding_ok", session_id=session_id, project_id=project_id
        )
        return ref_id
    except Exception as exc:
        store_core._debug(
            "graph_insert_embedding_error", session_id=session_id, error=str(exc)
        )
        return None

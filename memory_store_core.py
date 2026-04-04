"""memory_store.py – Redis hot-state, Postgres (pgvector) and Neo4j storage.

Design:
- Redis:    rolling summary, working memory, recent turns (hot state)
- Postgres: codebase_embeddings (chunk text + 768-dim vectors, RRF hybrid search)
            doc_embeddings (future: external documentation)
            conversation_turns, memory_embeddings, etc.
- Neo4j:    structural graph (files, symbols, CONTAINS/CALLS/IMPORTS relationships)
            lightweight Chunk reference nodes (id only, no embedding/text)
- All operations are best-effort: exceptions caught and logged, never propagated.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import graph_bootstrap
from neo4j import unit_of_work

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_ENABLE_MEMORY = os.getenv("LM_PROXY_MEMORY_ENABLED", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_ENABLE_REDIS = os.getenv("LM_PROXY_MEMORY_ENABLE_REDIS", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_ENABLE_PERSISTENCE = os.getenv(
    "LM_PROXY_MEMORY_ENABLE_PERSISTENCE", "1"
).strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_RETRIEVAL = os.getenv(
    "LM_PROXY_MEMORY_ENABLE_RETRIEVAL", "1"
).strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_EMBEDDINGS = os.getenv(
    "LM_PROXY_MEMORY_ENABLE_EMBEDDINGS", "0"
).strip().lower() in {"1", "true", "yes", "on"}
_EXPECTED_EMBEDDING_DIM = int(os.getenv("LM_PROXY_MEMORY_EMBEDDING_DIM", "768"))
_NEO4J_TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
_NEO4J_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_NEO4J_META_BASE = {"source": "lm_proxy", "tool": "memory_store"}

_REDIS_URL = os.getenv("LM_PROXY_REDIS_URL", "redis://localhost:6379/0")
_PG_DSN = os.getenv("LM_PROXY_PG_DSN", "")
_PG_POOL_MIN = int(os.getenv("LM_PROXY_PG_POOL_MIN", "1"))
_PG_POOL_MAX = int(os.getenv("LM_PROXY_PG_POOL_MAX", "6"))
_SESSION_NAMESPACE = os.getenv("LM_PROXY_MEMORY_SESSION_NAMESPACE", "lmproxy")
_MAX_RECENT_TURNS = int(os.getenv("LM_PROXY_MEMORY_MAX_RECENT_TURNS", "20"))
_FACTS_PER_SUMMARY_SECTION = int(os.getenv("LM_PROXY_MEMORY_FACTS_PER_SECTION", "4"))
_ENABLE_DEBUG = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_NEO4J_WRITE_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_WRITE_TIMEOUT", "15.0"))

# Redis key TTL (seconds) – 7 days
_REDIS_TTL = 60 * 60 * 24 * 7


def _debug(message: str, **fields: Any) -> None:
    if not _ENABLE_DEBUG:
        return
    payload = {"message": message}
    payload.update(fields)
    try:
        print(
            f"[lm-proxy:memory_store] {json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}",
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        print(
            f"[lm-proxy:memory_store] {message} {fields}", file=sys.stderr, flush=True
        )


async def _neo4j_write(session, cypher: str, op: str, **params) -> None:
    metadata = dict(_NEO4J_META_BASE)
    op_value = op or "write"
    if _NEO4J_OP_PREFIX:
        op_value = f"{_NEO4J_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    @unit_of_work(timeout=_NEO4J_TX_TIMEOUT, metadata=metadata)
    async def _tx(tx):
        res = await tx.run(cypher, **params)
        await res.consume()

    if hasattr(session, "execute_write"):
        await session.execute_write(_tx)
    else:
        await _tx(session)


async def _neo4j_read(session, cypher: str, op: str, **params) -> list[dict]:
    metadata = dict(_NEO4J_META_BASE)
    op_value = op or "read"
    if _NEO4J_OP_PREFIX:
        op_value = f"{_NEO4J_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    @unit_of_work(timeout=_NEO4J_TX_TIMEOUT, metadata=metadata)
    async def _tx(tx):
        res = await tx.run(cypher, **params)
        return await res.data()

    if hasattr(session, "execute_read"):
        return await session.execute_read(_tx)
    return await _tx(session)


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------
_redis_client: Optional[Any] = None  # redis.asyncio.Redis


def _redis_key(session_id: str, suffix: str) -> str:
    return f"{_SESSION_NAMESPACE}:{session_id}:{suffix}"


async def _get_redis() -> Optional[Any]:
    """Lazy-initialize a Redis client. Returns None if unavailable."""
    global _redis_client
    if not _ENABLE_REDIS:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis  # type: ignore

        _redis_client = aioredis.from_url(_REDIS_URL, decode_responses=True)
        await _redis_client.ping()
        _debug("redis_connected", url=_REDIS_URL)
    except Exception as exc:
        _debug("redis_connect_failed", error=str(exc))
        _redis_client = None
    return _redis_client


async def get_session_state(session_id: str) -> Dict[str, Any]:
    """Retrieve structured working memory from Redis. Returns empty dict on miss."""
    try:
        r = await _get_redis()
        if r is None:
            return {}
        val = await r.get(_redis_key(session_id, "state"))
        if val:
            return json.loads(val)
    except Exception as exc:
        _debug("redis_get_state_error", session_id=session_id, error=str(exc))
    return {}


async def set_session_state(session_id: str, state: Dict[str, Any]) -> None:
    """Persist structured working memory to Redis."""
    try:
        r = await _get_redis()
        if r is None:
            return
        await r.set(_redis_key(session_id, "state"), json.dumps(state), ex=_REDIS_TTL)
    except Exception as exc:
        _debug("redis_set_state_error", session_id=session_id, error=str(exc))


async def get_rolling_summary(session_id: str) -> str:
    """Retrieve rolling summary text from Redis."""
    try:
        r = await _get_redis()
        if r is None:
            return ""
        val = await r.get(_redis_key(session_id, "summary"))
        return val or ""
    except Exception as exc:
        _debug("redis_get_summary_error", session_id=session_id, error=str(exc))
    return ""


async def set_rolling_summary(session_id: str, summary: str) -> None:
    """Persist rolling summary text to Redis."""
    try:
        r = await _get_redis()
        if r is None:
            return
        await r.set(_redis_key(session_id, "summary"), summary, ex=_REDIS_TTL)
    except Exception as exc:
        _debug("redis_set_summary_error", session_id=session_id, error=str(exc))


async def get_file_skeleton(session_id: str, file_path_hash: str) -> Optional[str]:
    """Retrieve a cached codebase skeleton from Redis."""
    try:
        r = await _get_redis()
        if r is None:
            return None
        # Use project session id + file hash
        key = _redis_key(session_id, f"skeleton:{file_path_hash}")
        return await r.get(key)
    except Exception as exc:
        _debug("redis_get_skeleton_error", session_id=session_id, error=str(exc))
    return None


async def set_file_skeleton(
    session_id: str, file_path_hash: str, skeleton: str
) -> None:
    """Cache a parsed codebase skeleton to Redis."""
    try:
        r = await _get_redis()
        if r is None:
            return
        key = _redis_key(session_id, f"skeleton:{file_path_hash}")
        # Keep skeletons cached for 7 days
        await r.set(key, skeleton, ex=_REDIS_TTL)
    except Exception as exc:
        _debug("redis_set_skeleton_error", session_id=session_id, error=str(exc))


async def append_recent_turn(session_id: str, turn: Dict[str, Any]) -> None:
    """Append a turn dict to the recent-turns list in Redis (capped at _MAX_RECENT_TURNS)."""
    try:
        r = await _get_redis()
        if r is None:
            return
        key = _redis_key(session_id, "turns")
        serialized = json.dumps(turn)
        async with r.pipeline(transaction=True) as pipe:
            pipe.rpush(key, serialized)
            pipe.ltrim(key, -_MAX_RECENT_TURNS, -1)
            pipe.expire(key, _REDIS_TTL)
            await pipe.execute()
    except Exception as exc:
        _debug("redis_append_turn_error", session_id=session_id, error=str(exc))


async def get_recent_turns(session_id: str) -> List[Dict[str, Any]]:
    """Retrieve the recent-turns list from Redis."""
    try:
        r = await _get_redis()
        if r is None:
            return []
        key = _redis_key(session_id, "turns")
        raw_list = await r.lrange(key, 0, -1)
        turns: List[Dict[str, Any]] = []
        for raw in raw_list:
            try:
                turns.append(json.loads(raw))
            except Exception:
                pass
        return turns
    except Exception as exc:
        _debug("redis_get_turns_error", session_id=session_id, error=str(exc))
    return []


# ---------------------------------------------------------------------------
# Postgres pool (codebase / doc embeddings)
# ---------------------------------------------------------------------------

_pg_pool: Optional[Any] = None  # psycopg_pool.AsyncConnectionPool


async def _open_pg_pool() -> None:
    global _pg_pool
    if not _PG_DSN or _pg_pool is not None:
        return
    try:
        from psycopg_pool import AsyncConnectionPool  # type: ignore

        _pg_pool = AsyncConnectionPool(
            _PG_DSN, min_size=_PG_POOL_MIN, max_size=_PG_POOL_MAX, open=False
        )
        await _pg_pool.open(wait=True, timeout=15)
        _debug("pg_pool_opened", dsn=_PG_DSN[:40])
    except Exception as exc:
        _debug("pg_pool_open_failed", error=str(exc))
        _pg_pool = None


def _pg_pool_available() -> bool:
    return _ENABLE_PERSISTENCE and _PG_DSN != "" and _pg_pool is not None


# ---------------------------------------------------------------------------
# Neo4j helpers – backed by graph_bootstrap.py
# ---------------------------------------------------------------------------


async def open_pool() -> None:
    """Initialize both Postgres pool and Neo4j driver."""
    await _open_pg_pool()
    try:
        await graph_bootstrap.init_graph_db()
    except Exception as exc:
        _debug("graph_init_failed", error=str(exc))


async def close_pool() -> None:
    """Close Postgres pool, Neo4j driver, and Redis client."""
    global _redis_client, _pg_pool
    try:
        if _redis_client is not None:
            await _redis_client.close()
            _redis_client = None
            _debug("redis_disconnected")
        if _pg_pool is not None:
            await _pg_pool.close()
            _pg_pool = None
            _debug("pg_pool_closed")
        await graph_bootstrap.close_graph_db()
    except Exception as exc:
        _debug("pool_close_failed", error=str(exc))


def _pool_available() -> bool:
    """Check if Neo4j is available (for graph operations)."""
    return _ENABLE_PERSISTENCE and graph_bootstrap._NEO4J_ENABLED


async def insert_turn(
    session_id: str,
    turn_index: int,
    role: str,
    content: str,
    compact_content: str,
    model: Optional[str] = None,
    tool_name: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    project_path: Optional[str] = None,
) -> Optional[str]:
    """Insert a conversation turn into Neo4j; links to Session and optional Project."""
    if not _pool_available():
        return None
    try:
        import uuid as _uuid
        import hashlib

        row_id = str(_uuid.uuid4())
        meta_json = json.dumps(metadata or {})

        project_id = None
        if project_path:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]

        cypher = """
        MERGE (t:MemoryTurn {id: $id})
        SET t.session_id = $session_id,
            t.project_id = $project_id,
            t.turn_index = $turn_index,
            t.role = $role,
            t.content = $content,
            t.compact_content = $compact_content,
            t.model = $model,
            t.tool_name = $tool_name,
            t.tool_call_id = $tool_call_id,
            t.metadata = $metadata,
            t.created_at = $created_at
        WITH t
        MERGE (s:Session {id: $session_id})
        MERGE (s)-[:HAS_TURN]->(t)
        WITH t
        WHERE $project_id IS NOT NULL
        MERGE (p:Project {id: $project_id})
        MERGE (p)-[:HAS_TURN]->(t)
        """
        driver = graph_bootstrap.get_driver()
        if not driver:
            return None

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:

            async def _tx(tx):
                result = await tx.run(
                    cypher,
                    id=row_id,
                    session_id=session_id,
                    project_id=project_id,
                    turn_index=turn_index,
                    role=role,
                    content=content,
                    compact_content=compact_content,
                    model=model,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    metadata=meta_json,
                    created_at=time.time(),
                )
                await result.consume()

            if hasattr(session, "execute_write"):
                await session.execute_write(_tx)
            else:
                await _tx(session)
        _debug("graph_insert_turn", session_id=session_id, project_id=project_id)
        return row_id
    except Exception as exc:
        _debug("graph_insert_turn_error", session_id=session_id, error=str(exc))
        return None


async def insert_summary(
    session_id: str,
    summary_text: str,
    summary_type: str = "rolling",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert a memory summary record into Neo4j."""
    if not _pool_available():
        return None
    try:
        import uuid as _uuid

        row_id = str(_uuid.uuid4())
        meta_json = json.dumps(metadata or {})

        cypher = """
        MERGE (s:MemorySummary {id: $id})
        SET s.session_id = $session_id,
            s.summary_text = $summary_text,
            s.summary_type = $summary_type,
            s.metadata = $metadata,
            s.created_at = $created_at
        WITH s
        MERGE (sess:Session {id: $session_id})
        MERGE (sess)-[:HAS_SUMMARY]->(s)
        """
        driver = graph_bootstrap.get_driver()
        if not driver:
            return None
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:

            async def _tx(tx):
                result = await tx.run(
                    cypher,
                    id=row_id,
                    session_id=session_id,
                    summary_text=summary_text,
                    summary_type=summary_type,
                    metadata=meta_json,
                    created_at=time.time(),
                )
                await result.consume()

            if hasattr(session, "execute_write"):
                await session.execute_write(_tx)
            else:
                await _tx(session)
        _debug("graph_insert_summary", session_id=session_id, type=summary_type)
        return row_id
    except Exception as exc:
        _debug("graph_insert_summary_error", session_id=session_id, error=str(exc))
        return None


async def insert_tool_output(
    session_id: str,
    tool_name: str,
    tool_call_id: str,
    raw_output: str,
    compact_output: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert a raw + compact tool output record into Neo4j."""
    if not _pool_available():
        return None
    try:
        import uuid as _uuid

        row_id = str(_uuid.uuid4())
        meta_json = json.dumps(metadata or {})

        cypher = """
        MERGE (o:ToolOutput {id: $id})
        SET o.session_id = $session_id,
            o.tool_name = $tool_name,
            o.tool_call_id = $tool_call_id,
            o.raw_output = $raw_output,
            o.compact_output = $compact_output,
            o.metadata = $metadata,
            o.created_at = $created_at
        WITH o
        MERGE (sess:Session {id: $session_id})
        MERGE (sess)-[:PRODUCED_OUTPUT]->(o)
        """
        driver = graph_bootstrap.get_driver()
        if not driver:
            return None
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:

            async def _tx(tx):
                result = await tx.run(
                    cypher,
                    id=row_id,
                    session_id=session_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    raw_output=raw_output,
                    compact_output=compact_output,
                    metadata=meta_json,
                    created_at=time.time(),
                    timeout=_NEO4J_WRITE_TIMEOUT_S,
                )
                await result.consume()

            if hasattr(session, "execute_write"):
                await session.execute_write(_tx)
            else:
                await _tx(session)
        _debug("graph_insert_tool_output", session_id=session_id, tool=tool_name)
        return row_id
    except Exception as exc:
        _debug("graph_insert_tool_output_error", session_id=session_id, error=str(exc))
        return None


async def insert_checkpoint(
    session_id: str,
    working_memory: Dict[str, Any],
    rolling_summary: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert a memory checkpoint into Neo4j."""
    if not _pool_available():
        return None
    try:
        import uuid as _uuid

        row_id = str(_uuid.uuid4())
        meta_json = json.dumps(metadata or {})
        wm_json = json.dumps(working_memory)

        cypher = """
        MERGE (c:MemoryCheckpoint {id: $id})
        SET c.session_id = $session_id,
            c.working_memory = $working_memory,
            c.rolling_summary = $rolling_summary,
            c.metadata = $metadata,
            c.created_at = $created_at
        WITH c
        MERGE (sess:Session {id: $session_id})
        MERGE (sess)-[:HAS_CHECKPOINT]->(c)
        """
        driver = graph_bootstrap.get_driver()
        if not driver:
            return None
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:

            async def _tx(tx):
                result = await tx.run(
                    cypher,
                    id=row_id,
                    session_id=session_id,
                    working_memory=wm_json,
                    rolling_summary=rolling_summary,
                    metadata=meta_json,
                    created_at=time.time(),
                    timeout=_NEO4J_WRITE_TIMEOUT_S,
                )
                await result.consume()

            if hasattr(session, "execute_write"):
                await session.execute_write(_tx)
            else:
                await _tx(session)
        _debug("graph_insert_checkpoint", session_id=session_id)
        return row_id
    except Exception as exc:
        _debug("graph_insert_checkpoint_error", session_id=session_id, error=str(exc))
        return None


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
    if not _pg_pool_available():
        return False
    if not isinstance(vector, list) or len(vector) != _EXPECTED_EMBEDDING_DIM:
        _debug("pg_insert_ce_dim_mismatch", project_id=project_id)
        return False
    try:
        vec_str = "[" + ",".join(str(v) for v in vector) + "]"
        meta_json = json.dumps(metadata or {})
        async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
            await conn.execute(
                """
                INSERT INTO codebase_embeddings
                  (chunk_id, project_id, file_path, ref_type, chunk_index,
                   content, embedding, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb, to_timestamp(%s))
                ON CONFLICT (chunk_id) DO NOTHING
                """,
                (
                    chunk_id,
                    project_id,
                    file_path,
                    ref_type,
                    chunk_index,
                    content,
                    vec_str,
                    meta_json,
                    time.time(),
                ),
            )
        return True
    except Exception as exc:
        _debug(
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
    if not _ENABLE_EMBEDDINGS or not batch:
        return 0

    try:
        import hashlib

        if project_path and not project_id:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]

        # ── Phase 1: Postgres batch upsert (single connection, one round-trip) ─
        written = 0
        if _pg_pool_available():
            _now = time.time()
            rows = []
            for item in batch:
                chunk_id = item["ref_id"]
                meta = item.get("metadata", {})
                file_path = meta.get("file", "") if isinstance(meta, dict) else ""
                chunk_idx = int(chunk_id.split("::")[-1]) if "::" in chunk_id else 0
                vec = item.get("vector", [])

                if not isinstance(vec, list) or len(vec) != _EXPECTED_EMBEDDING_DIM:
                    continue  # skip malformed vectors

                vec_str = "[" + ",".join(str(v) for v in vec) + "]"
                meta_json = json.dumps(meta if isinstance(meta, dict) else {})
                rows.append(
                    (
                        chunk_id,
                        project_id,
                        file_path,
                        item.get("ref_type", "code_chunk"),
                        chunk_idx,
                        item["text"],
                        vec_str,
                        meta_json,
                        _now,
                    )
                )

            if rows:
                async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
                    async with conn.cursor() as cur:
                        await cur.executemany(
                            """
                            INSERT INTO codebase_embeddings
                              (chunk_id, project_id, file_path, ref_type, chunk_index,
                               content, embedding, metadata, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb, to_timestamp(%s))
                            ON CONFLICT (chunk_id) DO NOTHING
                            """,
                            rows,
                        )
                written = len(rows)
        else:
            # No Postgres — fall back to Neo4j full write
            return await _neo4j_insert_embeddings_batch(session_id, project_id, batch)

        # ── Phase 2: Neo4j lightweight Chunk reference nodes ─────────────────
        # No embedding or text stored — just the id for graph traversal.
        if _pool_available():
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

        _debug("insert_embeddings_batch", session_id=session_id, count=written)
        return written

    except Exception as exc:
        _debug("insert_embeddings_batch_error", session_id=session_id, error=str(exc))
        return 0


async def _neo4j_insert_embeddings_batch(
    session_id: str,
    project_id: str,
    batch: List[Dict[str, Any]],
) -> int:
    """Fallback: write full chunks to Neo4j when Postgres is unavailable."""
    if not _ENABLE_EMBEDDINGS or not _pool_available() or not batch:
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
        _debug("neo4j_fallback_error", session_id=session_id, error=str(exc))
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
    if not _ENABLE_EMBEDDINGS or not _pool_available():
        return None

    if not isinstance(vector, list) or not vector:
        _debug(
            "graph_insert_embedding_invalid_vector",
            session_id=session_id,
            ref_type=ref_type,
        )
        return None

    actual_dim = len(vector)
    if actual_dim != _EXPECTED_EMBEDDING_DIM:
        _debug(
            "graph_insert_embedding_dim_mismatch",
            session_id=session_id,
            ref_type=ref_type,
            expected_dim=_EXPECTED_EMBEDDING_DIM,
            actual_dim=actual_dim,
        )
        return None
    try:
        import uuid as _uuid
        # row_id = str(_uuid.uuid4()) # This line is removed as ref_id is already a parameter
        # meta_json = json.dumps(metadata or {}) # This is moved directly into session.run

        import hashlib  # Added for project_id calculation

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
            await _neo4j_write(
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
        _debug(
            "graph_insert_embedding_ok", session_id=session_id, project_id=project_id
        )
        return ref_id
    except Exception as exc:
        _debug("graph_insert_embedding_error", session_id=session_id, error=str(exc))
        return None


async def search_similar_memory(
    session_id: str,
    query_vector: List[float],
    k: int = 5,
    same_session_only: bool = True,
    query_text: str = "",
    global_search: bool = False,
) -> List[Dict[str, Any]]:
    """
    Hybrid retrieval: Neo4j vector search on MemoryEmbedding nodes.
    Supports global_search across all sessions/projects.
    """
    if not _ENABLE_EMBEDDINGS or not _ENABLE_RETRIEVAL or not _pool_available():
        return []

    if not isinstance(query_vector, list) or not query_vector:
        _debug("graph_search_invalid_query_vector", session_id=session_id)
        return []

    try:
        # Note: We use the node index 'memory_embeddings_vector' created earlier
        cypher = """
        CALL db.index.vector.queryNodes('memory_embeddings_vector', $k, $query_vector)
        YIELD node, score
        WHERE ($global_search OR ($same_session OR node.session_id = $sid))
        RETURN node.ref_id as ref_id, 
               node.ref_type as ref_type, 
               node.text as compact_text, 
               node.created_at as created_at, 
               score as rrf_score
        ORDER BY score DESC
        LIMIT $k
        """
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        results: List[Dict[str, Any]] = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            records = await _neo4j_read(
                session,
                cypher,
                "search_similar_memory",
                query_vector=query_vector,
                k=k,
                global_search=global_search,
                same_session=not same_session_only,
                sid=session_id,
            )
            results = [dict(record) for record in records]

        _debug(
            "graph_search_memory",
            session_id=session_id,
            k=k,
            found=len(results),
        )
        return results

    except Exception as exc:
        _debug("graph_search_similar_error", session_id=session_id, error=str(exc))
    return []


# ---------------------------------------------------------------------------
# Structured-summary durable facts helpers
# ---------------------------------------------------------------------------


def _parse_summary_json(summary_text: str) -> Dict[str, List[str]]:
    if not summary_text or not summary_text.strip():
        return {}
    try:
        parsed = json.loads(summary_text)
        if not isinstance(parsed, dict):
            return {}
        out: Dict[str, List[str]] = {}
        for key, value in parsed.items():
            if isinstance(value, list):
                out[key] = [str(v).strip() for v in value if str(v).strip()]
            elif value:
                out[key] = [str(value).strip()]
        return out
    except Exception:
        return {}


def extract_summary_facts(
    summary_text: str, max_items_per_section: Optional[int] = None
) -> List[Dict[str, str]]:
    """
    Flatten structured summary JSON into durable facts.
    Returns:
      [{"fact_type": "goals", "content": "..."}, ...]
    """
    parsed = _parse_summary_json(summary_text)
    if not parsed:
        return []

    limit = max_items_per_section or _FACTS_PER_SUMMARY_SECTION
    facts: List[Dict[str, str]] = []
    for fact_type, items in parsed.items():
        for item in items[:limit]:
            facts.append({"fact_type": fact_type, "content": item[:240]})
    return facts


async def _fact_embedding_exists(
    session_id: str, ref_type: str, compact_text: str
) -> bool:
    """Check if a durable fact exists in Neo4j."""
    if not _pool_available():
        return False
    try:
        cypher = """
        MATCH (m:MemoryEmbedding)
        WHERE m.session_id = $sid AND m.ref_type = $rtype AND m.text = $text
        RETURN 1 LIMIT 1
        """
        driver = graph_bootstrap.get_driver()
        if not driver:
            return False
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            records = await _neo4j_read(
                session,
                cypher,
                "fact_embedding_exists",
                sid=session_id,
                rtype=ref_type,
                text=compact_text,
            )
            return bool(records)
    except Exception as exc:
        _debug("graph_fact_exists_error", session_id=session_id, error=str(exc))
        return False


async def insert_summary_facts(
    session_id: str,
    summary_text: str,
    embed_text: Callable[[str], Awaitable[Optional[List[float]]]],
    metadata: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    Persist high-signal summary facts into memory_embeddings using ref_type like:
      fact:goals
      fact:decisions
      fact:errors
      ...

    This avoids a new table and makes facts immediately retrievable by the
    existing hybrid retrieval path.
    """
    if not _ENABLE_EMBEDDINGS or not _ENABLE_PERSISTENCE:
        return []

    created_ids: List[str] = []
    facts = extract_summary_facts(summary_text)
    if not facts:
        return created_ids

    for fact in facts:
        fact_type = fact["fact_type"]
        content = fact["content"]
        ref_type = f"fact:{fact_type}"

        try:
            if await _fact_embedding_exists(session_id, ref_type, content):
                continue

            vector = await embed_text(content)
            if not vector:
                continue

            row_id = await insert_embedding(
                session_id=session_id,
                ref_id=f"{session_id}:{fact_type}:{abs(hash(content))}",
                ref_type=ref_type,
                compact_text=content,
                vector=vector,
                metadata={
                    "source": "rolling_summary",
                    "fact_type": fact_type,
                    **(metadata or {}),
                },
            )
            if row_id:
                created_ids.append(row_id)
        except Exception as exc:
            _debug(
                "pg_insert_summary_fact_error",
                session_id=session_id,
                fact_type=fact_type,
                error=str(exc),
            )

    if created_ids:
        _debug("pg_insert_summary_facts", session_id=session_id, count=len(created_ids))
    return created_ids


# ---------------------------------------------------------------------------
# Codebase Search helpers
# ---------------------------------------------------------------------------


async def insert_codebase_embedding_graph(
    project_id: str,
    file_path: str,
    chunk_index: int,
    content: str,
    vector: List[float],
) -> bool:
    """Insert a codebase chunk into Neo4j with its vector embedding."""
    try:
        driver = graph_bootstrap.get_driver()
        if not driver:
            # Try to init if not already (safeguard)
            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()
            if not driver:
                return False

        # Generate a stable ID for the chunk
        chunk_id = f"{project_id}:{file_path}:{chunk_index}"
        # File ID: project_id:file:path (matches structural graph convention)
        file_id = f"{project_id}:file:{file_path}"

        cypher = """
        MERGE (chk:Chunk {id: $chk_id})
        SET chk.project_id = $pid,
            chk.file_path = $path,
            chk.chunk_index = $idx,
            chk.text = $text,
            chk.embedding = $vec
        WITH chk
        MATCH (f:File {id: $fid})
        MERGE (f)-[:HAS_CHUNK]->(chk)
        """

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await _neo4j_write(
                session,
                cypher,
                "insert_codebase_embedding_graph",
                chk_id=chunk_id,
                pid=project_id,
                path=file_path,
                idx=chunk_index,
                text=content,
                vec=vector,
                fid=file_id,
            )
        _debug(
            "neo4j_insert_chunk",
            project_id=project_id,
            file_path=file_path,
            chunk_index=chunk_index,
        )
        return True
    except Exception as exc:
        _debug(
            "neo4j_insert_chunk_error",
            project_id=project_id,
            file_path=file_path,
            error=str(exc),
        )
        return False


async def search_codebase(
    project_id: str,
    query_vector: List[float],
    query_text: str = "",
    k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Hybrid retrieval: Postgres pgvector cosine ANN + BM25 tsvector, fused via RRF.
    Falls back to Neo4j native vector search if Postgres is unavailable.
    Results are augmented with Neo4j structural graph context.
    """
    if not _ENABLE_EMBEDDINGS or not _ENABLE_RETRIEVAL:
        return []
    if not isinstance(query_vector, list) or not query_vector:
        _debug("search_codebase_invalid_vector", project_id=project_id)
        return []

    # ── Postgres primary path ─────────────────────────────────────────────────
    if _pg_pool_available():
        try:
            vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
            use_keyword = bool(query_text and query_text.strip())
            fetch = k * 4

            if use_keyword:
                sql = """
                    WITH semantic AS (
                        SELECT id, file_path, chunk_index, content, metadata, created_at,
                               ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rank
                        FROM codebase_embeddings
                        WHERE project_id = %(pid)s
                        LIMIT %(fetch)s
                    ),
                    keyword AS (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   ORDER BY ts_rank(search_vec,
                                       plainto_tsquery('english', %(qt)s)) DESC
                               ) AS rank
                        FROM codebase_embeddings
                        WHERE project_id = %(pid)s
                          AND search_vec @@ plainto_tsquery('english', %(qt)s)
                        LIMIT %(fetch)s
                    ),
                    fused AS (
                        SELECT s.file_path, s.chunk_index, s.content, s.metadata, s.created_at,
                               (1.0 / (60 + s.rank)
                                + COALESCE(1.0 / (60 + k_tbl.rank), 0.0)) AS rrf_score
                        FROM semantic s
                        LEFT JOIN keyword k_tbl ON s.id = k_tbl.id
                    )
                    SELECT file_path, chunk_index, content, metadata, created_at, rrf_score
                    FROM fused
                    ORDER BY rrf_score DESC
                    LIMIT %(k)s
                """
                params: Dict[str, Any] = {
                    "vec": vec_str,
                    "qt": query_text,
                    "pid": project_id,
                    "fetch": fetch,
                    "k": k,
                }
            else:
                sql = """
                    SELECT file_path, chunk_index, content, metadata, created_at,
                           (1.0 / (60 + ROW_NUMBER() OVER (
                               ORDER BY embedding <=> %(vec)s::vector
                           ))) AS rrf_score
                    FROM codebase_embeddings
                    WHERE project_id = %(pid)s
                    ORDER BY embedding <=> %(vec)s::vector
                    LIMIT %(k)s
                """
                params = {"vec": vec_str, "pid": project_id, "k": k}

            results: List[Dict[str, Any]] = []
            async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cur:
                    await cur.execute(sql, params)
                    async for row in cur:
                        results.append(
                            {
                                "file_path": row[0],
                                "chunk_index": row[1],
                                "content": row[2],
                                "metadata": row[3],
                                "created_at": row[4],
                                "rrf_score": float(row[5]),
                            }
                        )

            _debug(
                "pg_search_codebase",
                project_id=project_id,
                k=k,
                keyword_arm=use_keyword,
                found=len(results),
            )
            return await _augment_with_graph_context(project_id, results)

        except Exception as exc:
            _debug("pg_search_codebase_error", project_id=project_id, error=str(exc))
            # fall through to Neo4j

    # ── Neo4j fallback (when Postgres unavailable) ────────────────────────────
    try:
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []
        cypher = """
        CALL db.index.vector.queryNodes('codebase_chunks_vector', $k, $vec)
        YIELD node, score
        WHERE node.project_id = $pid
        RETURN node.metadata AS metadata_json, node.text AS content,
               score AS rrf_score, '' AS file_path, 0 AS chunk_index
        """
        results = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
            res = await s.run(cypher, k=k * 2, vec=query_vector, pid=project_id)
            async for record in res:
                results.append(dict(record))
        results = results[:k]
        _debug(
            "neo4j_search_codebase_fallback", project_id=project_id, found=len(results)
        )
        return await _augment_with_graph_context(project_id, results)
    except Exception as exc:
        _debug("neo4j_search_codebase_error", project_id=project_id, error=str(exc))
    return []


async def _augment_with_graph_context(
    project_id: str,
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Expand semantic results with Neo4j structural graph context.

    For each retrieved file, looks up its symbols and their outgoing
    CALLS/INHERITS relationships and prepends them to the chunk content.
    File node IDs use the current format: "project_id:file:rel_path".
    """
    try:
        if not graph_bootstrap._NEO4J_ENABLED:
            return results
        driver = graph_bootstrap.get_driver()
        if not driver:
            return results

        # Current file node ID format: "project_id:file:rel_path"
        file_node_ids = list(
            set(
                f"{project_id}:file:{r['file_path']}"
                for r in results
                if r.get("file_path")
            )
        )
        if not file_node_ids:
            return results

        cypher = """
        UNWIND $file_ids AS fid
        MATCH (f:File {id: fid})-[:CONTAINS]->(node)
        OPTIONAL MATCH (node)-[rel:CALLS|INHERITS]->(target)
        RETURN f.filepath AS file_path,
               labels(node)[0] AS node_type,
               node.name AS node_name,
               type(rel) AS rel_type,
               target.name AS target_name
        """
        graph_data: Dict[str, Dict[str, list]] = {}
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
            res = await s.run(cypher, file_ids=file_node_ids)
            async for record in res:
                path = record["file_path"]
                if not path:
                    continue
                graph_data.setdefault(path, {})
                key = f"[{record['node_type']}] {record['node_name']}"
                graph_data[path].setdefault(key, [])
                if record["rel_type"] and record["target_name"]:
                    graph_data[path][key].append(
                        f"{record['rel_type']} -> {record['target_name']}"
                    )

        for r in results:
            path = r.get("file_path", "")
            if path in graph_data and graph_data[path]:
                lines = [f"=== GraphRAG: {path} ==="]
                for node_key, rels in graph_data[path].items():
                    lines.append(f" * {node_key}")
                    for rel in set(rels):
                        lines.append(f"    - {rel}")
                lines.append("=" * 35)
                r["content"] = "\n".join(lines) + "\n\n" + r.get("content", "")

        return results

    except Exception as exc:
        _debug("graph_rag_augmentation_error", error=str(exc))
        return results


async def get_project_preferences(project_id: str) -> List[str]:
    """Retrieve UserPreference strings linked to the current Project."""
    try:
        import graph_bootstrap

        if not graph_bootstrap._NEO4J_ENABLED:
            return []
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        cypher = """
        MATCH (proj:Project {id: $pid})-[:PREFERS_ENV]->(pref:UserPreference)
        RETURN pref.instruction AS instruction
        """

        prefs = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            records = await _neo4j_read(
                session, cypher, "get_project_preferences", pid=project_id
            )
            for record in records:
                if record.get("instruction"):
                    prefs.append(record["instruction"])
        return prefs
    except Exception:
        return []


async def get_global_instructions() -> List[str]:
    """Retrieve floating Instruction nodes for general guidance."""
    try:
        import graph_bootstrap

        if not graph_bootstrap._NEO4J_ENABLED:
            return []
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        cypher = """
        MATCH (i:Instruction)
        RETURN i.text AS text
        """

        instructions = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            records = await _neo4j_read(session, cypher, "get_global_instructions")
            for record in records:
                if record.get("text"):
                    instructions.append(record["text"])
        return instructions
    except Exception:
        return []


async def add_durable_memory(
    session_id: str, text: str, is_global: bool = False
) -> bool:
    """
    Store a durable memory or instruction.
    If is_global is True, creates a floating Instruction node.
    Otherwise, creates a UserPreference node linked to the current project.
    """
    try:
        import graph_bootstrap

        if not graph_bootstrap._NEO4J_ENABLED:
            return False
        driver = graph_bootstrap.get_driver()
        if not driver:
            return False

        # Extract project_id from session_id
        project_id = session_id.split(":")[0] if ":" in session_id else session_id

        if is_global:
            cypher = "CREATE (i:Instruction {text: $text, created_at: timestamp()})"
            params = {"text": text}
        else:
            # MERGE project so memories work even for non-indexed project IDs
            cypher = """
            MERGE (proj:Project {id: $pid})
            CREATE (pref:UserPreference {
                instruction: $text,
                name: 'User Added Memory',
                created_at: timestamp()
            })
            MERGE (proj)-[:PREFERS_ENV]->(pref)
            RETURN pref
            """
            params = {"pid": project_id, "text": text}

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await _neo4j_write(session, cypher, "add_durable_memory", **params)
        return True
    except Exception as e:
        _debug("add_durable_memory_error", error=str(e))
        return False


async def list_durable_memories(
    session_id: str,
    include_global: bool = False,
) -> list[dict]:
    """
    List all durable memories stored for a session/project.

    Returns a list of dicts with keys: text, is_global, created_at.
    Sorted newest-first.
    """
    try:
        import graph_bootstrap

        if not graph_bootstrap._NEO4J_ENABLED:
            return []
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        project_id = session_id.split(":")[0] if ":" in session_id else session_id
        memories: list[dict] = []

        # Session-scoped memories (UserPreference nodes)
        pref_cypher = """
        MATCH (proj:Project {id: $pid})-[:PREFERS_ENV]->(pref:UserPreference)
        RETURN pref.instruction AS text, pref.created_at AS created_at
        ORDER BY pref.created_at DESC
        """
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
            records = await _neo4j_read(
                s, pref_cypher, "list_durable_memories_pref", pid=project_id
            )
            for record in records:
                memories.append(
                    {
                        "text": record.get("text"),
                        "is_global": False,
                        "created_at": record.get("created_at"),
                    }
                )

        # Global memories (Instruction nodes)
        if include_global:
            inst_cypher = """
            MATCH (i:Instruction)
            RETURN i.text AS text, i.created_at AS created_at
            ORDER BY i.created_at DESC
            """
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
                records = await _neo4j_read(
                    s, inst_cypher, "list_durable_memories_global"
                )
                for record in records:
                    memories.append(
                        {
                            "text": record.get("text"),
                            "is_global": True,
                            "created_at": record.get("created_at"),
                        }
                    )

        # Sort newest-first across both sources
        memories.sort(key=lambda m: m.get("created_at") or 0, reverse=True)
        return memories

    except Exception as e:
        _debug("list_durable_memories_error", error=str(e))
        return []

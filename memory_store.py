"""memory_store.py – Redis hot-state and Postgres durable storage for the memory layer.

Design:
- Redis stores rolling summary, structured working memory, and recent turn list.
- Postgres stores durable conversation_turns, memory_summaries, tool_outputs,
  memory_checkpoints, and memory_embeddings.
- All operations are best-effort: exceptions are caught and logged, never propagated.
- Postgres connections are managed via psycopg_pool.AsyncConnectionPool (min=1, max=4).
  The pool is opened lazily on first use and re-used across requests.

Upgrades:
- Rolling summaries can now be structured JSON strings.
- Helper utilities to parse summary facts and store durable fact embeddings
  without adding a new table.
- Duplicate fact suppression for memory_embeddings.
- Hybrid Neo4j GraphRAG context injection for codebase searches.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_ENABLE_MEMORY = os.getenv("LM_PROXY_MEMORY_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_REDIS = os.getenv("LM_PROXY_MEMORY_ENABLE_REDIS", "1").strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_PERSISTENCE = os.getenv("LM_PROXY_MEMORY_ENABLE_PERSISTENCE", "1").strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_RETRIEVAL = os.getenv("LM_PROXY_MEMORY_ENABLE_RETRIEVAL", "1").strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_EMBEDDINGS = os.getenv("LM_PROXY_MEMORY_ENABLE_EMBEDDINGS", "0").strip().lower() in {"1", "true", "yes", "on"}
_EXPECTED_EMBEDDING_DIM = int(os.getenv("LM_PROXY_MEMORY_EMBEDDING_DIM", "768"))

_REDIS_URL = os.getenv("LM_PROXY_REDIS_URL", "redis://localhost:6379/0")
_PG_DSN = os.getenv("LM_PROXY_PG_DSN", "")
_SESSION_NAMESPACE = os.getenv("LM_PROXY_MEMORY_SESSION_NAMESPACE", "lmproxy")
_MAX_RECENT_TURNS = int(os.getenv("LM_PROXY_MEMORY_MAX_RECENT_TURNS", "20"))
_FACTS_PER_SUMMARY_SECTION = int(os.getenv("LM_PROXY_MEMORY_FACTS_PER_SECTION", "4"))
_ENABLE_DEBUG = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}

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
            flush=True,
        )
    except Exception:
        print(f"[lm-proxy:memory_store] {message} {fields}", flush=True)


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


async def set_file_skeleton(session_id: str, file_path_hash: str, skeleton: str) -> None:
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
# Postgres helpers – backed by psycopg_pool.AsyncConnectionPool
# ---------------------------------------------------------------------------
_pg_pool: Optional[Any] = None  # psycopg_pool.AsyncConnectionPool

_PG_POOL_MIN = int(os.getenv("LM_PROXY_MEMORY_PG_POOL_MIN", "1"))
_PG_POOL_MAX = int(os.getenv("LM_PROXY_MEMORY_PG_POOL_MAX", "4"))


async def open_pool() -> None:
    """
    Open the Postgres connection pool.
    Called once from the proxy startup event; safe to call again (no-op if already open).
    Silently skips if persistence is disabled or DSN is not configured.
    """
    global _pg_pool
    if not _ENABLE_PERSISTENCE or not _PG_DSN:
        return
    if _pg_pool is not None:
        return
    try:
        from psycopg_pool import AsyncConnectionPool  # type: ignore
        _pg_pool = AsyncConnectionPool(
            conninfo=_PG_DSN,
            min_size=_PG_POOL_MIN,
            max_size=_PG_POOL_MAX,
            open=False,
            kwargs={"autocommit": True},
        )
        await _pg_pool.open(wait=True, timeout=10)
        _debug("postgres_pool_opened", dsn=_PG_DSN[:40], min=_PG_POOL_MIN, max=_PG_POOL_MAX)
        
        # Also initialize GraphRAG database if enabled
        try:
            import graph_bootstrap
            await graph_bootstrap.init_graph_db()
        except ImportError:
            pass
            
    except Exception as exc:
        _debug("postgres_pool_open_failed", error=str(exc))
        _pg_pool = None


def _pool_available() -> bool:
    return _ENABLE_PERSISTENCE and _PG_DSN != "" and _pg_pool is not None


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
) -> Optional[str]:
    """Insert a conversation turn; returns its generated ID or None on failure."""
    if not _pool_available():
        return None
    try:
        import uuid as _uuid
        row_id = str(_uuid.uuid4())
        meta_json = json.dumps(metadata or {})
        async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
            await conn.execute(
                """
                INSERT INTO conversation_turns
                  (id, session_id, turn_index, role, content, compact_content,
                   model, tool_name, tool_call_id, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, to_timestamp(%s))
                """,
                (row_id, session_id, turn_index, role, content, compact_content,
                 model, tool_name, tool_call_id, meta_json, time.time()),
            )
        _debug("pg_insert_turn", session_id=session_id, role=role, turn_index=turn_index)
        return row_id
    except Exception as exc:
        _debug("pg_insert_turn_error", session_id=session_id, error=str(exc))
        return None


async def insert_summary(
    session_id: str,
    summary_text: str,
    summary_type: str = "rolling",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert a memory summary record."""
    if not _pool_available():
        return None
    try:
        import uuid as _uuid
        row_id = str(_uuid.uuid4())
        meta_json = json.dumps(metadata or {})
        async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
            await conn.execute(
                """
                INSERT INTO memory_summaries
                  (id, session_id, summary_text, summary_type, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, to_timestamp(%s))
                """,
                (row_id, session_id, summary_text, summary_type, meta_json, time.time()),
            )
        _debug("pg_insert_summary", session_id=session_id, type=summary_type)
        return row_id
    except Exception as exc:
        _debug("pg_insert_summary_error", session_id=session_id, error=str(exc))
        return None


async def insert_tool_output(
    session_id: str,
    tool_name: str,
    tool_call_id: str,
    raw_output: str,
    compact_output: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert a raw + compact tool output record."""
    if not _pool_available():
        return None
    try:
        import uuid as _uuid
        row_id = str(_uuid.uuid4())
        meta_json = json.dumps(metadata or {})
        async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
            await conn.execute(
                """
                INSERT INTO tool_outputs
                  (id, session_id, tool_name, tool_call_id, raw_output, compact_output, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, to_timestamp(%s))
                """,
                (row_id, session_id, tool_name, tool_call_id, raw_output, compact_output, meta_json, time.time()),
            )
        _debug("pg_insert_tool_output", session_id=session_id, tool=tool_name)
        return row_id
    except Exception as exc:
        _debug("pg_insert_tool_output_error", session_id=session_id, error=str(exc))
        return None


async def insert_checkpoint(
    session_id: str,
    working_memory: Dict[str, Any],
    rolling_summary: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert a memory checkpoint (structured state + rolling summary snapshot)."""
    if not _pool_available():
        return None
    try:
        import uuid as _uuid
        row_id = str(_uuid.uuid4())
        meta_json = json.dumps(metadata or {})
        wm_json = json.dumps(working_memory)
        async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
            await conn.execute(
                """
                INSERT INTO memory_checkpoints
                  (id, session_id, working_memory, rolling_summary, metadata, created_at)
                VALUES (%s, %s, %s::jsonb, %s, %s::jsonb, to_timestamp(%s))
                """,
                (row_id, session_id, wm_json, rolling_summary, meta_json, time.time()),
            )
        _debug("pg_insert_checkpoint", session_id=session_id)
        return row_id
    except Exception as exc:
        _debug("pg_insert_checkpoint_error", session_id=session_id, error=str(exc))
        return None


async def insert_embedding(
    session_id: str,
    ref_id: str,
    ref_type: str,
    compact_text: str,
    vector: List[float],
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert a vector embedding record."""
    if not _ENABLE_EMBEDDINGS or not _pool_available():
        return None

    if not isinstance(vector, list) or not vector:
        _debug("pg_insert_embedding_invalid_vector", session_id=session_id, ref_type=ref_type)
        return None

    actual_dim = len(vector)
    if actual_dim != _EXPECTED_EMBEDDING_DIM:
        _debug(
            "pg_insert_embedding_dim_mismatch",
            session_id=session_id,
            ref_type=ref_type,
            expected_dim=_EXPECTED_EMBEDDING_DIM,
            actual_dim=actual_dim,
        )
        return None
    try:
        import uuid as _uuid
        row_id = str(_uuid.uuid4())
        meta_json = json.dumps(metadata or {})
        vector_str = "[" + ",".join(str(v) for v in vector) + "]"
        async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
            await conn.execute(
                """
                INSERT INTO memory_embeddings
                  (id, session_id, ref_id, ref_type, compact_text, embedding, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s::vector, %s::jsonb, to_timestamp(%s))
                """,
                (row_id, session_id, ref_id, ref_type, compact_text, vector_str, meta_json, time.time()),
            )
        _debug("pg_insert_embedding", session_id=session_id, ref_type=ref_type)
        return row_id
    except Exception as exc:
        _debug("pg_insert_embedding_error", session_id=session_id, error=str(exc))
        return None


async def search_similar_memory(
    session_id: str,
    query_vector: List[float],
    k: int = 5,
    same_session_only: bool = True,
    query_text: str = "",
) -> List[Dict[str, Any]]:
    """
    Hybrid retrieval: pgvector cosine similarity + Postgres full-text search,
    fused via Reciprocal Rank Fusion (RRF).

    Both arms are run over memory_embeddings:
      - Semantic arm: cosine distance on the embedding column.
      - Keyword arm:  ts_rank on the search_vec generated tsvector column.
        Only active when query_text is provided and non-empty.

    RRF formula: score = 1/(60 + semantic_rank) + 1/(60 + keyword_rank)
    The keyword arm contributes 0 for rows with no keyword match (LEFT JOIN).

    Returns list of dicts with keys:
      ref_id, ref_type, compact_text, rrf_score, created_at
    Ordered by rrf_score descending (best first).

    Falls back to empty list on any error.
    """
    if not _ENABLE_EMBEDDINGS or not _ENABLE_RETRIEVAL or not _pool_available():
        return []

    if not isinstance(query_vector, list) or not query_vector:
        _debug("pg_search_invalid_query_vector", session_id=session_id)
        return []

    actual_dim = len(query_vector)
    if actual_dim != _EXPECTED_EMBEDDING_DIM:
        _debug(
            "pg_search_dim_mismatch",
            session_id=session_id,
            expected_dim=_EXPECTED_EMBEDDING_DIM,
            actual_dim=actual_dim,
        )
        return []
    try:
        vector_str = "[" + ",".join(str(v) for v in query_vector) + "]"
        session_clause = "AND session_id = %(sid)s" if same_session_only else ""

        use_keyword = bool(query_text and query_text.strip())

        if use_keyword:
            sql = f"""
                WITH semantic AS (
                    SELECT id, ref_id, ref_type, compact_text, created_at,
                           ROW_NUMBER() OVER (
                               ORDER BY embedding <=> %(vec)s::vector
                           ) AS rank
                    FROM memory_embeddings
                    WHERE 1=1 {session_clause}
                    LIMIT %(limit)s
                ),
                keyword AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               ORDER BY ts_rank(search_vec,
                                   plainto_tsquery('english', %(qt)s)) DESC
                           ) AS rank
                    FROM memory_embeddings
                    WHERE 1=1 {session_clause}
                      AND search_vec @@ plainto_tsquery('english', %(qt)s)
                    LIMIT %(limit)s
                ),
                fused AS (
                    SELECT s.ref_id, s.ref_type, s.compact_text, s.created_at,
                           (1.0 / (60 + s.rank)
                            + COALESCE(1.0 / (60 + k.rank), 0.0)) AS rrf_score
                    FROM semantic s
                    LEFT JOIN keyword k ON s.id = k.id
                )
                SELECT ref_id, ref_type, compact_text, created_at, rrf_score
                FROM fused
                ORDER BY rrf_score DESC
                LIMIT %(k)s
            """
            params: Dict[str, Any] = {
                "vec": vector_str,
                "qt": query_text,
                "sid": session_id,
                "limit": k * 4,
                "k": k,
            }
        else:
            sql = f"""
                SELECT ref_id, ref_type, compact_text, created_at,
                       (1.0 / (60 + ROW_NUMBER() OVER (
                           ORDER BY embedding <=> %(vec)s::vector
                       ))) AS rrf_score
                FROM memory_embeddings
                WHERE 1=1 {session_clause}
                ORDER BY embedding <=> %(vec)s::vector
                LIMIT %(k)s
            """
            params = {"vec": vector_str, "sid": session_id, "k": k}

        results: List[Dict[str, Any]] = []
        async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                async for row in cur:
                    results.append({
                        "ref_id": str(row[0]),
                        "ref_type": row[1],
                        "compact_text": row[2],
                        "created_at": row[3],
                        "rrf_score": float(row[4]),
                    })

        _debug(
            "pg_search_hybrid",
            session_id=session_id,
            k=k,
            keyword_arm=use_keyword,
            found=len(results),
        )
        return results

    except Exception as exc:
        _debug("pg_search_similar_error", session_id=session_id, error=str(exc))
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


def extract_summary_facts(summary_text: str, max_items_per_section: Optional[int] = None) -> List[Dict[str, str]]:
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


async def _fact_embedding_exists(session_id: str, ref_type: str, compact_text: str) -> bool:
    """
    Duplicate suppression for durable facts stored in memory_embeddings.
    """
    if not _pool_available():
        return False
    try:
        async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT 1
                    FROM memory_embeddings
                    WHERE session_id = %s
                      AND ref_type = %s
                      AND compact_text = %s
                    LIMIT 1
                    """,
                    (session_id, ref_type, compact_text),
                )
                row = await cur.fetchone()
                return row is not None
    except Exception as exc:
        _debug("pg_fact_exists_error", session_id=session_id, error=str(exc))
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
# Codebase Search durable facts helpers
# ---------------------------------------------------------------------------

async def insert_codebase_embedding(
    project_id: str,
    file_path: str,
    chunk_index: int,
    content: str,
    vector: List[float],
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert a codebase chunk vector embedding record."""
    if not _ENABLE_EMBEDDINGS or not _pool_available():
        return None

    if not isinstance(vector, list) or not vector:
        _debug("pg_insert_codebase_embedding_invalid_vector", project_id=project_id, file_path=file_path)
        return None

    actual_dim = len(vector)
    if actual_dim != _EXPECTED_EMBEDDING_DIM:
        _debug(
            "pg_insert_codebase_embedding_dim_mismatch",
            project_id=project_id,
            expected_dim=_EXPECTED_EMBEDDING_DIM,
            actual_dim=actual_dim,
        )
        return None
        
    try:
        import uuid as _uuid
        row_id = str(_uuid.uuid4())
        meta_json = json.dumps(metadata or {})
        vector_str = "[" + ",".join(str(v) for v in vector) + "]"
        async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
            await conn.execute(
                """
                INSERT INTO codebase_embeddings
                  (id, project_id, file_path, chunk_index, content, embedding, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s::vector, %s::jsonb, to_timestamp(%s))
                """,
                (row_id, project_id, file_path, chunk_index, content, vector_str, meta_json, time.time()),
            )
        _debug("pg_insert_codebase_embedding", project_id=project_id, file_path=file_path, chunk_index=chunk_index)
        return row_id
    except Exception as exc:
        _debug("pg_insert_codebase_embedding_error", project_id=project_id, file_path=file_path, error=str(exc))
        return None

async def search_codebase(
    project_id: str,
    query_vector: List[float],
    query_text: str = "",
    k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Hybrid retrieval: pgvector cosine similarity + Postgres full-text search,
    fused via Reciprocal Rank Fusion (RRF), specifically targeting codebase_embeddings.
    """
    if not _ENABLE_EMBEDDINGS or not _ENABLE_RETRIEVAL or not _pool_available():
        return []

    if not isinstance(query_vector, list) or not query_vector:
        _debug("pg_codebase_search_invalid_query_vector", project_id=project_id)
        return []

    try:
        vector_str = "[" + ",".join(str(v) for v in query_vector) + "]"
        use_keyword = bool(query_text and query_text.strip())

        if use_keyword:
            sql = f"""
                WITH semantic AS (
                    SELECT id, file_path, chunk_index, content, metadata, created_at,
                           ROW_NUMBER() OVER (
                               ORDER BY embedding <=> %(vec)s::vector
                           ) AS rank
                    FROM codebase_embeddings
                    WHERE project_id = %(pid)s
                    LIMIT %(limit)s
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
                    LIMIT %(limit)s
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
                "vec": vector_str,
                "qt": query_text,
                "pid": project_id,
                "limit": k * 4,
                "k": k,
            }
        else:
            sql = f"""
                SELECT file_path, chunk_index, content, metadata, created_at,
                       (1.0 / (60 + ROW_NUMBER() OVER (
                           ORDER BY embedding <=> %(vec)s::vector
                       ))) AS rrf_score
                FROM codebase_embeddings
                WHERE project_id = %(pid)s
                ORDER BY embedding <=> %(vec)s::vector
                LIMIT %(k)s
            """
            params = {"vec": vector_str, "pid": project_id, "k": k}

        results: List[Dict[str, Any]] = []
        async with _pg_pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                async for row in cur:
                    results.append({
                        "file_path": row[0],
                        "chunk_index": row[1],
                        "content": row[2],
                        "metadata": row[3],
                        "created_at": row[4],
                        "rrf_score": float(row[5]),
                    })

        _debug(
            "pg_search_codebase",
            project_id=project_id,
            k=k,
            keyword_arm=use_keyword,
            found=len(results),
        )
        
        # ----------------------------------------------------
        # GRAPH RAG: Augment results with structural context
        # ----------------------------------------------------
        augmented_results = await _augment_with_graph_context(project_id, results)
        
        return augmented_results

    except Exception as exc:
        _debug("pg_search_codebase_error", project_id=project_id, error=str(exc))
    return []

async def _augment_with_graph_context(project_id: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fetches neighborhood subgraph from Neo4j for semantic hits."""
    try:
        import graph_bootstrap
        if not graph_bootstrap._ENABLE_GRAPH:
            return results
        driver = graph_bootstrap.get_driver()
        if not driver:
            return results
            
        file_ids = list(set([f"{project_id}_{r['file_path']}" for r in results]))
        if not file_ids:
            return results
            
        # Extract the node definitions and anything they call/inherit
        cypher = """
        UNWIND $file_ids AS file_id
        MATCH (f:File {id: file_id})-[:CONTAINS]->(node)
        OPTIONAL MATCH (node)-[rel:CALLS|INHERITS]->(target)
        RETURN f.path AS file_path, 
               labels(node)[0] AS node_type, 
               node.name AS node_name, 
               type(rel) AS rel_type, 
               target.name AS target_name
        """
        
        graph_data = {}
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            res = await session.run(cypher, file_ids=file_ids)
            async for record in res:
                path = record["file_path"]
                if path not in graph_data:
                    graph_data[path] = {}
                    
                n_name = record["node_name"]
                n_type = record["node_type"]
                rel_type = record["rel_type"]
                t_name = record["target_name"]
                
                key = f"[{n_type}] {n_name}"
                if key not in graph_data[path]:
                    graph_data[path][key] = []
                    
                if rel_type and t_name:
                    graph_data[path][key].append(f"{rel_type} -> {t_name}")
                    
        # Inject the context to the top of each chunk result
        for r in results:
            path = r["file_path"]
            if path in graph_data and graph_data[path]:
                struct_lines = [f"=== GraphRAG Structure for {path} ==="]
                for node_key, rels in graph_data[path].items():
                    struct_lines.append(f" * {node_key}")
                    for rel in set(rels):
                        struct_lines.append(f"    - {rel}")
                struct_lines.append("===================================")
                
                graph_str = "\\n".join(struct_lines)
                r["content"] = f"{graph_str}\\n\\n{r['content']}"
                
        return results

    except Exception as e:
        _debug("graph_rag_augmentation_error", error=str(e))
        return results
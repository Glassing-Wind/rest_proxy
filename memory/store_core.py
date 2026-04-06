"""memory/store_core.py – Redis hot-state, Postgres (pgvector) and Neo4j storage."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import graph_bootstrap
from graphrag_core import neo4j as neo4j_utils

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
    await neo4j_utils.execute_write(
        session,
        cypher,
        op=op or "write",
        op_prefix=_NEO4J_OP_PREFIX,
        timeout_s=_NEO4J_TX_TIMEOUT,
        base_metadata=_NEO4J_META_BASE,
        **params,
    )


async def _neo4j_read(session, cypher: str, op: str, **params) -> list[dict]:
    return await neo4j_utils.execute_read(
        session,
        cypher,
        op=op or "read",
        op_prefix=_NEO4J_OP_PREFIX,
        timeout_s=_NEO4J_TX_TIMEOUT,
        base_metadata=_NEO4J_META_BASE,
        **params,
    )


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
_interlink_pool: Optional[Any] = None


async def _open_pg_pool() -> None:
    global _pg_pool, _interlink_pool
    import proxy.config as config

    if not _PG_DSN or (_pg_pool is not None and config._INTERLINK_DSN == _PG_DSN):
        return

    from psycopg_pool import AsyncConnectionPool  # type: ignore

    # Main Memory Pool
    if _pg_pool is None and _PG_DSN:
        try:
            _pg_pool = AsyncConnectionPool(
                _PG_DSN, min_size=_PG_POOL_MIN, max_size=_PG_POOL_MAX, open=False
            )
            await _pg_pool.open(wait=True, timeout=15)
            _debug("pg_pool_opened", dsn=_PG_DSN[:40])
        except Exception as exc:
            _debug("pg_pool_open_failed", error=str(exc))
            _pg_pool = None

    # Interlink Pool
    if config._INTERLINK_ENABLED:
        if config._INTERLINK_DSN == _PG_DSN:
            # Shared pool
            _interlink_pool = _pg_pool
        elif _interlink_pool is None and config._INTERLINK_DSN:
            try:
                _interlink_pool = AsyncConnectionPool(
                    config._INTERLINK_DSN, min_size=1, max_size=_PG_POOL_MAX, open=False
                )
                await _interlink_pool.open(wait=True, timeout=15)
                _debug("interlink_pool_opened", dsn=config._INTERLINK_DSN[:40])
            except Exception as exc:
                _debug("interlink_pool_open_failed", error=str(exc))
                _interlink_pool = None


def _pg_pool_available() -> bool:
    return _ENABLE_PERSISTENCE and _PG_DSN != "" and _pg_pool is not None


def _interlink_pool_available() -> bool:
    import proxy.config as config
    return config._INTERLINK_ENABLED and config._INTERLINK_DSN != "" and _interlink_pool is not None


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
    global _redis_client, _pg_pool, _interlink_pool
    try:
        if _redis_client is not None:
            await _redis_client.close()
            _redis_client = None
            _debug("redis_disconnected")
        if _pg_pool is not None:
            await _pg_pool.close()
            _pg_pool = None
        if _interlink_pool is not None and _interlink_pool != _pg_pool:
            # Only close if it's not the same object as _pg_pool (which was closed above)
            await _interlink_pool.close()
            _interlink_pool = None
            _debug("pg_pool_closed")
        await graph_bootstrap.close_graph_db()
    except Exception as exc:
        _debug("pool_close_failed", error=str(exc))


def _pool_available() -> bool:
    """Check if Neo4j is available (for graph operations)."""
    return _ENABLE_PERSISTENCE and graph_bootstrap._NEO4J_ENABLED


# ---------------------------------------------------------------------------
# Delegated modules
# ---------------------------------------------------------------------------


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
    from memory import store_graph_ops

    return await store_graph_ops.insert_turn(
        session_id,
        turn_index,
        role,
        content,
        compact_content,
        model=model,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        metadata=metadata,
        project_path=project_path,
    )


async def insert_summary(
    session_id: str,
    summary_text: str,
    summary_type: str = "rolling",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    from memory import store_graph_ops

    return await store_graph_ops.insert_summary(
        session_id,
        summary_text,
        summary_type=summary_type,
        metadata=metadata,
    )


async def insert_tool_output(
    session_id: str,
    tool_name: str,
    tool_call_id: str,
    raw_output: str,
    compact_output: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    from memory import store_graph_ops

    return await store_graph_ops.insert_tool_output(
        session_id,
        tool_name,
        tool_call_id,
        raw_output,
        compact_output,
        metadata=metadata,
    )


async def insert_checkpoint(
    session_id: str,
    working_memory: Dict[str, Any],
    rolling_summary: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    from memory import store_graph_ops

    return await store_graph_ops.insert_checkpoint(
        session_id,
        working_memory,
        rolling_summary,
        metadata=metadata,
    )


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
    from memory import store_embeddings

    return await store_embeddings.insert_codebase_embedding(
        chunk_id,
        project_id,
        file_path,
        chunk_index,
        content,
        vector,
        ref_type=ref_type,
        metadata=metadata,
    )


async def insert_embeddings_batch(
    session_id: str,
    project_id: str,
    batch: List[Dict[str, Any]],
    project_path: Optional[str] = None,
) -> int:
    from memory import store_embeddings

    return await store_embeddings.insert_embeddings_batch(
        session_id,
        project_id,
        batch,
        project_path=project_path,
    )


async def insert_embedding(
    session_id: str,
    ref_id: str,
    ref_type: str,
    compact_text: str,
    vector: List[float],
    metadata: Optional[Dict[str, Any]] = None,
    project_path: Optional[str] = None,
) -> Optional[str]:
    from memory import store_embeddings

    return await store_embeddings.insert_embedding(
        session_id,
        ref_id,
        ref_type,
        compact_text,
        vector,
        metadata=metadata,
        project_path=project_path,
    )


async def search_similar_memory(
    session_id: str,
    query_vector: List[float],
    k: int = 5,
    same_session_only: bool = True,
    query_text: str = "",
    global_search: bool = False,
) -> List[Dict[str, Any]]:
    from memory import store_search

    return await store_search.search_similar_memory(
        session_id,
        query_vector,
        k=k,
        same_session_only=same_session_only,
        query_text=query_text,
        global_search=global_search,
    )


def extract_summary_facts(
    summary_text: str, max_items_per_section: Optional[int] = None
) -> List[Dict[str, str]]:
    from memory import store_facts

    return store_facts.extract_summary_facts(
        summary_text, max_items_per_section=max_items_per_section
    )


async def insert_summary_facts(
    session_id: str,
    summary_text: str,
    embed_text: Callable[[str], Awaitable[Optional[List[float]]]],
    metadata: Optional[Dict[str, Any]] = None,
) -> List[str]:
    from memory import store_facts

    return await store_facts.insert_summary_facts(
        session_id,
        summary_text,
        embed_text,
        metadata=metadata,
    )


async def insert_codebase_embedding_graph(
    project_id: str,
    file_path: str,
    chunk_index: int,
    content: str,
    vector: List[float],
) -> bool:
    from memory import store_search

    return await store_search.insert_codebase_embedding_graph(
        project_id,
        file_path,
        chunk_index,
        content,
        vector,
    )


async def search_codebase(
    project_id: str,
    query_vector: List[float],
    query_text: str = "",
    k: int = 5,
) -> List[Dict[str, Any]]:
    from memory import store_search

    return await store_search.search_codebase(
        project_id,
        query_vector,
        query_text=query_text,
        k=k,
    )


async def get_project_preferences(project_id: str) -> List[str]:
    from memory import store_durable

    return await store_durable.get_project_preferences(project_id)


async def get_global_instructions() -> List[str]:
    from memory import store_durable

    return await store_durable.get_global_instructions()


async def add_durable_memory(
    session_id: str, text: str, is_global: bool = False
) -> bool:
    from memory import store_durable

    return await store_durable.add_durable_memory(
        session_id,
        text,
        is_global=is_global,
    )


async def list_durable_memories(
    session_id: str,
    include_global: bool = False,
) -> list[dict]:
    from memory import store_durable

    return await store_durable.list_durable_memories(
        session_id,
        include_global=include_global,
    )

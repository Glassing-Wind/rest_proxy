"""graph_bootstrap.py – Neo4j Graph Database connection and schema initialization."""

import asyncio
import json
import os
import sys
import time
from typing import Any, Optional
from neo4j import AsyncGraphDatabase, unit_of_work

_NEO4J_ENABLED = os.getenv("LM_PROXY_GRAPH_ENABLED", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_NEO4J_URI = os.getenv("LM_PROXY_NEO4J_URI", "bolt://127.0.0.1:7687")
_NEO4J_USER = os.getenv("LM_PROXY_NEO4J_USER", "neo4j")
_NEO4J_PASSWORD = os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")
_NEO4J_DB = os.getenv("LM_PROXY_NEO4J_DB", "proxy")
_EMBEDDING_DIM = int(os.getenv("LM_PROXY_MEMORY_EMBEDDING_DIM", "768"))
_TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
_TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "graph_bootstrap"}
_MAX_CONNECTION_POOL_SIZE = int(
    os.getenv("LM_PROXY_NEO4J_MAX_CONNECTION_POOL_SIZE", "100")
)
_CONNECTION_ACQUISITION_TIMEOUT = float(
    os.getenv("LM_PROXY_NEO4J_CONNECTION_ACQUISITION_TIMEOUT", "60.0")
)
_MAX_TRANSACTION_RETRY_TIME = float(
    os.getenv("LM_PROXY_NEO4J_MAX_TRANSACTION_RETRY_TIME", "30.0")
)
_LIVENESS_CHECK_TIMEOUT = float(
    os.getenv("LM_PROXY_NEO4J_LIVENESS_CHECK_TIMEOUT", "30.0")
)
_KEEP_ALIVE = os.getenv("LM_PROXY_NEO4J_KEEP_ALIVE", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_ENABLE_DEBUG = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_INIT_FAILURE_COOLDOWN_SECONDS = max(
    0.0, float(os.getenv("LM_PROXY_NEO4J_INIT_FAILURE_COOLDOWN_SECONDS", "10.0"))
)

_driver: Optional[Any] = None
_last_init_error: Optional[str] = None
_last_init_error_at: float = 0.0
_init_lock: Optional[asyncio.Lock] = None
_init_lock_loop = None


def _driver_config() -> dict[str, Any]:
    return {
        "max_connection_pool_size": max(1, _MAX_CONNECTION_POOL_SIZE),
        "connection_acquisition_timeout": max(
            1.0, _CONNECTION_ACQUISITION_TIMEOUT
        ),
        "max_transaction_retry_time": max(0.0, _MAX_TRANSACTION_RETRY_TIME),
        "liveness_check_timeout": max(0.0, _LIVENESS_CHECK_TIMEOUT),
        "keep_alive": _KEEP_ALIVE,
    }


def _debug(message: str, **fields: Any) -> None:
    if not _ENABLE_DEBUG:
        return
    payload = {"message": message}
    payload.update(fields)
    try:
        print(
            f"[lm-proxy:graph_bootstrap] {json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}",
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        print(
            f"[lm-proxy:graph_bootstrap] {message} {fields}",
            file=sys.stderr,
            flush=True,
        )


def _get_init_lock() -> asyncio.Lock:
    global _init_lock, _init_lock_loop
    loop = asyncio.get_running_loop()
    if _init_lock is None or _init_lock_loop is not loop:
        _init_lock = asyncio.Lock()
        _init_lock_loop = loop
    return _init_lock


async def init_graph_db() -> None:
    """Initialize Neo4j driver and ensure basic schema constraints exist."""
    global _driver, _last_init_error, _last_init_error_at
    async with _get_init_lock():
        if not _NEO4J_ENABLED:
            _debug("graph_disabled")
            return
        if _driver is not None:
            return
        if (
            _last_init_error
            and _INIT_FAILURE_COOLDOWN_SECONDS > 0.0
            and (time.monotonic() - _last_init_error_at) < _INIT_FAILURE_COOLDOWN_SECONDS
        ):
            _debug(
                "neo4j_init_suppressed_after_recent_failure",
                cooldown_seconds=_INIT_FAILURE_COOLDOWN_SECONDS,
                error=_last_init_error,
            )
            return

        created_driver = None
        try:
            driver_config = _driver_config()
            created_driver = AsyncGraphDatabase.driver(
                _NEO4J_URI,
                auth=(_NEO4J_USER, _NEO4J_PASSWORD),
                **driver_config,
            )
            _driver = created_driver
            # Verify connection
            await _driver.verify_connectivity()
            _last_init_error = None
            _last_init_error_at = 0.0
            _debug("neo4j_connected", uri=_NEO4J_URI, db=_NEO4J_DB, **driver_config)

            # Apply Schema Constraints using a session
            # Use single-property uniqueness (node.id) since Neo4j Community Edition
            # doesn't support composite uniqueness constraints.
            async with _driver.session(database=_NEO4J_DB) as session:

                async def _run_write(cypher: str, op: str) -> None:
                    metadata = dict(_TX_METADATA_BASE)
                    op_value = op or "write"
                    if _TX_OP_PREFIX:
                        op_value = f"{_TX_OP_PREFIX}.{op_value}"
                    metadata["op"] = op_value

                    async def _tx(tx):
                        res = await tx.run(cypher)
                        await res.consume()

                    if hasattr(session, "execute_write"):
                        await session.execute_write(
                            unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)(_tx)
                        )
                    else:
                        await unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)(_tx)(
                            session
                        )

                # Uniqueness constraints — ensure all MERGE operations use NodeUniqueIndexSeek.
                # Without these, Session/Project/Chunk MERGE falls back to NodeByLabelScan
                # (confirmed by PROFILE: full label scan + Eager on Session).
                constraints = [
                    # Global structural node identity (already exists, kept for safety)
                    "CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE",
                    # File nodes are MERGE'd by id; ensure index-backed MERGE
                    "CREATE CONSTRAINT file_id_unique IF NOT EXISTS FOR (f:File) REQUIRE f.id IS UNIQUE",
                    # Per-project lookup index for read-heavy queries
                    "CREATE INDEX node_project_id IF NOT EXISTS FOR (n:Node) ON (n.project_id)",
                    "CREATE INDEX file_project_id IF NOT EXISTS FOR (f:File) ON (f.project_id)",
                    # Session/Project: MERGE'd on every semantic batch — must use index
                    "CREATE CONSTRAINT session_id_unique IF NOT EXISTS FOR (s:Session) REQUIRE s.id IS UNIQUE",
                    "CREATE CONSTRAINT project_id_unique IF NOT EXISTS FOR (p:Project) REQUIRE p.id IS UNIQUE",
                    "CREATE CONSTRAINT memory_turn_id_unique IF NOT EXISTS FOR (t:MemoryTurn) REQUIRE t.id IS UNIQUE",
                    "CREATE CONSTRAINT memory_summary_id_unique IF NOT EXISTS FOR (s:MemorySummary) REQUIRE s.id IS UNIQUE",
                    "CREATE CONSTRAINT memory_checkpoint_id_unique IF NOT EXISTS FOR (c:MemoryCheckpoint) REQUIRE c.id IS UNIQUE",
                    "CREATE CONSTRAINT tool_output_id_unique IF NOT EXISTS FOR (o:ToolOutput) REQUIRE o.id IS UNIQUE",
                    # Chunk: dedicated label constraint for vector index alignment
                    "CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
                    # Relationship index: eliminates O(degree) edge scan in CONTAINS MERGE
                    "CREATE INDEX contains_idx IF NOT EXISTS FOR ()-[r:CONTAINS]-() ON (r.project_id)",
                ]

                for query in constraints:
                    try:
                        await _run_write(query, op="schema_bootstrap")
                    except Exception as e:
                        _debug("constraint_creation_warning", error=str(e), query=query)

                # 3. Vector Index for Codebase Search
                vector_index_query = f"""
                CREATE VECTOR INDEX `codebase_chunks_vector` IF NOT EXISTS
                FOR (n:Chunk)
                ON (n.embedding)
                OPTIONS {{indexConfig: {{
                  `vector.dimensions`: {_EMBEDDING_DIM},
                  `vector.similarity_function`: 'cosine'
                }}}}
                """
                try:
                    await _run_write(vector_index_query, op="vector_index")
                    _debug("neo4j_vector_index_initialized")
                except Exception as e:
                    _debug("vector_index_creation_error", error=str(e))

                _debug("neo4j_schema_initialized")

        except Exception as exc:
            if created_driver is not None:
                try:
                    await created_driver.close()
                except Exception:
                    pass
            print(
                f"[lm-proxy:graph_bootstrap] CRITICAL: Neo4j connection failure: {exc}",
                file=sys.stderr,
            )
            _debug("neo4j_connection_error", error=str(exc))
            _last_init_error = str(exc)
            _last_init_error_at = time.monotonic()
            _driver = None


async def close_graph_db() -> None:
    """Close the Neo4j driver connection."""
    global _driver, _last_init_error, _last_init_error_at
    async with _get_init_lock():
        if _driver is not None:
            await _driver.close()
            _driver = None
            _debug("neo4j_disconnected")
        _last_init_error = None
        _last_init_error_at = 0.0


def get_driver():
    """Return the active Neo4j Async Driver instance."""
    return _driver


def get_last_init_error() -> Optional[str]:
    """Return the most recent Neo4j init/connectivity error, if any."""
    return _last_init_error


async def require_driver():
    """Initialize Neo4j if needed and return a live driver or raise a clear error."""
    await init_graph_db()
    if _driver is None:
        detail = ""
        if _last_init_error:
            detail = f" Last error: {_last_init_error}"
        raise RuntimeError(
            "Neo4j driver unavailable. Check that Neo4j is running and that "
            "LM_PROXY_NEO4J_URI / LM_PROXY_NEO4J_USER / LM_PROXY_NEO4J_PASSWORD / "
            f"LM_PROXY_NEO4J_DB are valid.{detail}"
        )
    return _driver

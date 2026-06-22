"""graph_bootstrap.py – Neo4j Graph Database connection and schema initialization."""

import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional
from neo4j import AsyncGraphDatabase, unit_of_work

try:
    from tools.brain.graph_contract import node_label, rel_type
except ModuleNotFoundError:
    _GRAPH_CONTRACT_PATH = (
        Path(__file__).resolve().parent / "tools" / "brain" / "graph_contract.py"
    )
    _GRAPH_CONTRACT_SPEC = importlib.util.spec_from_file_location(
        "_brain_graph_contract", _GRAPH_CONTRACT_PATH
    )
    _GRAPH_CONTRACT = importlib.util.module_from_spec(_GRAPH_CONTRACT_SPEC)
    assert _GRAPH_CONTRACT_SPEC and _GRAPH_CONTRACT_SPEC.loader
    _GRAPH_CONTRACT_SPEC.loader.exec_module(_GRAPH_CONTRACT)
    node_label = _GRAPH_CONTRACT.node_label
    rel_type = _GRAPH_CONTRACT.rel_type

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

FILE_LABEL = node_label("file")
REL_CONTAINS = rel_type("contains")

_SCHEMA_CONSTRAINTS = (
    (
        "node_id_unique",
        "CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE",
    ),
    (
        "file_id_unique",
        f"CREATE CONSTRAINT file_id_unique IF NOT EXISTS FOR (f:{FILE_LABEL}) REQUIRE f.id IS UNIQUE",
    ),
    (
        "session_id_unique",
        "CREATE CONSTRAINT session_id_unique IF NOT EXISTS FOR (s:Session) REQUIRE s.id IS UNIQUE",
    ),
    (
        "project_id_unique",
        "CREATE CONSTRAINT project_id_unique IF NOT EXISTS FOR (p:Project) REQUIRE p.id IS UNIQUE",
    ),
    (
        "memory_turn_id_unique",
        "CREATE CONSTRAINT memory_turn_id_unique IF NOT EXISTS FOR (t:MemoryTurn) REQUIRE t.id IS UNIQUE",
    ),
    (
        "memory_summary_id_unique",
        "CREATE CONSTRAINT memory_summary_id_unique IF NOT EXISTS FOR (s:MemorySummary) REQUIRE s.id IS UNIQUE",
    ),
    (
        "memory_checkpoint_id_unique",
        "CREATE CONSTRAINT memory_checkpoint_id_unique IF NOT EXISTS FOR (c:MemoryCheckpoint) REQUIRE c.id IS UNIQUE",
    ),
    (
        "tool_output_id_unique",
        "CREATE CONSTRAINT tool_output_id_unique IF NOT EXISTS FOR (o:ToolOutput) REQUIRE o.id IS UNIQUE",
    ),
    (
        "chunk_id_unique",
        "CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
    ),
)
_SCHEMA_INDEXES = (
    (
        "node_project_id",
        "CREATE INDEX node_project_id IF NOT EXISTS FOR (n:Node) ON (n.project_id)",
    ),
    (
        "node_project_name",
        "CREATE INDEX node_project_name IF NOT EXISTS FOR (n:Node) ON (n.project_id, n.name)",
    ),
    (
        "node_project_name_filepath",
        "CREATE INDEX node_project_name_filepath IF NOT EXISTS FOR (n:Node) ON (n.project_id, n.name, n.filepath)",
    ),
    (
        "node_project_qualified_name",
        "CREATE INDEX node_project_qualified_name IF NOT EXISTS FOR (n:Node) ON (n.project_id, n.qualified_name)",
    ),
    (
        "file_project_id",
        f"CREATE INDEX file_project_id IF NOT EXISTS FOR (f:{FILE_LABEL}) ON (f.project_id)",
    ),
    (
        "contains_idx",
        f"CREATE INDEX contains_idx IF NOT EXISTS FOR ()-[r:{REL_CONTAINS}]-() ON (r.project_id)",
    ),
    (
        "codebase_chunks_vector",
        f"""
        CREATE VECTOR INDEX `codebase_chunks_vector` IF NOT EXISTS
        FOR (n:Chunk)
        ON (n.embedding)
        OPTIONS {{indexConfig: {{
          `vector.dimensions`: {_EMBEDDING_DIM},
          `vector.similarity_function`: 'cosine'
        }}}}
        """,
    ),
)
_EXPECTED_CONSTRAINT_SIGNATURES = {
    "node_id_unique": ("UNIQUENESS", ("Node",), ("id",)),
    "file_id_unique": ("UNIQUENESS", (FILE_LABEL,), ("id",)),
    "session_id_unique": ("UNIQUENESS", ("Session",), ("id",)),
    "project_id_unique": ("UNIQUENESS", ("Project",), ("id",)),
    "memory_turn_id_unique": ("UNIQUENESS", ("MemoryTurn",), ("id",)),
    "memory_summary_id_unique": ("UNIQUENESS", ("MemorySummary",), ("id",)),
    "memory_checkpoint_id_unique": ("UNIQUENESS", ("MemoryCheckpoint",), ("id",)),
    "tool_output_id_unique": ("UNIQUENESS", ("ToolOutput",), ("id",)),
    "chunk_id_unique": ("UNIQUENESS", ("Chunk",), ("id",)),
}
_EXPECTED_INDEX_SIGNATURES = {
    "node_project_id": ("RANGE", "NODE", ("Node",), ("project_id",)),
    "node_project_name": ("RANGE", "NODE", ("Node",), ("project_id", "name")),
    "node_project_name_filepath": (
        "RANGE",
        "NODE",
        ("Node",),
        ("project_id", "name", "filepath"),
    ),
    "node_project_qualified_name": (
        "RANGE",
        "NODE",
        ("Node",),
        ("project_id", "qualified_name"),
    ),
    "file_project_id": ("RANGE", "NODE", (FILE_LABEL,), ("project_id",)),
    "contains_idx": ("RANGE", "RELATIONSHIP", (REL_CONTAINS,), ("project_id",)),
    "codebase_chunks_vector": (
        "VECTOR",
        "NODE",
        ("Chunk",),
        ("embedding",),
    ),
}

_driver: Optional[Any] = None
_last_init_error: Optional[str] = None
_last_init_error_at: float = 0.0
_init_lock: Optional[asyncio.Lock] = None
_init_lock_loop = None


def _driver_config() -> dict[str, Any]:
    return {
        "max_connection_pool_size": max(1, _MAX_CONNECTION_POOL_SIZE),
        "connection_acquisition_timeout": max(1.0, _CONNECTION_ACQUISITION_TIMEOUT),
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


async def _read_schema_state(session) -> tuple[list[dict], list[dict]] | None:
    """Return constraint/index descriptors, or None when introspection is unavailable."""
    metadata = dict(_TX_METADATA_BASE)
    metadata["op"] = (
        f"{_TX_OP_PREFIX}.schema_check" if _TX_OP_PREFIX else "schema_check"
    )

    async def _tx(tx):
        constraints_result = await tx.run(
            "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
            "RETURN name, type, labelsOrTypes, properties"
        )
        constraint_rows = await constraints_result.data()
        indexes_result = await tx.run(
            "SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties "
            "RETURN name, type, entityType, labelsOrTypes, properties"
        )
        index_rows = await indexes_result.data()
        return constraint_rows, index_rows

    try:
        if hasattr(session, "execute_read"):
            return await session.execute_read(
                unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)(_tx)
            )
        return await unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)(_tx)(session)
    except Exception as exc:
        _debug("neo4j_schema_introspection_failed", error=str(exc))
        return None


def _constraint_signature(row: dict) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    constraint_type = str(row.get("type") or "")
    if constraint_type in {"NODE_PROPERTY_UNIQUENESS", "UNIQUENESS"}:
        constraint_type = "UNIQUENESS"
    return (
        constraint_type,
        tuple(row.get("labelsOrTypes") or ()),
        tuple(row.get("properties") or ()),
    )


def _index_signature(
    row: dict,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    return (
        str(row.get("type") or ""),
        str(row.get("entityType") or ""),
        tuple(row.get("labelsOrTypes") or ()),
        tuple(row.get("properties") or ()),
    )


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
            and (time.monotonic() - _last_init_error_at)
            < _INIT_FAILURE_COOLDOWN_SECONDS
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

                schema_state = await _read_schema_state(session)
                constraint_signatures = {
                    _constraint_signature(row)
                    for row in (schema_state[0] if schema_state else [])
                }
                index_signatures = {
                    _index_signature(row)
                    for row in (schema_state[1] if schema_state else [])
                }
                missing_constraints = [
                    (name, query)
                    for name, query in _SCHEMA_CONSTRAINTS
                    if _EXPECTED_CONSTRAINT_SIGNATURES[name]
                    not in constraint_signatures
                ]
                missing_indexes = [
                    (name, query)
                    for name, query in _SCHEMA_INDEXES
                    if _EXPECTED_INDEX_SIGNATURES[name] not in index_signatures
                ]

                for name, query in (*missing_constraints, *missing_indexes):
                    try:
                        await _run_write(query, op="schema_bootstrap")
                    except Exception as e:
                        _debug(
                            "schema_object_creation_warning",
                            error=str(e),
                            name=name,
                        )

                if missing_constraints or missing_indexes:
                    _debug(
                        "neo4j_schema_reconciled",
                        constraints_created=len(missing_constraints),
                        indexes_created=len(missing_indexes),
                    )
                else:
                    _debug("neo4j_schema_current")

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

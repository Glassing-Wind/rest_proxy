"""graph_bootstrap.py – Neo4j Graph Database connection and schema initialization."""

import os
import json
import sys
from typing import Optional, Any
from neo4j import AsyncGraphDatabase

_NEO4J_ENABLED = os.getenv("LM_PROXY_GRAPH_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
_NEO4J_URI = os.getenv("LM_PROXY_NEO4J_URI", "bolt://localhost:7687")
_NEO4J_USER = os.getenv("LM_PROXY_NEO4J_USER", "neo4j")
_NEO4J_PASSWORD = os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")
_NEO4J_DB = os.getenv("LM_PROXY_NEO4J_DB", "proxy")
_ENABLE_DEBUG = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}

_driver: Optional[Any] = None

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
        print(f"[lm-proxy:graph_bootstrap] {message} {fields}", file=sys.stderr, flush=True)

async def init_graph_db() -> None:
    """Initialize Neo4j driver and ensure basic schema constraints exist."""
    global _driver
    if not _NEO4J_ENABLED:
        _debug("graph_disabled")
        return
    if _driver is not None:
        return

    try:
        _driver = AsyncGraphDatabase.driver(
            _NEO4J_URI,
            auth=(_NEO4J_USER, _NEO4J_PASSWORD)
        )
        # Verify connection
        await _driver.verify_connectivity()
        _debug("neo4j_connected", uri=_NEO4J_URI, db=_NEO4J_DB)
        
        # Apply Schema Constraints using a session
        # Use single-property uniqueness (node.id) since Neo4j Community Edition 
        # doesn't support composite uniqueness constraints.
        async with _driver.session(database=_NEO4J_DB) as session:
            # Single global identity constraint
            constraints = [
                "CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE",
            ]
            
            for query in constraints:
                try:
                    await session.run(query)
                except Exception as e:
                    _debug("constraint_creation_warning", error=str(e), query=query)

            # 3. Vector Index for Codebase Search
            vector_index_query = """
            CREATE VECTOR INDEX `codebase_chunks_vector` IF NOT EXISTS
            FOR (n:Chunk)
            ON (n.embedding)
            OPTIONS {indexConfig: {
              `vector.dimensions`: 768,
              `vector.similarity_function`: 'cosine'
            }}
            """
            try:
                await session.run(vector_index_query)
                _debug("neo4j_vector_index_initialized")
            except Exception as e:
                _debug("vector_index_creation_error", error=str(e))
                    
            _debug("neo4j_schema_initialized")
            
    except Exception as exc:
        print(f"[lm-proxy:graph_bootstrap] CRITICAL: Neo4j connection failure: {exc}", file=sys.stderr)
        _debug("neo4j_connection_error", error=str(exc))
        _driver = None

async def close_graph_db() -> None:
    """Close the Neo4j driver connection."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
        _debug("neo4j_disconnected")

def get_driver():
    """Return the active Neo4j Async Driver instance."""
    return _driver

"""memory/store_graph_ops.py — Neo4j graph write helpers."""

import json
import time
from typing import Any, Dict, Optional

import graph_bootstrap
from memory import store_core


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
    if not store_core._pool_available():
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
        store_core._debug(
            "graph_insert_turn", session_id=session_id, project_id=project_id
        )
        return row_id
    except Exception as exc:
        store_core._debug(
            "graph_insert_turn_error", session_id=session_id, error=str(exc)
        )
        return None


async def insert_summary(
    session_id: str,
    summary_text: str,
    summary_type: str = "rolling",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert a memory summary record into Neo4j."""
    if not store_core._pool_available():
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
        store_core._debug(
            "graph_insert_summary", session_id=session_id, type=summary_type
        )
        return row_id
    except Exception as exc:
        store_core._debug(
            "graph_insert_summary_error", session_id=session_id, error=str(exc)
        )
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
    if not store_core._pool_available():
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
                    timeout=store_core._NEO4J_WRITE_TIMEOUT_S,
                )
                await result.consume()

            if hasattr(session, "execute_write"):
                await session.execute_write(_tx)
            else:
                await _tx(session)
        store_core._debug(
            "graph_insert_tool_output", session_id=session_id, tool=tool_name
        )
        return row_id
    except Exception as exc:
        store_core._debug(
            "graph_insert_tool_output_error", session_id=session_id, error=str(exc)
        )
        return None


async def insert_checkpoint(
    session_id: str,
    working_memory: Dict[str, Any],
    rolling_summary: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert a memory checkpoint into Neo4j."""
    if not store_core._pool_available():
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
                    timeout=store_core._NEO4J_WRITE_TIMEOUT_S,
                )
                await result.consume()

            if hasattr(session, "execute_write"):
                await session.execute_write(_tx)
            else:
                await _tx(session)
        store_core._debug("graph_insert_checkpoint", session_id=session_id)
        return row_id
    except Exception as exc:
        store_core._debug(
            "graph_insert_checkpoint_error", session_id=session_id, error=str(exc)
        )
        return None

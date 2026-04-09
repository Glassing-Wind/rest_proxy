"""memory/store_graph_ops.py — Neo4j graph write helpers."""

import hashlib
import json
import time
from typing import Any, Dict, Optional

import graph_bootstrap
from memory import store_core


def _project_id_for_path(project_path: Optional[str]) -> Optional[str]:
    if not project_path:
        return None
    return hashlib.md5(project_path.encode()).hexdigest()[:12]


async def insert_turns_batch(
    rows: list[dict[str, Any]],
) -> list[str]:
    """Insert multiple conversation turns into Neo4j in one UNWIND write."""
    if not store_core._pool_available() or not rows:
        return []
    try:
        import uuid as _uuid

        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        started_at = time.perf_counter()
        now = time.time()
        prepared_rows: list[dict[str, Any]] = []
        row_ids: list[str] = []
        for row in rows:
            row_id = str(_uuid.uuid4())
            row_ids.append(row_id)
            prepared_rows.append(
                {
                    "id": row_id,
                    "session_id": row["session_id"],
                    "project_id": _project_id_for_path(row.get("project_path")),
                    "turn_index": row["turn_index"],
                    "role": row["role"],
                    "content": row["content"],
                    "compact_content": row["compact_content"],
                    "model": row.get("model"),
                    "tool_name": row.get("tool_name"),
                    "tool_call_id": row.get("tool_call_id"),
                    "metadata": json.dumps(row.get("metadata") or {}),
                    "created_at": row.get("created_at", now),
                }
            )

        cypher = """
        UNWIND $rows AS row
        MERGE (t:MemoryTurn {id: row.id})
        SET t.session_id = row.session_id,
            t.project_id = row.project_id,
            t.turn_index = row.turn_index,
            t.role = row.role,
            t.content = row.content,
            t.compact_content = row.compact_content,
            t.model = row.model,
            t.tool_name = row.tool_name,
            t.tool_call_id = row.tool_call_id,
            t.metadata = row.metadata,
            t.created_at = row.created_at
        WITH t, row
        MERGE (s:Session {id: row.session_id})
        MERGE (s)-[:HAS_TURN]->(t)
        WITH t, row
        WHERE row.project_id IS NOT NULL
        MERGE (p:Project {id: row.project_id})
        MERGE (p)-[:HAS_TURN]->(t)
        """

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await store_core._neo4j_write(
                session,
                cypher,
                "insert_turns_batch",
                rows=prepared_rows,
            )
        store_core._debug(
            "graph_insert_turns_batch",
            count=len(prepared_rows),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return row_ids
    except Exception as exc:
        store_core._debug("graph_insert_turns_batch_error", error=str(exc))
        return []


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
        row_ids = await insert_turns_batch(
            [
                {
                    "session_id": session_id,
                    "turn_index": turn_index,
                    "role": role,
                    "content": content,
                    "compact_content": compact_content,
                    "model": model,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "metadata": metadata,
                    "project_path": project_path,
                }
            ]
        )
        row_id = row_ids[0] if row_ids else None
        if not row_id:
            return None
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
        row_ids = await insert_summaries_batch(
            [
                {
                    "session_id": session_id,
                    "summary_text": summary_text,
                    "summary_type": summary_type,
                    "metadata": metadata,
                }
            ]
        )
        row_id = row_ids[0] if row_ids else None
        if not row_id:
            return None
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
        row_ids = await insert_tool_outputs_batch(
            [
                {
                    "session_id": session_id,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "raw_output": raw_output,
                    "compact_output": compact_output,
                    "metadata": metadata,
                }
            ]
        )
        row_id = row_ids[0] if row_ids else None
        if not row_id:
            return None
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
        row_ids = await insert_checkpoints_batch(
            [
                {
                    "session_id": session_id,
                    "working_memory": working_memory,
                    "rolling_summary": rolling_summary,
                    "metadata": metadata,
                }
            ]
        )
        row_id = row_ids[0] if row_ids else None
        if not row_id:
            return None
        return row_id
    except Exception as exc:
        store_core._debug(
            "graph_insert_checkpoint_error", session_id=session_id, error=str(exc)
        )
        return None


async def insert_summaries_batch(rows: list[dict[str, Any]]) -> list[str]:
    """Insert multiple summary records into Neo4j in one UNWIND write."""
    if not store_core._pool_available() or not rows:
        return []
    try:
        import uuid as _uuid

        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        started_at = time.perf_counter()
        now = time.time()
        prepared_rows: list[dict[str, Any]] = []
        row_ids: list[str] = []
        for row in rows:
            row_id = str(_uuid.uuid4())
            row_ids.append(row_id)
            prepared_rows.append(
                {
                    "id": row_id,
                    "session_id": row["session_id"],
                    "summary_text": row["summary_text"],
                    "summary_type": row.get("summary_type", "rolling"),
                    "metadata": json.dumps(row.get("metadata") or {}),
                    "created_at": row.get("created_at", now),
                }
            )

        cypher = """
        UNWIND $rows AS row
        MERGE (s:MemorySummary {id: row.id})
        SET s.session_id = row.session_id,
            s.summary_text = row.summary_text,
            s.summary_type = row.summary_type,
            s.metadata = row.metadata,
            s.created_at = row.created_at
        WITH s, row
        MERGE (sess:Session {id: row.session_id})
        MERGE (sess)-[:HAS_SUMMARY]->(s)
        """

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await store_core._neo4j_write(
                session,
                cypher,
                "insert_summaries_batch",
                rows=prepared_rows,
            )
        store_core._debug(
            "graph_insert_summaries_batch",
            count=len(prepared_rows),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return row_ids
    except Exception as exc:
        store_core._debug("graph_insert_summaries_batch_error", error=str(exc))
        return []


async def insert_tool_outputs_batch(rows: list[dict[str, Any]]) -> list[str]:
    """Insert multiple tool output records into Neo4j in one UNWIND write."""
    if not store_core._pool_available() or not rows:
        return []
    try:
        import uuid as _uuid

        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        started_at = time.perf_counter()
        now = time.time()
        prepared_rows: list[dict[str, Any]] = []
        row_ids: list[str] = []
        for row in rows:
            row_id = str(_uuid.uuid4())
            row_ids.append(row_id)
            prepared_rows.append(
                {
                    "id": row_id,
                    "session_id": row["session_id"],
                    "tool_name": row["tool_name"],
                    "tool_call_id": row["tool_call_id"],
                    "raw_output": row["raw_output"],
                    "compact_output": row["compact_output"],
                    "metadata": json.dumps(row.get("metadata") or {}),
                    "created_at": row.get("created_at", now),
                }
            )

        cypher = """
        UNWIND $rows AS row
        MERGE (o:ToolOutput {id: row.id})
        SET o.session_id = row.session_id,
            o.tool_name = row.tool_name,
            o.tool_call_id = row.tool_call_id,
            o.raw_output = row.raw_output,
            o.compact_output = row.compact_output,
            o.metadata = row.metadata,
            o.created_at = row.created_at
        WITH o, row
        MERGE (sess:Session {id: row.session_id})
        MERGE (sess)-[:PRODUCED_OUTPUT]->(o)
        """

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await store_core._neo4j_write(
                session,
                cypher,
                "insert_tool_outputs_batch",
                rows=prepared_rows,
            )
        store_core._debug(
            "graph_insert_tool_outputs_batch",
            count=len(prepared_rows),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return row_ids
    except Exception as exc:
        store_core._debug("graph_insert_tool_outputs_batch_error", error=str(exc))
        return []


async def insert_checkpoints_batch(rows: list[dict[str, Any]]) -> list[str]:
    """Insert multiple checkpoint records into Neo4j in one UNWIND write."""
    if not store_core._pool_available() or not rows:
        return []
    try:
        import uuid as _uuid

        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        started_at = time.perf_counter()
        now = time.time()
        prepared_rows: list[dict[str, Any]] = []
        row_ids: list[str] = []
        for row in rows:
            row_id = str(_uuid.uuid4())
            row_ids.append(row_id)
            prepared_rows.append(
                {
                    "id": row_id,
                    "session_id": row["session_id"],
                    "working_memory": json.dumps(row["working_memory"]),
                    "rolling_summary": row["rolling_summary"],
                    "metadata": json.dumps(row.get("metadata") or {}),
                    "created_at": row.get("created_at", now),
                }
            )

        cypher = """
        UNWIND $rows AS row
        MERGE (c:MemoryCheckpoint {id: row.id})
        SET c.session_id = row.session_id,
            c.working_memory = row.working_memory,
            c.rolling_summary = row.rolling_summary,
            c.metadata = row.metadata,
            c.created_at = row.created_at
        WITH c, row
        MERGE (sess:Session {id: row.session_id})
        MERGE (sess)-[:HAS_CHECKPOINT]->(c)
        """

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await store_core._neo4j_write(
                session,
                cypher,
                "insert_checkpoints_batch",
                rows=prepared_rows,
            )
        store_core._debug(
            "graph_insert_checkpoints_batch",
            count=len(prepared_rows),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return row_ids
    except Exception as exc:
        store_core._debug("graph_insert_checkpoints_batch_error", error=str(exc))
        return []

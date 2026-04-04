"""memory/store_facts.py — summary fact helpers."""

import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

import graph_bootstrap
from memory import store_core
from memory import store_embeddings


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

    limit = max_items_per_section or store_core._FACTS_PER_SUMMARY_SECTION
    facts: List[Dict[str, str]] = []
    for fact_type, items in parsed.items():
        for item in items[:limit]:
            facts.append({"fact_type": fact_type, "content": item[:240]})
    return facts


async def _fact_embedding_exists(
    session_id: str, ref_type: str, compact_text: str
) -> bool:
    """Check if a durable fact exists in Neo4j."""
    if not store_core._pool_available():
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
            records = await store_core._neo4j_read(
                session,
                cypher,
                "fact_embedding_exists",
                sid=session_id,
                rtype=ref_type,
                text=compact_text,
            )
            return bool(records)
    except Exception as exc:
        store_core._debug(
            "graph_fact_exists_error", session_id=session_id, error=str(exc)
        )
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
    if not store_core._ENABLE_EMBEDDINGS or not store_core._ENABLE_PERSISTENCE:
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

            row_id = await store_embeddings.insert_embedding(
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
            store_core._debug(
                "pg_insert_summary_fact_error",
                session_id=session_id,
                fact_type=fact_type,
                error=str(exc),
            )

    if created_ids:
        store_core._debug(
            "pg_insert_summary_facts", session_id=session_id, count=len(created_ids)
        )
    return created_ids

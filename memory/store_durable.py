"""memory/store_durable.py — durable memory helpers."""

from typing import List

import graph_bootstrap
from memory import store_core


def normalize_memory_metadata(
    tags: list[str] | None = None,
    category: str | None = None,
    importance: int = 3,
) -> tuple[list[str], str | None, int]:
    """Validate and normalize optional durable-memory organization metadata."""
    normalized_tags: list[str] = []
    for raw_tag in tags or []:
        tag = str(raw_tag).strip().lower()
        if not tag:
            continue
        if len(tag) > 40:
            raise ValueError("memory tags must be 40 characters or fewer")
        if tag not in normalized_tags:
            normalized_tags.append(tag)
    if len(normalized_tags) > 12:
        raise ValueError("a memory can have at most 12 tags")

    normalized_category = category.strip().lower() if isinstance(category, str) else None
    if not normalized_category:
        normalized_category = None
    if normalized_category and len(normalized_category) > 40:
        raise ValueError("memory category must be 40 characters or fewer")

    normalized_importance = int(importance)
    if not 1 <= normalized_importance <= 5:
        raise ValueError("memory importance must be between 1 and 5")
    return normalized_tags, normalized_category, normalized_importance


async def get_project_preferences(project_id: str) -> List[str]:
    """Retrieve UserPreference strings linked to the current Project."""
    try:
        if not graph_bootstrap._NEO4J_ENABLED:
            return []
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        cypher = """
        MATCH (proj:Project {id: $pid})-[:PREFERS_ENV]->(pref:UserPreference)
        RETURN pref.instruction AS instruction
        ORDER BY coalesce(pref.importance, 3) DESC, pref.created_at DESC
        """

        prefs = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            records = await store_core._neo4j_read(
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
        if not graph_bootstrap._NEO4J_ENABLED:
            return []
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        cypher = """
        MATCH (i:Instruction)
        RETURN i.text AS text
        ORDER BY coalesce(i.importance, 3) DESC, i.created_at DESC
        """

        instructions = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            records = await store_core._neo4j_read(
                session, cypher, "get_global_instructions"
            )
            for record in records:
                if record.get("text"):
                    instructions.append(record["text"])
        return instructions
    except Exception:
        return []


async def add_durable_memory(
    session_id: str,
    text: str,
    is_global: bool = False,
    tags: list[str] | None = None,
    category: str | None = None,
    importance: int = 3,
) -> bool:
    """
    Store a durable memory or instruction.
    If is_global is True, creates a floating Instruction node.
    Otherwise, creates a UserPreference node linked to the current project.
    """
    try:
        if not graph_bootstrap._NEO4J_ENABLED:
            return False
        driver = graph_bootstrap.get_driver()
        if not driver:
            return False

        # Extract project_id from session_id
        project_id = session_id.split(":")[0] if ":" in session_id else session_id
        normalized_tags, normalized_category, normalized_importance = normalize_memory_metadata(
            tags, category, importance
        )

        if is_global:
            cypher = """
            CREATE (i:Instruction {
                text: $text, created_at: timestamp(), tags: $tags,
                category: $category, importance: $importance
            })
            """
            params = {
                "text": text,
                "tags": normalized_tags,
                "category": normalized_category,
                "importance": normalized_importance,
            }
        else:
            # MERGE project so memories work even for non-indexed project IDs
            cypher = """
            MERGE (proj:Project {id: $pid})
            CREATE (pref:UserPreference {
                instruction: $text,
                name: 'User Added Memory',
                created_at: timestamp(),
                tags: $tags,
                category: $category,
                importance: $importance
            })
            MERGE (proj)-[:PREFERS_ENV]->(pref)
            RETURN pref
            """
            params = {
                "pid": project_id,
                "text": text,
                "tags": normalized_tags,
                "category": normalized_category,
                "importance": normalized_importance,
            }

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await store_core._neo4j_write(
                session, cypher, "add_durable_memory", **params
            )
        return True
    except Exception as e:
        store_core._debug("add_durable_memory_error", error=str(e))
        return False


async def durable_memory_backend_status() -> tuple[bool, str | None]:
    """
    Return whether durable memory writes/reads are currently available and, if not,
    a short human-readable reason.
    """
    try:
        if not graph_bootstrap._NEO4J_ENABLED:
            return False, "Neo4j-backed durable memory is disabled."
        try:
            await graph_bootstrap.require_driver()
            return True, None
        except Exception as exc:
            return False, str(exc)
    except Exception as exc:
        return False, str(exc)


async def list_durable_memories(
    session_id: str,
    include_global: bool = False,
    tags: list[str] | None = None,
    category: str | None = None,
    min_importance: int = 1,
) -> list[dict]:
    """
    List all durable memories stored for a session/project.

    Returns a list of dicts with keys: text, is_global, created_at.
    Sorted newest-first.
    """
    try:
        if not graph_bootstrap._NEO4J_ENABLED:
            return []
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        project_id = session_id.split(":")[0] if ":" in session_id else session_id
        normalized_tags, normalized_category, normalized_min_importance = normalize_memory_metadata(
            tags, category, min_importance
        )
        memories: list[dict] = []

        # Session-scoped memories (UserPreference nodes)
        pref_cypher = """
        MATCH (proj:Project {id: $pid})-[:PREFERS_ENV]->(pref:UserPreference)
        RETURN pref.instruction AS text, pref.created_at AS created_at,
               coalesce(pref.tags, []) AS tags, pref.category AS category,
               coalesce(pref.importance, 3) AS importance
        ORDER BY pref.created_at DESC
        """
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
            records = await store_core._neo4j_read(
                s, pref_cypher, "list_durable_memories_pref", pid=project_id
            )
            for record in records:
                memories.append(
                    {
                        "text": record.get("text"),
                        "is_global": False,
                        "created_at": record.get("created_at"),
                        "tags": record.get("tags") or [],
                        "category": record.get("category"),
                        "importance": record.get("importance") or 3,
                    }
                )

        # Global memories (Instruction nodes)
        if include_global:
            inst_cypher = """
            MATCH (i:Instruction)
            RETURN i.text AS text, i.created_at AS created_at,
                   coalesce(i.tags, []) AS tags, i.category AS category,
                   coalesce(i.importance, 3) AS importance
            ORDER BY i.created_at DESC
            """
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
                records = await store_core._neo4j_read(
                    s, inst_cypher, "list_durable_memories_global"
                )
                for record in records:
                    memories.append(
                        {
                            "text": record.get("text"),
                            "is_global": True,
                            "created_at": record.get("created_at"),
                            "tags": record.get("tags") or [],
                            "category": record.get("category"),
                            "importance": record.get("importance") or 3,
                        }
                    )

        memories = [
            memory
            for memory in memories
            if int(memory.get("importance") or 3) >= normalized_min_importance
            and (
                not normalized_category
                or memory.get("category") == normalized_category
            )
            and (
                not normalized_tags
                or all(tag in (memory.get("tags") or []) for tag in normalized_tags)
            )
        ]

        # Sort newest-first across both sources
        memories.sort(key=lambda m: m.get("created_at") or 0, reverse=True)
        return memories

    except Exception as e:
        store_core._debug("list_durable_memories_error", error=str(e))
        return []

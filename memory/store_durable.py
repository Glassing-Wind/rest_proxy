"""memory/store_durable.py — durable memory helpers."""

from typing import List

import graph_bootstrap
from memory import store_core


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
    session_id: str, text: str, is_global: bool = False
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
        memories: list[dict] = []

        # Session-scoped memories (UserPreference nodes)
        pref_cypher = """
        MATCH (proj:Project {id: $pid})-[:PREFERS_ENV]->(pref:UserPreference)
        RETURN pref.instruction AS text, pref.created_at AS created_at
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
                records = await store_core._neo4j_read(
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
        store_core._debug("list_durable_memories_error", error=str(e))
        return []

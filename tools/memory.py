"""tools/memory.py — session memory tools."""
import hashlib
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules

def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def search_memory(session_id: str, query: str, global_search: bool = False) -> str:
        """
        Search for context in the memory store for a given session.
        Set global_search=True to search across all sessions/projects.

        Args:
            session_id: The unique identifier for the session/project.
            query: The search query or current user message.
            global_search: Whether to search across all sessions/projects.
        """
        try:
            memory_store, memory_retrieval, _, _, _ = get_memory_modules()
            if memory_store._ENABLE_PERSISTENCE:
                await memory_store.open_pool()
            assembled = await memory_retrieval.assemble_memory(session_id, query, global_search=global_search)
            if assembled and assembled.assembled_text:
                return assembled.assembled_text
            return "No relevant memory found."
        except Exception as e:
            return f"Error searching memory: {str(e)}"

    @mcp.tool()
    async def add_memory(session_id: str, text: str, is_global: bool = False) -> str:
        """
        Store a durable memory or instruction. This is NOT for temporary conversation turns,
        but for persistent facts or rules that should guide the agent's behavior.

        YOU (the agent) should call this proactively — not just when the user asks.
        After discovering a non-obvious config requirement, a library gotcha, an API
        confirmation, or an architectural decision, store it here so future sessions
        on this project benefit immediately.

        Before adding, call list_memories() to check for duplicates.
        Keep text concise (1–2 sentences), factual, and actionable.

        Args:
            session_id: The unique identifier for the session/project.
            text: The instruction or fact to remember.
            is_global: If True, this memory is not tied to a project and will be visible everywhere.
        """
        try:
            memory_store, _, _, _, _ = get_memory_modules()
            if memory_store._ENABLE_PERSISTENCE:
                await memory_store.open_pool()
            success = await memory_store.add_durable_memory(session_id, text, is_global=is_global)
            if success:
                return f"Successfully added {'global ' if is_global else ''}memory: {text}"
            return "Failed to add memory."
        except Exception as e:
            return f"Error adding memory: {str(e)}"

    @mcp.tool()
    async def get_session_summary(session_id: str) -> str:
        """
        Get the current rolling summary for a session.

        Args:
            session_id: The unique identifier for the session.
        """
        try:
            memory_store, _, _, _, _ = get_memory_modules()
            summary = await memory_store.get_rolling_summary(session_id)
            if summary:
                return summary
            return "No summary available for this session."
        except Exception as e:
            return f"Error retrieving summary: {str(e)}"

    @mcp.tool()
    async def list_memories(session_id: str, include_global: bool = False) -> str:
        """
        List all durable memories stored for a session/project.
        Useful for auditing what has been remembered, or deciding what to prune.

        Args:
            session_id:      The session or project identifier.
            include_global:  If True, also include global Instruction memories
                             (visible across all sessions/projects).
        """
        try:
            memory_store, _, _, _, _ = get_memory_modules()
            if memory_store._ENABLE_PERSISTENCE:
                await memory_store.open_pool()
            memories = await memory_store.list_durable_memories(
                session_id, include_global=include_global
            )
            if not memories:
                scope = "session + global" if include_global else "session"
                return f"No memories found for {scope} '{session_id}'."
            lines = [f"Memories for '{session_id}' ({len(memories)} total):\n"]
            for i, m in enumerate(memories, 1):
                tag = "[GLOBAL]" if m["is_global"] else "[SESSION]"
                ts = m.get("created_at")
                if ts:
                    from datetime import datetime
                    ts_str = f"  ({datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')})"
                else:
                    ts_str = ""
                lines.append(f"{i}. {tag} {m['text']}{ts_str}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing memories: {str(e)}"

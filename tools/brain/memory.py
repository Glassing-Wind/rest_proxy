"""tools/memory.py — session memory tools."""
import hashlib
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules

def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def search_memory(workspace_id: str, query: str, global_search: bool = False) -> str:
        """
        Search for context in the memory store for a given workspace.
        Set global_search=True to search across all workspaces/projects.

        Args:
            workspace_id: The unique identifier for the workspace/project.
            query: The search query or current user message.
            global_search: Whether to search across all workspaces/projects.
        """
        try:
            memory_store, memory_retrieval, _, _, _ = get_memory_modules()
            if memory_store._ENABLE_PERSISTENCE:
                await memory_store.open_pool()
            assembled = await memory_retrieval.assemble_memory(workspace_id, query, global_search=global_search)
            if assembled and assembled.assembled_text:
                return assembled.assembled_text
            return "No relevant memory found."
        except Exception as e:
            return f"Error searching memory: {str(e)}"

    @mcp.tool()
    async def add_memory(workspace_id: str, text: str, is_global: bool = False) -> str:
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
            workspace_id: The unique identifier for the workspace/project.
            text: The instruction or fact to remember.
            is_global: If True, this memory is not tied to a project and will be visible everywhere.
        """
        try:
            memory_store, _, _, _, _ = get_memory_modules()
            if memory_store._ENABLE_PERSISTENCE:
                await memory_store.open_pool()
            success = await memory_store.add_durable_memory(workspace_id, text, is_global=is_global)
            if success:
                return f"Successfully added {'global ' if is_global else ''}memory: {text}"
            return "Failed to add memory."
        except Exception as e:
            return f"Error adding memory: {str(e)}"

    @mcp.tool()
    async def get_session_summary(session_id: str) -> str:
        """
        Backward-compatible summary lookup for a session/project identifier.

        Args:
            session_id: The unique identifier for the session or workspace.
        """
        try:
            memory_store, _, _, _, _ = get_memory_modules()
            if memory_store._ENABLE_PERSISTENCE:
                await memory_store.open_pool()
            summary = await memory_store.get_rolling_summary(session_id)
            if summary:
                return summary
            return "No summary available for this session."
        except Exception as e:
            return f"Error retrieving summary: {str(e)}"

    @mcp.tool()
    async def list_memories(workspace_id: str, include_global: bool = False) -> str:
        """
        List all durable memories stored for a workspace/project.
        Useful for auditing what has been remembered, or deciding what to prune.

        Args:
            workspace_id:    The workspace or project identifier.
            include_global:  If True, also include global Instruction memories
                             (visible across all workspaces/projects).
        """
        try:
            memory_store, _, _, _, _ = get_memory_modules()
            if memory_store._ENABLE_PERSISTENCE:
                await memory_store.open_pool()
            memories = await memory_store.list_durable_memories(
                workspace_id, include_global=include_global
            )
            if not memories:
                scope = "workspace + global" if include_global else "workspace"
                return f"No memories found for {scope} '{workspace_id}'."
            lines = [f"Memories for '{workspace_id}' ({len(memories)} total):\n"]
            for i, m in enumerate(memories, 1):
                tag = "[GLOBAL]" if m["is_global"] else "[WORKSPACE]"
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

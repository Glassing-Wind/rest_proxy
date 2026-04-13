"""tools/workspace_context.py — Workspace context resolution helpers.

This module keeps workspace selection and per-client context isolated in the
shared HTTP MCP daemon. It avoids mutating os.environ during tool calls and
provides a cached context for the current client session.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# WorkspaceContext dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceContext:
    """Immutable snapshot of the resolved workspace for the current client.

    Instances are created by `resolve()` and should be passed explicitly through
    the call chain rather than re-resolved on every function call.
    """

    workspace_path: str
    workspace_id: str
    base_id: str
    agent_id: str

_GLOBAL_CURRENT_CONTEXT: Optional[WorkspaceContext] = None  # Cache for single-session processes


def _session_suffix() -> str:
    """Return a deterministic session suffix: <PID>[-<IPC slice>].

    Uses VSCODE_PID if available (stable across the IDE session), otherwise
    falls back to the OS process PID.
    """
    v_pid = os.getenv("VSCODE_PID") or str(os.getpid())
    v_ipc = os.getenv("VSCODE_IPC_HOOK")

    suffix = f"{v_pid}"
    if v_ipc:
        ipc_name = os.path.basename(v_ipc).split(".")[0]
        suffix += f"-{ipc_name[:4]}"
    return suffix


async def _rebind_session(session_id: str, new_ctx: WorkspaceContext):
    """Update cached context so the next tool call reflects the new workspace."""
    global _GLOBAL_CURRENT_CONTEXT
    _GLOBAL_CURRENT_CONTEXT = new_ctx


async def resolve(workspace_root: Optional[str] = None) -> WorkspaceContext:
    """Build an immutable WorkspaceContext for the given workspace root.

    If workspace_root is provided and a session is active, automatically
    re-bind the cached context to that workspace.
    """
    from graphrag_core.config import resolve_workspace_context as _find_path

    workspace_path = workspace_root or _find_path()
    workspace_id = os.path.basename(workspace_path)

    base_id = workspace_id
    agent_id = f"{base_id}-{_session_suffix()}"

    ctx = WorkspaceContext(
        workspace_path=workspace_path,
        workspace_id=workspace_id,
        base_id=base_id,
        agent_id=agent_id,
    )

    from _jobs import client_session_id
    session_id = client_session_id.get()
    if not session_id:
        global _GLOBAL_CURRENT_CONTEXT
        _GLOBAL_CURRENT_CONTEXT = ctx

    if workspace_root and session_id:
        await _rebind_session(session_id, ctx)

    return ctx


def get_current_ctx() -> Optional[WorkspaceContext]:
    """Retrieve the cached context for the current process/session."""
    return _GLOBAL_CURRENT_CONTEXT


def rebind_session_sync(workspace_id_or_path: str) -> None:
    """
    Synchronous entry point to trigger context re-binding.
    Used by tools like list_dir/grep to detect workspace changes.
    """
    from _jobs import client_session_id, _MAIN_LOOP
    import asyncio
    sid = client_session_id.get()
    
    async def _do_resolve(session_id: Optional[str]):
        # Manually set the ContextVar inside the async task so resolve() sees it
        token = None
        if session_id:
            token = client_session_id.set(session_id)
        try:
            # We re-resolve context for the specific workspace root
            new_ctx = await resolve(workspace_id_or_path)
            await _rebind_session(session_id or "local", new_ctx)
        finally:
            if token:
                client_session_id.reset(token)

    if _MAIN_LOOP and _MAIN_LOOP.is_running():
        # Case A: We are in the main Brain Server process
        _MAIN_LOOP.call_soon_threadsafe(
            lambda: asyncio.create_task(_do_resolve(sid))
        )
    else:
        # Case B: We are in a standalone script or local MCP process
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # We are already in an event loop (e.g. MCP host)
                loop.create_task(_do_resolve(sid))
            else:
                asyncio.run(_do_resolve(sid))
        except RuntimeError:
            # No event loop exists - run a one-off
            asyncio.run(_do_resolve(sid))

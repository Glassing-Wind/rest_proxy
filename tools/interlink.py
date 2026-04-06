"""tools/interlink.py — Interlink communication tools for multi-agent coordination."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from memory import store_core
from tools import workspace_context
from tools.workspace_context import WorkspaceContext

# Root of the rest_proxy repo — used to locate supervisor PID files.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_DIR = _REPO_ROOT / ".runtime"


# ---------------------------------------------------------------------------
# Core async functions
# ---------------------------------------------------------------------------


async def heartbeat(
    capabilities: List[str] = [],
    current_goal: Optional[str] = None,
    metadata: Dict[str, Any] = {},
    workspace_root: Optional[str] = None,
    ctx: Optional[WorkspaceContext] = None,
) -> WorkspaceContext:
    """Update the agent's status in the registry.

    Returns the resolved WorkspaceContext so callers can reuse it without
    a second resolve() call.
    """
    if ctx is None:
        ctx = await workspace_context.resolve(workspace_root)

    if not store_core._interlink_pool_available():
        return ctx

    sql = """
        INSERT INTO agent_registry (
            agent_id, workspace_id, workspace_path, last_seen,
            capabilities, current_goal, metadata
        ) VALUES (
            %(aid)s, %(wid)s, %(wpath)s, extract(epoch from now()),
            %(caps)s, %(goal)s, %(meta)s
        )
        ON CONFLICT (agent_id) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            workspace_path = EXCLUDED.workspace_path,
            last_seen = EXCLUDED.last_seen,
            capabilities = EXCLUDED.capabilities,
            current_goal = EXCLUDED.current_goal,
            metadata = agent_registry.metadata || EXCLUDED.metadata
    """
    params = {
        "aid": ctx.agent_id,
        "wid": ctx.workspace_id,
        "wpath": ctx.workspace_path,
        "caps": capabilities,
        "goal": current_goal,
        "meta": json.dumps(metadata),
    }
    try:
        async with store_core._interlink_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
    except Exception as e:
        store_core._debug("interlink_heartbeat_error", error=str(e))

    return ctx


async def get_active_agents(max_age_seconds: int = 120) -> List[Dict[str, Any]]:
    """List agents that have checked in recently."""
    if not store_core._interlink_pool_available():
        return []

    sql = """
        SELECT agent_id, workspace_id, workspace_path, last_seen,
               capabilities, current_goal, metadata
        FROM agent_registry
        WHERE last_seen > extract(epoch from now()) - %(age)s
        ORDER BY last_seen DESC
    """
    agents = []
    try:
        async with store_core._interlink_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, {"age": max_age_seconds})
                async for row in cur:
                    agents.append({
                        "agent_id": row[0],
                        "workspace_id": row[1],
                        "workspace_path": row[2],
                        "last_seen": row[3],
                        "capabilities": row[4],
                        "current_goal": row[5],
                        "metadata": row[6],
                    })
    except Exception as e:
        store_core._debug("interlink_get_agents_error", error=str(e))
    return agents


async def send_message(
    receiver_id: Optional[str],
    content: str,
    interaction_type: str = "query",
    subject: Optional[str] = None,
    session_id: Optional[str] = None,
    target_workspace: Optional[str] = None,
    metadata: Dict[str, Any] = {},
    workspace_root: Optional[str] = None,
    ctx: Optional[WorkspaceContext] = None,
) -> Optional[str]:
    """Send a message to another agent, or broadcast to all (receiver_id=None)."""
    if not store_core._pg_pool_available():
        return None

    if ctx is None:
        ctx = await workspace_context.resolve(workspace_root)

    sql = """
        INSERT INTO agent_messages (
            sender_id, receiver_id, source_workspace, target_workspace,
            interaction_type, session_id, subject, content, metadata
        ) VALUES (
            %(sid)s, %(rid)s, %(sw)s, %(tw)s,
            %(it)s, %(sess)s, %(sub)s, %(cont)s, %(meta)s
        ) RETURNING id
    """
    params = {
        "sid": ctx.agent_id,
        "rid": receiver_id,
        "sw": ctx.workspace_id,
        "tw": target_workspace,
        "it": interaction_type,
        "sess": session_id,
        "sub": subject,
        "cont": content,
        "meta": json.dumps(metadata),
    }
    try:
        async with store_core._interlink_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                return str(row[0]) if row else None
    except Exception as e:
        store_core._debug("interlink_send_message_error", error=str(e))
    return None


async def list_messages(
    status: str = "unread",
    limit: int = 20,
    workspace_root: Optional[str] = None,
    ctx: Optional[WorkspaceContext] = None,
) -> List[Dict[str, Any]]:
    """Retrieve messages addressed to the current agent.

    Matches on both agent_id (session-scoped, may have PID suffix) and base_id
    (stable name) so messages sent before a restart are still delivered.
    """
    if not store_core._interlink_pool_available():
        return []

    if ctx is None:
        ctx = await workspace_context.resolve(workspace_root)

    # id ASC as secondary sort ensures stable ordering when created_at has
    # duplicate float values (epoch precision limits).
    sql = """
        SELECT id, sender_id, source_workspace, interaction_type,
               session_id, subject, content, metadata, created_at, status
        FROM agent_messages
        WHERE (receiver_id IS NULL OR receiver_id = %(aid)s OR receiver_id = %(bid)s)
          AND status = %(status)s
        ORDER BY created_at DESC, id ASC
        LIMIT %(limit)s
    """
    msgs = []
    try:
        store_core._debug(
            "list_messages_debug",
            agent_id=ctx.agent_id,
            base_id=ctx.base_id,
            status=status,
        )
        async with store_core._interlink_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, {
                    "aid": ctx.agent_id,
                    "bid": ctx.base_id,
                    "status": status,
                    "limit": limit,
                })
                async for row in cur:
                    msgs.append({
                        "id": str(row[0]),
                        "sender_id": row[1],
                        "source_workspace": row[2],
                        "interaction_type": row[3],
                        "session_id": row[4],
                        "subject": row[5],
                        "content": row[6],
                        "metadata": row[7],
                        "created_at": row[8],
                        "status": row[9],
                    })
    except Exception as e:
        store_core._debug("interlink_list_messages_error", error=str(e))
    return msgs


async def mark_message_status(
    message_id: str,
    status: str = "read",
    workspace_root: Optional[str] = None,
    ctx: Optional[WorkspaceContext] = None,
) -> bool:
    """Update message status, verifying that the caller is the intended receiver.

    Only the agent whose agent_id or base_id matches the message's receiver_id
    can update its status. Broadcast messages (receiver_id IS NULL) can be
    marked by anyone.
    """
    if not store_core._interlink_pool_available():
        return False

    if ctx is None:
        ctx = await workspace_context.resolve(workspace_root)

    sql = """
        UPDATE agent_messages
        SET status = %(status)s
        WHERE id = %(mid)s
          AND (receiver_id = %(aid)s OR receiver_id = %(bid)s OR receiver_id IS NULL)
    """
    try:
        async with store_core._interlink_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, {
                    "status": status,
                    "mid": message_id,
                    "aid": ctx.agent_id,
                    "bid": ctx.base_id,
                })
                return (cur.rowcount or 0) > 0
    except Exception as e:
        store_core._debug("interlink_mark_status_error", error=str(e))
    return False


async def cleanup_old_messages(
    workspace_root: Optional[str] = None,
    ctx: Optional[WorkspaceContext] = None,
) -> int:
    """Delete messages older than the configured interlink TTL."""
    if not store_core._interlink_pool_available():
        return 0

    if ctx is None:
        ctx = await workspace_context.resolve(workspace_root)

    sql = "DELETE FROM agent_messages WHERE created_at < extract(epoch from now()) - %(ttl)s"
    try:
        async with store_core._interlink_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, {"ttl": ctx.interlink_ttl})
                return cur.rowcount or 0
    except Exception as e:
        store_core._debug("interlink_cleanup_error", error=str(e))
    return 0


# ---------------------------------------------------------------------------
# Hot-reload helpers
# ---------------------------------------------------------------------------


async def broadcast_tool_reload(
    fingerprint: str,
    ctx: Optional[WorkspaceContext] = None,
) -> None:
    """Broadcast a tool_list_changed event via Interlink so all connected agents
    can notify their own IDE windows to refresh tool definitions.

    Args:
        fingerprint: Short hash of the tool names at boot — used by receivers
                     to skip re-broadcast if they already have this version.
        ctx:         Pre-resolved WorkspaceContext; resolved lazily if None.
    """
    if ctx is None:
        ctx = await workspace_context.resolve()

    await send_message(
        receiver_id=None,                         # broadcast — all agents
        content=fingerprint,
        interaction_type="sync",
        subject="tool_list_changed",
        metadata={"fingerprint": fingerprint},
        ctx=ctx,
    )


def _signal_own_supervisor() -> str:
    """Send SIGUSR1 to the supervisor for this MCP_ID.

    Reads the PID from .runtime/graphrag_mcp_supervisor_{MCP_ID}.pid.
    Returns a human-readable result string.
    """
    mcp_id = os.environ.get("MCP_ID", "default")
    pid_file = _RUNTIME_DIR / f"graphrag_mcp_supervisor_{mcp_id}.pid"

    if not pid_file.exists():
        return f"No supervisor PID file found for MCP_ID={mcp_id} ({pid_file})."

    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        return f"Corrupt PID file: {pid_file}"

    try:
        os.kill(pid, signal.SIGUSR1)
        return f"SIGUSR1 sent to supervisor pid={pid} (MCP_ID={mcp_id})."
    except ProcessLookupError:
        return f"Supervisor pid={pid} is not running. Stale PID file?"
    except PermissionError:
        return f"Permission denied sending signal to pid={pid}."


# ---------------------------------------------------------------------------
# MCP Tool Wrappers
# ---------------------------------------------------------------------------


def register_interlink_tools(mcp: FastMCP) -> None:
    """Register the consolidated Interlink toolbox with the MCP server."""

    @mcp.tool()
    async def interlink_status(include_offline: bool = False) -> str:
        """
        View available agents in the cluster.
        Shows who is online, what repository they are in, and their current goal.
        """
        agents = await get_active_agents(max_age_seconds=3600 if include_offline else 120)
        if not agents:
            return "No active agents found in the registry."

        lines = ["### Active Agent Registry", ""]
        for a in agents:
            status = "🟢 ONLINE" if (time.time() - a["last_seen"] < 120) else "⚪ OFFLINE"
            lines.append(f"**Agent**: `{a['agent_id']}` ({status})")
            lines.append(f"- **Workspace**: `{a['workspace_id']}` ({a['workspace_path']})")
            if a["current_goal"]:
                lines.append(f"- **Goal**: {a['current_goal']}")
            if a["capabilities"]:
                lines.append(f"- **Capabilities**: {', '.join(a['capabilities'])}")
            lines.append("")
        return "\n".join(lines)

    @mcp.tool()
    async def interlink_message(
        action: str,
        target_agent: Optional[str] = None,
        content: Optional[str] = None,
        interaction_type: str = "query",
        subject: Optional[str] = None,
        message_id: Optional[str] = None,
        status: str = "unread",
        workspace_root: Optional[str] = None,
    ) -> str:
        """
        Multi-agent messaging toolbox.

        Actions:
        - `send`: Send a message to `target_agent` (use None for broadcast).
        - `poll`: Check for new messages in your mailbox.
        - `read`: Mark message `message_id` as read.
        - `archive`: Mark message `message_id` as archived.

        Interaction Types:
        - `query`: A simple question.
        - `request_review`: Asking for feedback on code.
        - `sync`: Coordination (e.g. "I've updated the API").
        - `ping`: Just checking if agent is alive/responsive.
        """
        # Resolve context ONCE and pass it to every sub-call.
        ctx = await workspace_context.resolve(workspace_root)

        if action == "send":
            if not content:
                return "Error: content is required for 'send' action."
            msg_id = await send_message(
                receiver_id=target_agent,
                content=content,
                interaction_type=interaction_type,
                subject=subject,
                ctx=ctx,
            )
            if msg_id:
                return f"Message sent to `{target_agent or 'everyone'}`. ID: {msg_id}"
            return "Failed to send message."

        elif action == "poll":
            msgs = await list_messages(status=status, ctx=ctx)
            if not msgs:
                return f"No {status} messages found."
            lines = [f"### Mailbox ({status})", ""]
            for m in msgs:
                lines.append(f"**From**: `{m['sender_id']}` (Type: `{m['interaction_type']}`)")
                lines.append(f"**Subject**: {m['subject'] or '(No Subject)'}")
                lines.append(f"**ID**: `{m['id']}`")
                lines.append(f"**Content**:\n{m['content']}")
                lines.append("-" * 20)
            return "\n".join(lines)

        elif action in ("read", "archive"):
            if not message_id:
                return "Error: message_id is required."
            ok = await mark_message_status(message_id, status=action, ctx=ctx)
            if ok:
                return f"Message {message_id} marked as {action}."
            return f"Failed to mark {message_id} — not found or not authorized."

        return f"Unknown action: {action}. Valid: send, poll, read, archive."

    @mcp.tool()
    async def update_my_status(
        current_goal: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        workspace_root: Optional[str] = None,
    ) -> str:
        """Update your own status/goal in the registry so other agents know what you are doing."""
        # heartbeat() returns the resolved ctx — no second resolve() call needed.
        ctx = await heartbeat(
            capabilities=capabilities or [],
            current_goal=current_goal,
            workspace_root=workspace_root,
        )
        return f"Status updated for `{ctx.agent_id}`."

    @mcp.tool()
    async def reload_mcp_tools(reason: str = "") -> str:
        """
        Trigger a hot-reload of the MCP server, refreshing all tool definitions.

        Safe to call from any agent or workspace. Sends SIGUSR1 to the supervisor
        for this IDE session. The supervisor kills and immediately respawns
        mcp_server.py; the new process emits a notifications/tool_list_changed
        JSON-RPC notification so the IDE reloads its tool list automatically.

        Other connected IDE sessions (e.g. Codex) will also be notified via the
        Interlink broadcast that the new server sends on boot.
        """
        result = _signal_own_supervisor()
        log_msg = f"reload_mcp_tools called. reason={reason!r} result={result}"
        store_core._debug("reload_mcp_tools", reason=reason, result=result)
        return f"{result}\n\nReason: {reason or '(none)'}\n\nThe server will restart within ~1 second and your IDE will refresh its tool list."

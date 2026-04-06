"""tools/workspace_context.py — Immutable workspace context for interlink tools.

Resolves workspace identity WITHOUT mutating os.environ, eliminating the
environment pollution problem between concurrent tool calls from different
IDE windows sharing the same MCP server process.

The key design principle: os.environ is a global, process-wide mutable dict.
Any call to load_dotenv(..., override=True) or os.environ[key] = value affects
ALL concurrent tool calls. This module instead uses dotenv.dotenv_values() which
returns a plain dict without touching the process environment.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Keys that are ALWAYS sourced from the global coordination env, overriding
# any project-specific value. These settings are infrastructure-level and must
# be consistent across all workspaces sharing the same Brain server.
_COORDINATION_KEYS = frozenset({
    "LM_PROXY_INTERLINK_DSN",
    "LM_PROXY_ENABLE_INTERLINK",
    "LM_PROXY_INTERLINK_TTL",
    "LM_PROXY_REDIS_URL",
})

_GLOBAL_ENV = Path.home() / ".gemini" / "antigravity" / ".env"
_REPO_ROOT = Path(__file__).resolve().parents[1]  # tools/ -> rest_proxy/

# ---------------------------------------------------------------------------
# WorkspaceContext dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceContext:
    """Immutable snapshot of the resolved workspace and agent identity.

    Instances are created by `resolve()` and should be passed explicitly through
    the call chain rather than re-resolved on every function call.
    """

    workspace_path: str
    workspace_id: str
    base_id: str        # Stable name (e.g. "antigravity-rental") — persists across restarts
    agent_id: str       # Active name — may have a PID suffix if a liveness collision is detected
    interlink_enabled: bool
    interlink_dsn: str
    interlink_ttl: int


# ---------------------------------------------------------------------------
# Internal helpers (pure — no os.environ writes)
# ---------------------------------------------------------------------------


def _read_env_stack(workspace_path: str) -> dict[str, str]:
    """Merge .env files in priority order WITHOUT touching os.environ.

    Priority (highest wins):
      1. Repo root .env  — engine defaults (lowest priority)
      2. Workspace .env  — project-specific settings
      3. Global ~/...antigravity/.env — Only for _COORDINATION_KEYS (force-wins)

    The global coordination vars always win so that the shared Brain server
    has consistent infrastructure settings regardless of which workspace is active.
    """
    result: dict[str, str] = {}

    # 1. Engine/repo root defaults
    repo_env = _REPO_ROOT / ".env"
    if repo_env.exists():
        result.update(dotenv_values(str(repo_env)))

    # 2. Project workspace (overrides engine defaults for project-specific config)
    ws_env = Path(workspace_path) / ".env"
    if ws_env.exists():
        result.update(dotenv_values(str(ws_env)))

    # 3. Global coordination keys (force override — infrastructure always wins)
    if _GLOBAL_ENV.exists():
        global_vars = dotenv_values(str(_GLOBAL_ENV))
        for key in _COORDINATION_KEYS:
            if key in global_vars:
                result[key] = global_vars[key]

    return result


def _session_suffix() -> str:
    """Return a deterministic collision-avoidance suffix: -<PID>[-<IPC slice>].

    Uses VSCODE_PID if available (stable across the IDE session), otherwise
    falls back to the OS process PID.
    """
    v_pid = os.getenv("VSCODE_PID") or str(os.getpid())
    v_ipc = os.getenv("VSCODE_IPC_HOOK")

    suffix = f"-{v_pid}"
    if v_ipc:
        # Use a stable slice of the IPC hook filename for extra uniqueness
        ipc_name = os.path.basename(v_ipc).split(".")[0]
        suffix += f"-{ipc_name[:4]}"
    return suffix


async def _resolve_agent_id(base_id: str, interlink_ttl: int) -> str:
    """Check for an active liveness collision and return the final agent_id.

    If the base_id is already heartbeating within the liveness window, we
    append a PID-based suffix to avoid hijacking the primary mailbox.
    Once the stale session's TTL expires (~120s), the next resolve() call
    will correctly reclaim the clean Base ID.
    """
    from memory import store_core

    if not store_core._interlink_pool_available():
        return base_id

    liveness_window = min(120, interlink_ttl // 2)
    sql = "SELECT last_seen FROM agent_registry WHERE agent_id = %(aid)s"
    try:
        async with store_core._interlink_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, {"aid": base_id})
                row = await cur.fetchone()
                if row and (time.time() - row[0] < liveness_window):
                    return f"{base_id}{_session_suffix()}"
    except Exception as e:
        from memory.store_core import _debug
        _debug("workspace_context_resolve_error", error=str(e))

    return base_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve(workspace_root: Optional[str] = None) -> WorkspaceContext:
    """Build an immutable WorkspaceContext for the given workspace root.

    This is the single entry point for all identity resolution. It:
      1. Finds the workspace path via sessions.json or .git/.env anchors.
      2. Reads all relevant .env files into a plain dict (no os.environ mutation).
      3. Resolves the stable Base ID from the project's .env.
      4. Performs a DB-backed liveness check and applies a PID suffix if needed.
      5. Returns a frozen dataclass that can be passed through the call chain.
    """
    from graphrag_core.config import resolve_workspace_context as _find_path

    # 1. Locate workspace directory
    workspace_path = workspace_root or _find_path()
    workspace_id = os.path.basename(workspace_path)

    # 2. Read config without touching os.environ
    env = _read_env_stack(workspace_path)

    # 3. Resolve Base ID: prefer explicit setting, fall back to directory name
    custom_id = env.get("LM_PROXY_INTERLINK_ID", "").strip()
    base_id = custom_id if custom_id else workspace_id

    # 4. Interlink infrastructure settings
    interlink_enabled = env.get("LM_PROXY_ENABLE_INTERLINK", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }
    interlink_dsn = env.get("LM_PROXY_INTERLINK_DSN", env.get("LM_PROXY_PG_DSN", ""))
    interlink_ttl = int(env.get("LM_PROXY_INTERLINK_TTL", "86400"))

    # 5. Collision-safe agent_id (one DB round-trip, only if pool is available)
    agent_id = await _resolve_agent_id(base_id, interlink_ttl)

    return WorkspaceContext(
        workspace_path=workspace_path,
        workspace_id=workspace_id,
        base_id=base_id,
        agent_id=agent_id,
        interlink_enabled=interlink_enabled,
        interlink_dsn=interlink_dsn,
        interlink_ttl=interlink_ttl,
    )

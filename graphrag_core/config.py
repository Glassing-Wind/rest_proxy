"""Centralized environment loading and base-path helpers."""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def get_repo_root() -> Path:
    """Return the repository root (directory containing this package's parent)."""
    return Path(__file__).resolve().parents[1]


def resolve_workspace_context(path: Optional[str] = None) -> str:
    """
    Find the root of the workspace context.
    If 'path' is provided and absolute, it is used directly.
    Otherwise, it checks the VSCODE_PID/IPC registry in sessions.json.
    Searches upwards for a .git or .env.
    """
    # 0. If path is provided and absolute, prioritize it.
    if path and os.path.isabs(path):
        return path

    # 1. Check VSCODE_PID or VSCODE_IPC_HOOK if path is generic/missing
    v_pid = os.getenv("VSCODE_PID")
    v_ipc = os.getenv("VSCODE_IPC_HOOK")
    
    registry_path = os.path.expanduser("~/.gemini/antigravity/sessions.json")
    if os.path.exists(registry_path):
        try:
            with open(registry_path, "r") as f:
                registry = json.load(f)
                # Try IPC hook FIRST (Most stable and unique per window/instance)
                if v_ipc and v_ipc in registry:
                    return registry[v_ipc]
                # Try PID as fallback
                if v_pid and v_pid in registry:
                    return registry[v_pid]
        except Exception:
            pass

    if not path or path == "/":
        path = os.getcwd()
        
    # If we are starting in a generic root, try to find the session mapping
    if path == "/" or path == os.path.expanduser("~"):
        # (v_pid check is already done above, so we just fall through)
        pass

    curr = Path(path).resolve()
    if curr.is_file():
        curr = curr.parent
        
    # Search upwards for a project anchor
    for parent in [curr] + list(curr.parents):
        if (parent / ".git").exists() or (parent / ".env").exists():
            return str(parent)
            
    return str(curr)


def load_env(workspace_path: Optional[str] = None) -> None:
    """
    Load .env files with limited shared-infrastructure overrides.
    1. Base Fallback: shared engine root (rest_proxy)
    2. Fallback: CWD
    3. PRIMARY: Target Workspace (Settings for Neo4j, Indexing, and everything else)
    4. Shared Infrastructure Overrides

    Note: For workspace resolution, prefer tools/workspace_context.resolve() which
    reads the same files without mutating os.environ.
    """
    # 1. Repo Root fallback (lowest priority)
    repo_root = get_repo_root()
    load_dotenv(os.path.join(repo_root, ".env"), override=False)

    # 2. CWD fallback
    load_dotenv(os.path.join(os.getcwd(), ".env"), override=True)

    # 3. Target Workspace (Highest Priority for project context)
    if workspace_path and os.path.exists(os.path.join(workspace_path, ".env")):
        load_dotenv(os.path.join(workspace_path, ".env"), override=True)
    
    # 4. Shared infrastructure overrides
    global_env = os.path.expanduser("~/.gemini/antigravity/.env")
    if os.path.exists(global_env):
        # Load a temporary copy so only explicitly shared settings leak across workspaces.
        import dotenv
        global_vars = dotenv.dotenv_values(global_env)

        shared_vars = {
            "LM_PROXY_REDIS_URL",
        }
        for key in shared_vars:
            if key in global_vars:
                os.environ[key] = global_vars[key]
    

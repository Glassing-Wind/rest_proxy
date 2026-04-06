import os
import json
from mcp.server.fastmcp import FastMCP

from tools.brain.graph.core import enqueue_graph_build, get_last_graph_build_metric


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def register_workspace(project_path: str) -> str:
        """
        Register a logical workspace/project ID to a local filesystem path.
        This enables 'Zero-Config' features by linking your current IDE session 
        to the project root.
        """
        v_pid = os.getenv("VSCODE_PID")
        if not v_pid:
            return "Error: VSCODE_PID environment variable not set. Are you running inside Antigravity/Cursor/VSCode terminal?"

        registry_dir = os.path.expanduser("~/.gemini/antigravity")
        os.makedirs(registry_dir, exist_ok=True)
        registry_path = os.path.join(registry_dir, "sessions.json")

        registry = {}
        if os.path.exists(registry_path):
            try:
                with open(registry_path, "r") as f:
                    registry = json.load(f)
            except Exception:
                pass

        abs_path = os.path.abspath(os.path.expanduser(project_path))
        
        # Register for both PID and IPC_HOOK to ensure cross-process stability
        if v_pid:
            registry[v_pid] = abs_path
        
        v_ipc = os.getenv("VSCODE_IPC_HOOK")
        if v_ipc:
            registry[v_ipc] = abs_path

        with open(registry_path, "w") as f:
            json.dump(registry, f, indent=2)

        return f"Successfully registered workspace: {abs_path} (PID: {v_pid})"

    from tools.brain.docs import core as docs_tools
    from tools.brain.graph import core as graph_tools
    from tools.brain.search import tools as search_tools

    docs_tools.register(mcp)
    graph_tools.register(mcp)
    search_tools.register(mcp)

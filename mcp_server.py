"""
mcp_server.py — thin entry point for the GraphRAG MCP server.

All tool implementations live in tools/*.py  (registered via tools/__init__.py).
Shared helpers:  _helpers.py   (lazy module loader)
                 _jobs.py      (background job registry)
"""

from graphrag_core.config import load_env

load_env()

import asyncio
import sys

# --- MCP Protocol Guard ---
# Redirect sys.stdout → stderr so third-party prints can't corrupt the JSON-RPC stream.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr
# --------------------------

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("rest_proxy")

# Register all tool groups
from tools import register_all

register_all(mcp)

# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    """Main entrypoint: restore watched projects, start watcher, run MCP server."""
    import tools.indexing as _idx
    import _jobs

    _jobs.register_main_loop(asyncio.get_running_loop())

    await _idx.load_watched_config()

    # Grab the index_workspace function so the watcher can call it without a circular import
    from tools.indexing import index_workspace

    await _idx.start_watcher(index_workspace)

    print("[lm-proxy] Startup complete.", file=sys.stderr)

    try:
        sys.stdout = _REAL_STDOUT
        await mcp.run_stdio_async()
    finally:
        await _idx.stop_watcher()
        try:
            import memory.store as memory_store

            await memory_store.close_pool()
        except ImportError:
            pass
        print("[lm-proxy] Shutdown complete.", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GraphRAG MCP Server")
    parser.add_argument("command", nargs="?", choices=["index_workspace"])
    parser.add_argument("path", nargs="?", help="Project path for indexing")
    args = parser.parse_args()

    if args.command == "index_workspace" and args.path:

        async def _run_index() -> None:
            from tools.indexing import index_workspace

            result = await index_workspace(args.path)
            print(result)

        asyncio.run(_run_index())
    else:
        try:
            asyncio.run(main())
        except (KeyboardInterrupt, SystemExit):
            pass

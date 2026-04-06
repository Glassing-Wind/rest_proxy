"""
mcp_server.py — thin entry point for the GraphRAG MCP server (STDIO).

This module provides the local STDIO transport for the GraphRAG Brain.
It is primarily used for local development and legacy environment support.
"""

import asyncio
import hashlib
import json
import sys
from _mcp import mcp

# --- MCP Protocol Guard ---
# Redirect sys.stdout → stderr so third-party prints can't corrupt the JSON-RPC stream.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr
# --------------------------


def _compute_tool_fingerprint() -> str:
    """Return a short hash of the registered tool names.

    Used to detect whether a tool_list_changed broadcast from another agent
    actually represents a different tool set from the one we booted with.
    """
    try:
        tool_names = sorted(t.name for t in mcp._tool_manager.list_tools())
    except Exception:
        tool_names = []
    return hashlib.sha256(json.dumps(tool_names).encode()).hexdigest()[:12]


# Computed once at import time for health and startup logs.
BOOT_FINGERPRINT: str = _compute_tool_fingerprint()


async def main() -> None:
    """Main entrypoint: restore watched projects, start watcher, run MCP server (STDIO)."""
    from tools.hands import indexing as _idx
    import _jobs

    _jobs.register_main_loop(asyncio.get_running_loop())

    await _idx.load_watched_config()

    # Grab the index_workspace function so the watcher can call it without a circular import
    from tools.hands.indexing import index_workspace

    await _idx.start_watcher(index_workspace)

    print("[lm-proxy] STDIO Server Startup complete.", file=sys.stderr)

    try:
        # Restore stdout for the actual MCP communication
        sys.stdout = _REAL_STDOUT
        
        # Signal the client to reload tools after startup.
        async def _notify_client_on_start() -> None:
            await asyncio.sleep(1.0)

            # 1. Notify our own IDE immediately via the JSON-RPC pipe.
            msg = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
            print(json.dumps(msg), flush=True)

            tool_count = 0
            try:
                tool_count = len(list(mcp._tool_manager.list_tools()))
            except Exception:
                pass
            print(
                f"[lm-proxy] Sent tool_list_changed to IDE. "
                f"tools={tool_count} fingerprint={BOOT_FINGERPRINT}",
                file=sys.stderr,
            )

        asyncio.create_task(_notify_client_on_start())

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

    parser = argparse.ArgumentParser(description="GraphRAG MCP Server (STDIO)")
    parser.add_argument("command", nargs="?", choices=["index_workspace", "bootstrap"])
    parser.add_argument("path", nargs="?", help="Project path for indexing")
    args = parser.parse_args()
    if args.command == "index_workspace" and args.path:
        async def _run_index() -> None:
            from tools.hands.indexing import index_workspace
            result = await index_workspace(args.path)
            print(result)

        asyncio.run(_run_index())
    elif args.command == "bootstrap":
        async def _run_bootstrap() -> None:
            from memory.bootstrap import bootstrap_schema
            ok = await bootstrap_schema()
            print(f"[lm-proxy] Bootstrap complete ok={ok}", file=sys.stderr)

        asyncio.run(_run_bootstrap())
    else:
        try:
            asyncio.run(main())
        except (KeyboardInterrupt, SystemExit):
            pass

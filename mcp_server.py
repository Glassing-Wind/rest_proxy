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


# Computed once at import time so the heartbeat loop can compare incoming
# fingerprints against the version this process booted with.
BOOT_FINGERPRINT: str = _compute_tool_fingerprint()

# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

async def _start_interlink_heartbeat() -> None:
    """Background task to update agent registry and clean up old messages."""
    try:
        from tools import interlink, workspace_context

        # Resolve context once at startup
        ctx = await workspace_context.resolve()

        if not ctx.interlink_enabled:
            return

        print(
            f"[lm-proxy] Starting Interlink heartbeat for {ctx.agent_id} "
            f"(base: {ctx.base_id}) at {ctx.workspace_path}",
            file=sys.stderr,
        )

        # Initial heartbeat and cleanup — reuse the resolved ctx
        ctx = await interlink.heartbeat(ctx=ctx)
        await interlink.cleanup_old_messages(ctx=ctx)

        consecutive_failures = 0
        count = 0
        while True:
            await asyncio.sleep(60)
            try:
                ctx = await interlink.heartbeat(ctx=ctx)
                consecutive_failures = 0
                count += 1
                if count % 60 == 0:
                    await interlink.cleanup_old_messages(ctx=ctx)
            except Exception as e:
                consecutive_failures += 1
                print(
                    f"[lm-proxy] Interlink heartbeat error "
                    f"(failure #{consecutive_failures}): {e}",
                    file=sys.stderr,
                )
                if consecutive_failures >= 5:
                    print(
                        "[lm-proxy] Interlink heartbeat: 5+ consecutive failures. "
                        "Is Postgres reachable?",
                        file=sys.stderr,
                    )
    except Exception as e:
        print(f"[lm-proxy] Interlink heartbeat fatal error: {e}", file=sys.stderr)


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
        
        # --- Hot Reload Refresh ---
        # Signal the client to reload tools and broadcast fingerprint to all agents.
        async def _notify_client_on_start() -> None:
            await asyncio.sleep(1.0)
            from tools import interlink, workspace_context

            # 1. Notify our own IDE immediately via the JSON-RPC pipe.
            # Per MCP 2024-11-05 spec the correct method is notifications/tools/list_changed
            msg = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
            print(json.dumps(msg), flush=True)

            # 2. Log fingerprint for visibility.
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

            # 3. Broadcast via Interlink so other agents notify their own IDEs.
            try:
                ctx = await workspace_context.resolve()
                if ctx.interlink_enabled:
                    await interlink.broadcast_tool_reload(
                        fingerprint=BOOT_FINGERPRINT, ctx=ctx
                    )
                    print(
                        f"[lm-proxy] Interlink broadcast sent (fp={BOOT_FINGERPRINT}).",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(
                    f"[lm-proxy] Interlink broadcast failed (non-fatal): {e}",
                    file=sys.stderr,
                )

        asyncio.create_task(_notify_client_on_start())
        # --------------------------

        # --- Interlink Support ---
        asyncio.create_task(_start_interlink_heartbeat())
        # -------------------------

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

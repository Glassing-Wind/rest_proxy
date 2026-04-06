"""
brain_server.py — GraphRAG MCP Brain, Streamable HTTP transport.

Uses the MCP Starlette app directly (no FastAPI wrapper) to avoid:
  - Lifespan propagation failures (mounted sub-app lifespan not called)
  - Double-prefix path routing issues (/mcp mounted at /mcp → strips prefix → 404)

Architecture:
  - Starlette app with our custom lifespan wrapping the MCP session manager
  - Route("/" → health), Mount("/" → mcp_app handles /mcp internally)
  - Session context middleware for multi-client session scoping
"""

import asyncio
import hashlib
import json
import sys
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from _mcp import mcp

# ---------------------------------------------------------------------------
# Tool fingerprint
# ---------------------------------------------------------------------------

def _compute_tool_fingerprint() -> str:
    try:
        tool_names = sorted(t.name for t in mcp._tool_manager.list_tools())
    except Exception:
        tool_names = []
    return hashlib.sha256(json.dumps(tool_names).encode()).hexdigest()[:12]


BOOT_FINGERPRINT: str = _compute_tool_fingerprint()


def _resolve_session_id(scope) -> str:
    """Resolve the most specific available client session identifier."""
    from starlette.datastructures import Headers

    headers = Headers(scope=scope)
    for header_name in (
        "X-Session-ID",
        "Mcp-Session-Id",
        "MCP-Session-Id",
        "X-Client-Session-ID",
    ):
        session_id = headers.get(header_name)
        if session_id:
            return session_id

    client = scope.get("client")
    client_ip = client[0] if client else "unknown"
    client_port = client[1] if client and len(client) > 1 else "unknown"
    forwarded_for = headers.get("X-Forwarded-For", "")
    ua = headers.get("User-Agent", "vanilla")
    fingerprint = f"{client_ip}:{client_port}:{forwarded_for}:{ua}"
    return f"anon-{hashlib.md5(fingerprint.encode()).hexdigest()[:12]}"


# ---------------------------------------------------------------------------
# Lifespan: wraps the MCP session manager + our own startup/shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: Starlette):
    from tools.hands import indexing as _idx
    from tools.hands.indexing import index_workspace
    import _jobs

    _jobs.register_main_loop(asyncio.get_running_loop())

    # Open DB pool.
    try:
        from proxy.config import _memory_store
        if _memory_store is not None:
            await _memory_store.open_pool()
            print("[brain-server] DB pool opened.", file=sys.stderr)
    except Exception as exc:
        print(f"[brain-server] DB pool error (non-fatal): {exc}", file=sys.stderr)

    # Start file watcher.
    await _idx.load_watched_config()
    await _idx.start_watcher(index_workspace)

    tool_count = 0
    try:
        tool_count = len(list(mcp._tool_manager.list_tools()))
    except Exception:
        pass
    print(f"[brain-server] Startup complete. tools={tool_count} fp={BOOT_FINGERPRINT}", file=sys.stderr)

    async with mcp.session_manager.run():
        yield

    # Shutdown.
    await _idx.stop_watcher()
    try:
        import memory.store as memory_store
        await memory_store.close_pool()
    except ImportError:
        pass
    print("[brain-server] Shutdown complete.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Session middleware
# ---------------------------------------------------------------------------

class SessionContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        from _jobs import client_session_id
        session_id = _resolve_session_id(scope)

        # 2. Set ContextVar AND update
        token = client_session_id.set(session_id)
        try:
            await self.app(scope, receive, send)
        finally:
            client_session_id.reset(token)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

async def health(request: Request) -> JSONResponse:
    tool_count = 0
    try:
        tool_count = len(list(mcp._tool_manager.list_tools()))
    except Exception:
        pass
    return JSONResponse({
        "server": "GraphRAG MCP Brain",
        "standard": "Streamable HTTP (2025-03-26)",
        "transport_path": "/mcp",
        "tools": tool_count,
        "fingerprint": BOOT_FINGERPRINT,
        "status": "online",
    })


# ---------------------------------------------------------------------------
# Build the MCP Starlette app and wrap it
#
# mcp.streamable_http_app() returns a Starlette app with its own route at
# streamable_http_path (default: "/mcp").  We mount it at "/" so requests to
# /mcp pass through unchanged — no path-stripping double-prefix issue.
# The health Route("/") is checked FIRST so it takes priority over the mount.
# ---------------------------------------------------------------------------

_mcp_starlette = mcp.streamable_http_app()

app = Starlette(
    routes=[
        Route("/", health),          # health check — matched first
        Route("/health", health),    # also at /health
        Mount("/", app=_mcp_starlette),  # pass-through; MCP handles /mcp
    ],
    lifespan=lifespan,
    middleware=[
        Middleware(SessionContextMiddleware),
    ],
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")

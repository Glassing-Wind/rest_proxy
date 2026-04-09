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
import os
import resource
import sys
import time
from contextlib import asynccontextmanager
from uuid import uuid4

# ---------------------------------------------------------------------------
# Resource Limits (v2025-04-07)
#
# Programmatically increase the file handle limit (RLIMIT_NOFILE) to 65k+.
# This prevents "[Errno 24] Too many open files" during parallel indexing.
# ---------------------------------------------------------------------------

def _apply_resource_limits():
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min(65536, hard)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            print(f"[brain-server] RLIMIT_NOFILE increased: {soft} -> {target}", file=sys.stderr)
    except Exception as exc:
        print(f"[brain-server] Failed to set RLIMIT_NOFILE: {exc}", file=sys.stderr)

_apply_resource_limits()

from starlette.applications import Starlette
from starlette.datastructures import Headers
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
BOOT_ID: str = f"{os.getpid()}-{uuid4().hex[:8]}"
STARTED_AT: float = time.time()


def _resolve_session_id(scope) -> str:
    """Resolve the most specific available client session identifier."""
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


def _mcp_transport_session_id(scope) -> str | None:
    """Return the raw MCP transport session header, if present."""
    headers = Headers(scope=scope)
    return headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")


def _short_session_id(session_id: str | None) -> str:
    if not session_id:
        return "<new>"
    if len(session_id) <= 16:
        return session_id
    return f"{session_id[:8]}..{session_id[-4:]}"


def _session_snapshot(session_id: str | None) -> dict:
    known_session = False
    active_sessions = 0
    if session_id:
        try:
            known_session = session_id in mcp.session_manager._server_instances
        except Exception:
            known_session = False
    try:
        active_sessions = len(mcp.session_manager._server_instances)
    except Exception:
        active_sessions = 0
    return {
        "incoming_session_id": session_id,
        "incoming_session_short": _short_session_id(session_id),
        "known_session": known_session,
        "active_sessions": active_sessions,
    }


def _append_debug_headers(headers: list[tuple[bytes, bytes]], *, session_id: str | None) -> None:
    snapshot = _session_snapshot(session_id)
    headers.extend(
        [
            (b"x-graphrag-boot-id", BOOT_ID.encode("utf-8")),
            (b"x-graphrag-tool-fingerprint", BOOT_FINGERPRINT.encode("utf-8")),
            (b"x-graphrag-session-known", str(int(snapshot["known_session"])).encode("utf-8")),
            (b"x-graphrag-active-sessions", str(snapshot["active_sessions"]).encode("utf-8")),
        ]
    )


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
    print(
        f"[brain-server] Startup complete. boot={BOOT_ID} tools={tool_count} fp={BOOT_FINGERPRINT}",
        file=sys.stderr,
    )

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


class MCPRequestLoggingMiddleware:
    """
    Emit concise transport-level logging for Streamable HTTP MCP requests.

    This intentionally does not change behavior; it only makes session lifecycle
    explicit across restart boundaries.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/mcp":
            return await self.app(scope, receive, send)

        method = scope.get("method", "UNKNOWN")
        incoming_session_id = _mcp_transport_session_id(scope)
        snapshot = _session_snapshot(incoming_session_id)
        known_session = snapshot["known_session"]
        active_sessions = snapshot["active_sessions"]
        if incoming_session_id:
            print(
                "[brain-server] MCP request "
                f"boot={BOOT_ID} method={method} session={_short_session_id(incoming_session_id)} "
                f"known={int(known_session)} active_sessions={active_sessions}",
                file=sys.stderr,
            )

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                _append_debug_headers(headers, session_id=incoming_session_id)
                message["headers"] = headers
                status = message.get("status")
                created_session_id = None
                for raw_key, raw_value in headers:
                    if raw_key.decode("latin1").lower() == "mcp-session-id":
                        created_session_id = raw_value.decode("latin1")
                        break

                if created_session_id:
                    print(
                        "[brain-server] MCP session created "
                        f"boot={BOOT_ID} method={method} session={_short_session_id(created_session_id)}",
                        file=sys.stderr,
                    )
                elif status == 404 and incoming_session_id:
                    print(
                        "[brain-server] MCP stale session rejected "
                        f"boot={BOOT_ID} method={method} session={_short_session_id(incoming_session_id)} "
                        "status=404",
                        file=sys.stderr,
                    )

            await send(message)

        await self.app(scope, receive, send_wrapper)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

async def health(request: Request) -> JSONResponse:
    tool_count = 0
    try:
        tool_count = len(list(mcp._tool_manager.list_tools()))
    except Exception:
        pass
    incoming_session_id = _mcp_transport_session_id(request.scope)
    response = JSONResponse({
        "server": "GraphRAG MCP Brain",
        "standard": "Streamable HTTP (2025-03-26)",
        "transport_path": "/mcp",
        "tools": tool_count,
        "boot_id": BOOT_ID,
        "fingerprint": BOOT_FINGERPRINT,
        "uptime_seconds": round(max(0.0, time.time() - STARTED_AT), 3),
        "session": _session_snapshot(incoming_session_id),
        "status": "online",
    })
    _append_debug_headers(response.raw_headers, session_id=incoming_session_id)
    return response


async def server_fingerprint(request: Request) -> JSONResponse:
    incoming_session_id = _mcp_transport_session_id(request.scope)
    tool_count = 0
    try:
        tool_count = len(list(mcp._tool_manager.list_tools()))
    except Exception:
        pass
    response = JSONResponse({
        "boot_id": BOOT_ID,
        "fingerprint": BOOT_FINGERPRINT,
        "tools": tool_count,
        "uptime_seconds": round(max(0.0, time.time() - STARTED_AT), 3),
        "session": _session_snapshot(incoming_session_id),
    })
    _append_debug_headers(response.raw_headers, session_id=incoming_session_id)
    return response


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
        Route("/fingerprint", server_fingerprint),
        Mount("/", app=_mcp_starlette),  # pass-through; MCP handles /mcp
    ],
    lifespan=lifespan,
    middleware=[
        Middleware(SessionContextMiddleware),
        Middleware(MCPRequestLoggingMiddleware),
    ],
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")

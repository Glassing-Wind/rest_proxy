"""Privacy-bounded telemetry at existing ASGI and MCP execution boundaries.

Route names and tool names come from server code. Request paths, headers,
arguments and results are never passed to the telemetry layer.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

from graphrag_core.telemetry import capture_context, operation, use_context


_TOOL_SURFACE: ContextVar[str] = ContextVar("telemetry_tool_surface", default="mcp")
_SURFACES = frozenset({"mcp", "rest", "proxy", "stdio"})
_REQUEST_CONTEXT_KEY = "rest_proxy.telemetry.request_context"
_REQUEST_SURFACE_KEY = "rest_proxy.telemetry.request_surface"
_TOOL_REQUEST: ContextVar[object | None] = ContextVar("telemetry_tool_request", default=None)


@contextmanager
def transport_surface(surface: str):
    """Carry a code-selected transport label into nested tool execution."""
    token = _TOOL_SURFACE.set(surface if surface in _SURFACES else "mcp")
    try:
        yield
    finally:
        _TOOL_SURFACE.reset(token)


def instrument_mcp_tools(mcp) -> int:
    """Instrument Tool.run without changing functions, schemas or metadata.

    All SDK transports and the bounded REST dispatcher already use Tool.run.
    The manager's complete registry also contains tools hidden by the compact
    discovery profile. Instrument it once after registration.
    """
    from mcp.server.fastmcp.tools.base import Tool

    class TelemetryTool(Tool):
        async def run(self, arguments, context=None, convert_result=False):
            request = None
            try:
                request = context.request_context.request if context is not None else None
            except (AttributeError, ValueError):
                pass
            # Stateful HTTP sessions inherit the initialization POST's task
            # context. Attach the context retained on this message's Request
            # instead. A nested dispatcher already has the proper tool parent.
            first_http_tool = request is not None and request is not _TOOL_REQUEST.get()
            parent = nullcontext()
            surface = _TOOL_SURFACE.get()
            if first_http_tool:
                scope = getattr(request, "scope", {})
                parent = use_context(scope.get(_REQUEST_CONTEXT_KEY))
                surface = scope.get(_REQUEST_SURFACE_KEY, "mcp")
            token = _TOOL_REQUEST.set(request)
            try:
                with parent, transport_surface(surface), operation("tool", self.name, surface=surface):
                    return await super().run(arguments, context=context, convert_result=convert_result)
            finally:
                _TOOL_REQUEST.reset(token)

    count = 0
    try:
        registry = mcp._tool_manager._tools
        for name, tool in tuple(registry.items()):
            # Preserve extensions which own a custom Tool.run implementation.
            # Repeated installation skips our subclass as well.
            if type(tool) is not Tool:
                continue
            registry[name] = TelemetryTool.model_construct(
                _fields_set=tool.model_fields_set, **dict(tool)
            )
            count += 1
    except Exception:
        # Instrumentation is optional; registration and execution remain usable.
        pass
    return count


class RequestTelemetryMiddleware:
    """Measure request duration using a fixed, server-owned route operation map.

    An exact map suits the current endpoints, which have no path parameters.
    Any unrecognized path has the single http.unmatched label. Status codes
    affect only a fixed outcome; no response bodies are inspected.
    """

    def __init__(self, app, *, routes: dict[str, str], surface: str):
        self.app = app
        self.routes = dict(routes)
        self.surface = surface if surface in _SURFACES else "mcp"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        name = self.routes.get(scope.get("path"), "http.unmatched")
        surface = "rest" if name in {"evidence.read", "learning.write"} else self.surface
        with transport_surface(surface), operation("request", name, surface=surface) as current:
            scope[_REQUEST_CONTEXT_KEY] = capture_context()
            scope[_REQUEST_SURFACE_KEY] = surface
            async def measured_send(message):
                if message["type"] == "http.response.start":
                    status = message.get("status", 500)
                    if type(status) is int and status >= 400:
                        current.set_outcome("error")
                await send(message)

            return await self.app(scope, receive, measured_send)

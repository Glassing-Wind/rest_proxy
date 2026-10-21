"""Offline ASGI/MCP instrumentation contracts; no services or exporter endpoint.

SDK memory transports and ASGI clients exercise the real execution boundary.
Telemetry recording is replaced by a privacy-bounded in-memory fixture.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import importlib
import importlib.util
import inspect
import json
import os
import sys
import unittest
from unittest import mock

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ToolAnnotations
from mcp.types import LATEST_PROTOCOL_VERSION
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.requests import Request
from starlette.routing import Route

from graphrag_core import telemetry_transport
from graphrag_core import telemetry
from tools.brain.embedded_rest import make_read_endpoint
from tools.brain.primary import register_primary_dispatcher

try:
    SDK_AVAILABLE = importlib.util.find_spec("opentelemetry.sdk") is not None
except ImportError:
    SDK_AVAILABLE = False


@dataclass
class RecordedOperation:
    category: str
    name: str
    surface: str
    parent: "RecordedOperation | None"
    outcome: str = "ok"
    finished: bool = False

    def set_outcome(self, outcome):
        self.outcome = outcome


class Recorder:
    def __init__(self):
        self.operations = []
        self.current = ContextVar("test_current_operation", default=None)

    @contextmanager
    def operation(self, category, name, *, surface="internal", backend="none"):
        item = RecordedOperation(category, name, surface, self.current.get())
        self.operations.append(item)
        token = self.current.set(item)
        try:
            yield item
        except BaseException as error:
            item.outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
            raise
        finally:
            item.finished = True
            self.current.reset(token)


class TransportTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.recorder = Recorder()
        patcher = mock.patch.object(telemetry_transport, "operation", self.recorder.operation)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fixture(self):
        mcp = FastMCP("telemetry-memory-fixture")

        async def profile(project_id: str, max_files: int = 20) -> dict:
            return {"project_id": project_id, "max_files": max_files}

        mcp.add_tool(profile, name="get_repository_learning_profile", title="Profile",
                     annotations=ToolAnnotations(readOnlyHint=True), meta={"fixture": True})
        return mcp

    async def test_installation_preserves_function_metadata_schema_and_catalog(self):
        mcp = self.fixture()
        original = mcp._tool_manager.get_tool("get_repository_learning_profile")
        before = [tool.model_dump() for tool in await mcp.list_tools()]
        self.assertEqual(telemetry_transport.instrument_mcp_tools(mcp), 1)
        wrapped = mcp._tool_manager.get_tool(original.name)
        self.assertIs(wrapped.fn, original.fn)
        self.assertIs(wrapped.fn_metadata, original.fn_metadata)
        self.assertEqual(inspect.getsourcefile(wrapped.fn), inspect.getsourcefile(original.fn))
        self.assertEqual(inspect.signature(wrapped.fn), inspect.signature(original.fn))
        self.assertEqual([tool.model_dump() for tool in await mcp.list_tools()], before)
        self.assertEqual(telemetry_transport.instrument_mcp_tools(mcp), 0)
        self.assertEqual(await wrapped.run({"project_id": "private"}),
                         {"project_id": "private", "max_files": 20})
        self.assertEqual(len(self.recorder.operations), 1)

    async def test_sdk_and_rest_share_one_tool_span_with_unchanged_output(self):
        mcp = self.fixture()
        telemetry_transport.instrument_mcp_tools(mcp)
        app = Starlette(routes=[Route("/evidence/read", make_read_endpoint(mcp), methods=["POST"])])
        app.add_middleware(telemetry_transport.RequestTelemetryMiddleware, surface="mcp",
                           routes={"/evidence/read": "evidence.read"})
        secret = "private-project-and-source"
        async with create_connected_server_and_client_session(mcp) as session:
            response = await session.call_tool("get_repository_learning_profile", {"project_id": secret})
            self.assertFalse(response.isError)
            self.assertEqual(json.loads(response.content[0].text), {"project_id": secret, "max_files": 20})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            result = await client.post("/evidence/read?private-query=credential", json={
                "tool": "get_repository_learning_profile", "arguments": {"project_id": secret}},
                headers={"authorization": "Bearer private-token", "x-session-id": "private-session",
                         "baggage": "secret=private-baggage"})
        self.assertEqual(result.json(), json.loads(response.content[0].text))
        self.assertEqual([(item.category, item.name, item.surface) for item in self.recorder.operations], [
            ("tool", "get_repository_learning_profile", "mcp"),
            ("request", "evidence.read", "rest"),
            ("tool", "get_repository_learning_profile", "rest"),
        ])
        self.assertIs(self.recorder.operations[-1].parent, self.recorder.operations[-2])
        self.assertNotIn("private", repr(self.recorder.operations))

    async def test_schema_validation_errors_and_unknown_names_do_not_change_sdk_errors(self):
        mcp = self.fixture()
        telemetry_transport.instrument_mcp_tools(mcp)
        async with create_connected_server_and_client_session(mcp) as session:
            invalid = await session.call_tool("get_repository_learning_profile", {})
            unknown = await session.call_tool("private-unknown-tool", {"private": "argument"})
        self.assertTrue(invalid.isError)
        self.assertTrue(unknown.isError)
        self.assertEqual(len(self.recorder.operations), 1)
        self.assertEqual(self.recorder.operations[0].outcome, "error")
        self.assertNotIn("private", repr(self.recorder.operations))

    async def test_sync_tool_and_compact_dispatch_use_existing_run_boundary(self):
        mcp = FastMCP("telemetry-dispatch-fixture")

        def definitions(symbol: str) -> dict:
            return {"symbol": symbol}

        mcp.add_tool(definitions, name="find_definitions")
        register_primary_dispatcher(mcp)
        telemetry_transport.instrument_mcp_tools(mcp)
        async with create_connected_server_and_client_session(mcp) as session:
            result = await session.call_tool("dispatch_deep_analysis", {
                "tool_name": "find_definitions", "arguments": {"symbol": "private-symbol"}})
        self.assertFalse(result.isError)
        self.assertEqual(json.loads(result.content[0].text), {"symbol": "private-symbol"})
        self.assertEqual([item.name for item in self.recorder.operations],
                         ["dispatch_deep_analysis", "find_definitions"])
        self.assertIs(self.recorder.operations[1].parent, self.recorder.operations[0])
        self.assertNotIn("private", repr(self.recorder.operations))

    async def test_tool_failure_and_cancellation_are_recorded_without_exception_text(self):
        mcp = FastMCP("telemetry-errors-fixture")

        async def fail() -> dict:
            raise RuntimeError("private-provider-key")

        started = asyncio.Event()

        async def block() -> dict:
            started.set()
            await asyncio.Event().wait()
            return {}

        mcp.add_tool(fail, name="search_codebase")
        mcp.add_tool(block, name="get_project_overview")
        telemetry_transport.instrument_mcp_tools(mcp)
        with self.assertRaisesRegex(Exception, "private-provider-key"):
            await mcp._tool_manager.get_tool("search_codebase").run({})
        task = asyncio.create_task(mcp._tool_manager.get_tool("get_project_overview").run({}))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual([item.outcome for item in self.recorder.operations], ["error", "cancelled"])
        self.assertNotIn("private", repr(self.recorder.operations))

    async def test_unknown_http_paths_and_error_response_have_fixed_labels(self):
        async def endpoint(request):
            return JSONResponse({"private": "result"}, status_code=503)

        app = Starlette(routes=[Route("/ready", endpoint)])
        app.add_middleware(telemetry_transport.RequestTelemetryMiddleware, surface="proxy",
                           routes={"/ready": "ready"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            self.assertEqual((await client.get("/ready")).status_code, 503)
            self.assertEqual((await client.get("/private/secret?token=private")).status_code, 404)
        self.assertEqual([(item.name, item.surface, item.outcome) for item in self.recorder.operations],
                         [("ready", "proxy", "error"), ("http.unmatched", "proxy", "error")])
        self.assertNotIn("private", repr(self.recorder.operations))

    async def test_streaming_request_finishes_after_body_and_cancellation_propagates(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def streaming(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            started.set()
            await release.wait()
            await send({"type": "http.response.body", "body": b"private-source", "more_body": False})

        middleware = telemetry_transport.RequestTelemetryMiddleware(
            streaming, routes={"/v1/chat/completions": "proxy.chat"}, surface="proxy")
        send = mock.AsyncMock()
        scope = {"type": "http", "path": "/v1/chat/completions"}
        task = asyncio.create_task(middleware(scope, mock.AsyncMock(), send))
        await started.wait()
        self.assertFalse(self.recorder.operations[0].finished)
        release.set()
        await task
        self.assertTrue(self.recorder.operations[0].finished)
        self.assertEqual(self.recorder.operations[0].outcome, "ok")
        release.clear()
        started.clear()
        task = asyncio.create_task(middleware(scope, mock.AsyncMock(), send))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.recorder.operations[-1].finished)
        self.assertEqual(self.recorder.operations[-1].outcome, "cancelled")
        self.assertNotIn("private", repr(self.recorder.operations))

    async def test_non_http_scopes_pass_through_without_spans(self):
        downstream = mock.AsyncMock()
        middleware = telemetry_transport.RequestTelemetryMiddleware(downstream, routes={}, surface="mcp")
        receive, send = mock.AsyncMock(), mock.AsyncMock()
        scope = {"type": "lifespan"}
        await middleware(scope, receive, send)
        downstream.assert_awaited_once_with(scope, receive, send)
        self.assertEqual(self.recorder.operations, [])

    async def test_stdio_surface_context_is_reset_after_tool_call(self):
        mcp = self.fixture()
        telemetry_transport.instrument_mcp_tools(mcp)
        with telemetry_transport.transport_surface("stdio"):
            await mcp._tool_manager.get_tool("get_repository_learning_profile").run({"project_id": "private"})
        await mcp._tool_manager.get_tool("get_repository_learning_profile").run({"project_id": "private"})
        self.assertEqual([item.surface for item in self.recorder.operations], ["stdio", "mcp"])


class TelemetryLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_details_report_only_safe_telemetry_status(self):
        with mock.patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1", "LM_PROXY_MEMORY_ENABLED": "0"}):
            modules = [importlib.import_module("proxy.app"), importlib.import_module("brain_server")]
        expected = {"enabled": False, "state": "unavailable", "exporter": "none"}
        for module in modules:
            monitor = mock.Mock(check=mock.AsyncMock(return_value=(503, {"ready": False})))
            request = Request({"type": "http", "app": module.app})
            with mock.patch.object(module, "ReadinessMonitor", return_value=monitor), \
                    mock.patch.object(module, "telemetry_status", return_value=expected):
                response = await module.health_details(request)
            self.assertEqual(json.loads(response.body), {"ready": False, "telemetry": expected})

    async def test_server_lifespans_stop_telemetry_after_inner_cleanup_and_startup_failure(self):
        environment = {"PYTHON_DOTENV_DISABLED": "1", "LM_PROXY_MEMORY_ENABLED": "0",
                       "LM_PROXY_WATCHER_ENABLED": "0", "LM_PROXY_ENABLE_TELEMETRY": "0"}
        with mock.patch.dict(os.environ, environment):
            modules = [importlib.import_module("proxy.app"), importlib.import_module("brain_server")]
        for module, service in zip(modules, ("proxy", "mcp")):
            for fail in (False, True):
                with self.subTest(module=module.__name__, startup_failure=fail):
                    events = []
                    handle = object()

                    @asynccontextmanager
                    async def inner(app):
                        events.append("inner-start")
                        if fail:
                            raise RuntimeError("private-startup-error")
                        try:
                            yield
                        finally:
                            events.append("inner-stop")

                    def start(*, service):
                        events.append("telemetry-start")
                        return handle

                    async def stop(value):
                        self.assertIs(value, handle)
                        events.append("telemetry-stop")

                    with mock.patch.object(module, "_application_lifespan", inner), \
                            mock.patch.object(module, "start_telemetry", side_effect=start) as starter, \
                            mock.patch.object(module, "stop_telemetry", side_effect=stop):
                        if fail:
                            with self.assertRaises(RuntimeError):
                                async with module.lifespan(module.app):
                                    self.fail("failed startup yielded")
                        else:
                            async with module.lifespan(module.app):
                                events.append("request")
                        starter.assert_called_once_with(service=service)
                    self.assertEqual(events, ["telemetry-start", "inner-start", "telemetry-stop"] if fail else
                                     ["telemetry-start", "inner-start", "request", "inner-stop", "telemetry-stop"])

    async def test_stdio_entrypoint_owns_provider_without_protocol_stdout(self):
        stdout = sys.stdout
        try:
            with mock.patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1", "LM_PROXY_MEMORY_ENABLED": "0"}):
                module = importlib.import_module("mcp_server")
        finally:
            sys.stdout = stdout
        handle = object()
        events = []

        async def serve():
            events.append("serve")
            self.assertEqual(telemetry_transport._TOOL_SURFACE.get(), "stdio")

        async def stop(value):
            self.assertIs(value, handle)
            events.append("stop")

        with mock.patch.object(module, "start_telemetry", return_value=handle) as start, \
                mock.patch.object(module, "stop_telemetry", side_effect=stop), \
                mock.patch.object(module, "_serve_stdio", side_effect=serve), mock.patch("sys.stdout") as output:
            await module.main()
        start.assert_called_once_with(service="stdio")
        self.assertEqual(events, ["serve", "stop"])
        output.write.assert_not_called()
        self.assertEqual(telemetry_transport._TOOL_SURFACE.get(), "mcp")


@unittest.skipUnless(SDK_AVAILABLE, "Optional OpenTelemetry SDK not installed")
class SdkTransportTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_stateful_http_calls_attach_to_their_own_request_after_initialization(self):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        engine = telemetry._build_engine(None, span_exporter=exporter, metric_reader=InMemoryMetricReader())
        with mock.patch.dict(os.environ, {"LM_PROXY_TELEMETRY_ENABLED": "1", "LM_PROXY_OTEL_ENDPOINT": ""}), \
                mock.patch.object(telemetry, "_build_engine", return_value=engine):
            handle = telemetry.start("mcp")
        try:
            mcp = FastMCP("stateful-http-telemetry-fixture", stateless_http=False, json_response=True)
            concurrent_started = set()
            both_started = asyncio.Event()

            async def profile(project_id: str) -> dict:
                if project_id.startswith("concurrent"):
                    concurrent_started.add(project_id)
                    if len(concurrent_started) == 2:
                        both_started.set()
                    await asyncio.wait_for(both_started.wait(), 2)
                with telemetry.operation("graph", "graph.execute_read", backend="neo4j"):
                    await asyncio.sleep(0)
                return {"source": project_id}

            mcp.add_tool(profile, name="get_repository_learning_profile")
            telemetry_transport.instrument_mcp_tools(mcp)
            app = telemetry_transport.RequestTelemetryMiddleware(
                mcp.streamable_http_app(), routes={"/mcp": "mcp.http"}, surface="mcp")
            async with mcp.session_manager.run(), httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
                headers = {"accept": "application/json, text/event-stream"}
                initialized = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                        "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                        "clientInfo": {"name": "private-fixture", "version": "1"}}})
                self.assertEqual(initialized.status_code, 200)
                headers.update({"mcp-session-id": initialized.headers["mcp-session-id"],
                                "mcp-protocol-version": LATEST_PROTOCOL_VERSION})
                notification = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "method": "notifications/initialized"})
                self.assertEqual(notification.status_code, 202)
                initialization_spans = {span.context.span_id for span in exporter.get_finished_spans()}

                async def call(identifier, project):
                    return await client.post("/mcp", headers=headers, json={
                        "jsonrpc": "2.0", "id": identifier, "method": "tools/call", "params": {
                            "name": "get_repository_learning_profile", "arguments": {"project_id": project}}})

                sequential = await call(1, "private-sequential-source")
                self.assertEqual(sequential.status_code, 200)
                first_calls = [span for span in exporter.get_finished_spans()
                               if span.context.span_id not in initialization_spans]
                first_request = next(span for span in first_calls if span.attributes["operation.category"] == "request")
                first_tool = next(span for span in first_calls if span.attributes["operation.category"] == "tool")
                self.assertEqual(first_tool.parent.span_id, first_request.context.span_id)
                self.assertNotIn(first_tool.parent.span_id, initialization_spans)
                before_concurrent = {span.context.span_id for span in exporter.get_finished_spans()}
                results = await asyncio.gather(call(2, "concurrent-private-one"), call(3, "concurrent-private-two"))
                self.assertEqual([result.status_code for result in results], [200, 200])
                self.assertTrue(all(not result.json()["result"].get("isError", False) for result in results))
                concurrent = [span for span in exporter.get_finished_spans()
                              if span.context.span_id not in before_concurrent]
                request_ids = {span.context.span_id for span in concurrent
                               if span.attributes["operation.category"] == "request"}
                tools = [span for span in concurrent if span.attributes["operation.category"] == "tool"]
                graphs = [span for span in concurrent if span.attributes["operation.category"] == "graph"]
                self.assertEqual(len(request_ids), 2)
                self.assertEqual({span.parent.span_id for span in tools}, request_ids)
                self.assertEqual({span.parent.span_id for span in graphs}, {span.context.span_id for span in tools})
                self.assertEqual(len({span.context.trace_id for span in concurrent}), 2)
            for span in exporter.get_finished_spans():
                self.assertNotIn("private", repr(dict(span.attributes)))
        finally:
            await telemetry.stop(handle)

    async def test_real_sdk_asgi_tool_backend_chain_has_bounded_attributes_and_parentage(self):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
        engine = telemetry._build_engine(None, span_exporter=exporter, metric_reader=reader)
        with mock.patch.dict(os.environ, {"LM_PROXY_TELEMETRY_ENABLED": "1", "LM_PROXY_OTEL_ENDPOINT": ""}), \
                mock.patch.object(telemetry, "_build_engine", return_value=engine):
            handle = telemetry.start("mcp")
        self.assertTrue(handle.enabled)
        try:
            mcp = FastMCP("real-sdk-telemetry-fixture")

            async def profile(project_id: str) -> dict:
                with telemetry.operation("graph", "graph.execute_read", backend="neo4j"):
                    await asyncio.sleep(0)
                return {"source": project_id}

            mcp.add_tool(profile, name="get_repository_learning_profile")
            telemetry_transport.instrument_mcp_tools(mcp)
            app = Starlette(routes=[Route("/evidence/read", make_read_endpoint(mcp), methods=["POST"])])
            app.add_middleware(telemetry_transport.RequestTelemetryMiddleware, surface="mcp",
                               routes={"/evidence/read": "evidence.read"})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
                response = await client.post("/evidence/read?secret=private-query", json={
                    "tool": "get_repository_learning_profile", "arguments": {"project_id": "private-source"}},
                    headers={"traceparent": "00-ffffffffffffffffffffffffffffffff-ffffffffffffffff-01",
                             "baggage": "private=baggage", "authorization": "Bearer private-token"})
            self.assertEqual(response.json(), {"source": "private-source"})
            spans = {span.name: span for span in exporter.get_finished_spans()}
            self.assertEqual(len(spans), 3)
            request = next(span for span in spans.values() if span.attributes["operation.category"] == "request")
            tool = next(span for span in spans.values() if span.attributes["operation.category"] == "tool")
            graph = next(span for span in spans.values() if span.attributes["operation.category"] == "graph")
            self.assertIsNone(request.parent)
            self.assertNotEqual(request.context.trace_id, int("f" * 32, 16))
            self.assertEqual(tool.parent.span_id, request.context.span_id)
            self.assertEqual(graph.parent.span_id, tool.context.span_id)
            self.assertEqual({span.context.trace_id for span in spans.values()}, {request.context.trace_id})
            self.assertEqual(tool.attributes["operation.surface"], "rest")
            for span in spans.values():
                self.assertEqual(set(span.attributes), telemetry._ATTRIBUTE_KEYS)
                self.assertEqual(dict(span.resource.attributes), {"service.name": "rest-proxy"})
                self.assertEqual(span.events, ())
                self.assertNotIn("private", repr(dict(span.attributes)))
            metrics = reader.get_metrics_data()
            counts = [point for resource in metrics.resource_metrics for scope in resource.scope_metrics
                      for metric in scope.metrics if metric.name == "rest_proxy.operation.count"
                      for point in metric.data.data_points]
            self.assertEqual(sum(point.value for point in counts), 3)
            self.assertTrue(all(set(point.attributes) == telemetry._ATTRIBUTE_KEYS for point in counts))
        finally:
            await telemetry.stop(handle)


if __name__ == "__main__":
    unittest.main()

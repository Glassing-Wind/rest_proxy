"""Offline telemetry tests; SDK tests skip when the optional full profile is absent.

No database, model, workspace indexing or production service is started. The OTLP
acceptance test binds only a disposable loopback HTTP collector.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from graphrag_core import telemetry

try:
    from opentelemetry import trace
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricExportResult
    from opentelemetry.sdk.trace.export import SpanExportResult
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import NonRecordingSpan, SpanContext, StatusCode, TraceFlags
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


class DisabledTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_and_disabled_scope_do_not_import_sdk_or_start_clients(self) -> None:
        code = """
import asyncio, os, sys
os.environ['LM_PROXY_TELEMETRY_ENABLED'] = '0'
from graphrag_core import telemetry
handle = telemetry.start('mcp')
with telemetry.operation('tool', 'search_codebase'):
    pass
asyncio.run(telemetry.stop(handle))
assert not any(key.startswith('opentelemetry') for key in sys.modules)
assert not handle.enabled and handle.state == 'disabled'
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_missing_sdk_and_invalid_endpoint_are_safe_fixed_states(self) -> None:
        with patch.dict(os.environ, {"LM_PROXY_TELEMETRY_ENABLED": "1", "LM_PROXY_OTEL_ENDPOINT": ""}):
            with patch.object(telemetry, "_build_engine", side_effect=ImportError("private SDK error")):
                handle = telemetry.start("mcp")
            self.assertFalse(handle.enabled)
            self.assertEqual(handle.state, "unavailable")
            self.assertEqual(telemetry.status(), {"enabled": False, "state": "unavailable", "exporter": "none",
                                                "delivery": None})
            with telemetry.operation("request", "http.request"):
                result = "normal request result"
            self.assertEqual(result, "normal request result")
            await telemetry.stop(handle)
            for endpoint in ["ftp://localhost", "http://user:password@localhost", "http://localhost?secret=x",
                             "http://localhost#secret", "http://localhost:0", "http://localhost:broken",
                             "http://localhost/v1/traces", "http://localhost/\nsecret"]:
                with self.subTest(endpoint_kind=endpoint.split(":")[0]):
                    with patch.dict(os.environ, {"LM_PROXY_OTEL_ENDPOINT": endpoint}):
                        with patch.object(telemetry, "_build_engine") as builder:
                            refused = telemetry.start("mcp")
                        builder.assert_not_called()
                        self.assertEqual(refused.state, "invalid-endpoint")
            with patch.dict(os.environ, {"LM_PROXY_TELEMETRY_ENABLED": "0"}):
                self.assertEqual(telemetry.start("mcp").state, "disabled")
                self.assertEqual(telemetry.status()["state"], "disabled")

    async def test_disabled_decorators_preserve_results_failures_and_cancellation(self) -> None:
        with patch.dict(os.environ, {"LM_PROXY_TELEMETRY_ENABLED": "0"}):
            handle = telemetry.start("stdio")

            @telemetry.instrument("parser", "repository.parse")
            def sync_call(value: str) -> str:
                return value

            @telemetry.instrument("tool", "search_codebase")
            async def async_call(value: str) -> str:
                return value

            @telemetry.instrument("tool", "search_codebase")
            async def cancel_call() -> None:
                raise asyncio.CancelledError()

            self.assertEqual(sync_call("source value"), "source value")
            self.assertEqual(await async_call("source value"), "source value")
            with self.assertRaises(asyncio.CancelledError):
                await cancel_call()
            with self.assertRaisesRegex(ValueError, "caller error"):
                with telemetry.operation("tool", "search_codebase"):
                    raise ValueError("caller error")
            await telemetry.stop(handle)


@unittest.skipUnless(SDK_AVAILABLE, "Optional OpenTelemetry SDK not installed")
class TelemetrySdkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.environment = patch.dict(os.environ, {
            "LM_PROXY_TELEMETRY_ENABLED": "1", "LM_PROXY_OTEL_ENDPOINT": "", "OTEL_SDK_DISABLED": "false",
        })
        self.environment.start()
        self.spans = InMemorySpanExporter()
        self.reader = InMemoryMetricReader()
        self.engine = telemetry._build_engine(None, span_exporter=self.spans, metric_reader=self.reader)
        self.builder = patch.object(telemetry, "_build_engine", return_value=self.engine)
        self.builder.start()
        self.handle = telemetry.start("mcp")
        self.assertTrue(self.handle.enabled)

    async def asyncTearDown(self) -> None:
        await telemetry.stop(self.handle)
        self.builder.stop()
        self.environment.stop()

    def metrics(self) -> dict:
        data = self.reader.get_metrics_data()
        return {metric.name: metric for resource in data.resource_metrics for scope in resource.scope_metrics
                for metric in scope.metrics}

    async def test_duration_seconds_counts_and_fixed_outcomes(self) -> None:
        for outcome in ["ok", "partial", "unavailable", "conflict", "empty"]:
            with telemetry.operation("learning", "learning.read", backend="sqlite") as scope:
                scope.set_outcome(outcome)
        metrics = self.metrics()
        duration = metrics["rest_proxy.operation.duration"]
        counter = metrics["rest_proxy.operation.count"]
        self.assertEqual(duration.unit, "s")
        self.assertEqual(counter.unit, "{operation}")
        self.assertEqual({point.attributes["operation.outcome"] for point in counter.data.data_points},
                         {"ok", "partial", "unavailable", "conflict", "empty"})
        self.assertEqual(sum(point.value for point in counter.data.data_points), 5)
        self.assertEqual(sum(point.count for point in duration.data.data_points), 5)
        self.assertTrue(all(point.sum >= 0 for point in duration.data.data_points))
        self.assertTrue(all(point.explicit_bounds[-1] == 60 for point in duration.data.data_points))
        self.assertTrue(all(not point.exemplars for point in duration.data.data_points))

    async def test_errors_and_cancellation_preserve_caller_behavior_without_exception_details(self) -> None:
        secret = "SECRET_ERROR_MESSAGE_AND_SOURCE"
        with self.assertRaisesRegex(ValueError, secret):
            with telemetry.operation("graph", "graph.execute_read", backend="neo4j"):
                raise ValueError(secret)
        with self.assertRaises(asyncio.CancelledError):
            with telemetry.operation("tool", "search_codebase"):
                raise asyncio.CancelledError(secret)
        error, cancellation = self.spans.get_finished_spans()
        self.assertEqual(error.status.status_code, StatusCode.ERROR)
        self.assertIsNone(error.status.description)
        self.assertEqual(error.attributes["operation.outcome"], "error")
        self.assertEqual(cancellation.attributes["operation.outcome"], "cancelled")
        self.assertFalse(error.events or cancellation.events)
        self.assertNotIn(secret, json.dumps([span.to_json() for span in [error, cancellation]]))
        self.assertNotIn(secret, repr(self.reader.get_metrics_data()))

    async def test_unknown_labels_and_outcomes_never_escape_allowlist(self) -> None:
        secret = "SOURCE_QUERY_HEADERS_SESSION_PROJECT_SECRET"
        with telemetry.operation(secret, secret, backend=secret, surface=secret) as scope:
            scope.set_outcome(secret)
        with telemetry.operation("tool", "new_unreviewed_tool_secret"):
            pass
        spans = self.spans.get_finished_spans()
        self.assertEqual(spans[0].name, "other")
        self.assertEqual(set(spans[0].attributes.values()), {"other", "ok"})
        self.assertEqual(spans[1].attributes["operation.name"], "other")
        self.assertNotIn(secret, repr(self.reader.get_metrics_data()))
        self.assertNotIn(secret, "".join(span.to_json() for span in spans))

    async def test_async_thread_parentage_and_foreign_context_exclusion(self) -> None:
        foreign = NonRecordingSpan(SpanContext(123456, 7890, is_remote=True, trace_flags=TraceFlags(1)))
        global_provider = trace.get_tracer_provider()
        with trace.use_span(foreign):
            with telemetry.operation("tool", "search_codebase"):
                self.assertIs(trace.get_current_span(), foreign)
                with telemetry.operation("retrieval", "repository.search", backend="ladybug"):
                    await asyncio.sleep(0)
                    await asyncio.to_thread(self.thread_child)
            self.assertIs(trace.get_current_span(), foreign)
        child, retrieval, root = self.spans.get_finished_spans()
        self.assertIsNone(root.parent)
        self.assertEqual(retrieval.parent.span_id, root.context.span_id)
        self.assertEqual(child.parent.span_id, retrieval.context.span_id)
        self.assertEqual({span.context.trace_id for span in [child, retrieval, root]}, {root.context.trace_id})
        self.assertNotEqual(root.context.trace_id, foreign.get_span_context().trace_id)
        self.assertIs(trace.get_tracer_provider(), global_provider)

    @staticmethod
    def thread_child() -> None:
        with telemetry.operation("parser", "repository.parse", backend="ts_pack"):
            pass

    async def test_request_bridge_clears_inherited_session_context_and_restores_internal_context(self) -> None:
        with telemetry.operation("request", "mcp.http", surface="mcp"):
            request_context = telemetry.capture_context()
        with telemetry.use_context(request_context):
            with telemetry.operation("tool", "search_codebase", surface="mcp"):
                with telemetry.use_context(None):
                    with telemetry.operation("request", "mcp.http", surface="mcp"):
                        pass
                with telemetry.operation("graph", "graph.execute_read", backend="ladybug"):
                    pass
        request, independent, graph, tool = self.spans.get_finished_spans()
        self.assertEqual(tool.parent.span_id, request.context.span_id)
        self.assertEqual(graph.parent.span_id, tool.context.span_id)
        self.assertIsNone(independent.parent)
        self.assertIsNone(telemetry.capture_context())

    async def test_decorators_do_not_capture_arguments_results_or_function_names(self) -> None:
        secret = "PRIVATE_ORIGINAL_SOURCE_AND_RESULT"

        @telemetry.instrument("parser", "repository.parse")
        def private_function_name(value: str) -> dict:
            return {"result": value}

        @telemetry.instrument("analyzer", "analyzer.profile", backend="repoanalyzer")
        async def private_async_function_name(value: str) -> dict:
            return private_function_name(value)

        self.assertEqual(await private_async_function_name(secret), {"result": secret})
        rendered = "".join(span.to_json() for span in self.spans.get_finished_spans())
        self.assertNotIn(secret, rendered)
        self.assertNotIn("private_function_name", rendered)
        self.assertEqual(private_function_name.__name__, "private_function_name")

    async def test_multiple_lifespans_cannot_shutdown_each_others_provider(self) -> None:
        second = telemetry.start("proxy")
        with patch.object(self.engine, "shutdown", wraps=self.engine.shutdown) as shutdown:
            await telemetry.stop(self.handle)
            shutdown.assert_not_called()
            self.assertTrue(telemetry.status()["enabled"])
            with telemetry.operation("request", "proxy.models", surface="proxy"):
                pass
            await telemetry.stop(second)
            shutdown.assert_called_once()
            self.assertFalse(telemetry.status()["enabled"])
            await telemetry.stop(second)
            shutdown.assert_called_once()

    async def test_faulty_instrumentation_does_not_break_operation_or_exception(self) -> None:
        with patch.object(self.engine.tracer, "start_span", side_effect=RuntimeError("private failure")):
            with telemetry.operation("tool", "search_codebase"):
                result = 42
        self.assertEqual(result, 42)
        with patch.object(self.engine.counter, "add", side_effect=RuntimeError("private failure")):
            with self.assertRaisesRegex(ValueError, "actual request failure"):
                with telemetry.operation("graph", "graph.execute_read"):
                    raise ValueError("actual request failure")

    async def test_ambient_span_mutation_cannot_inject_source_into_private_spans(self) -> None:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.trace import Status

        foreign_provider = TracerProvider(resource=Resource({"service.name": "foreign-fixture"}), shutdown_on_exit=False)
        foreign = foreign_provider.get_tracer("foreign-fixture").start_span("foreign")
        secret = "PRIVATE_SOURCE_AND_EXCEPTION_FROM_OTHER_INSTRUMENTOR"
        try:
            with trace.use_span(foreign, record_exception=False, set_status_on_exception=False):
                with telemetry.operation("graph", "graph.execute_read", backend="ladybug"):
                    self.assertIs(trace.get_current_span(), foreign)
                    trace.get_current_span().set_attribute("db.statement", secret)
                    trace.get_current_span().update_name(secret)
                    trace.get_current_span().set_status(Status(StatusCode.ERROR, secret))
                    trace.get_current_span().record_exception(ValueError(secret))
            span = self.spans.get_finished_spans()[0]
            self.assertEqual(span.name, "graph.execute_read")
            self.assertEqual(set(span.attributes), telemetry._ATTRIBUTE_KEYS)
            self.assertFalse(span.events)
            self.assertIsNone(span.status.description)
            self.assertNotIn(secret, span.to_json())
        finally:
            foreign.end()
            foreign_provider.shutdown()

    async def test_local_mode_ignores_ambient_exporter_and_resource_environment(self) -> None:
        await telemetry.stop(self.handle)
        self.builder.stop()
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://example.invalid",
                                    "OTEL_RESOURCE_ATTRIBUTES": "project=SECRET_RESOURCE,user.id=SECRET_ID",
                                    "OTEL_EXPORTER_OTLP_HEADERS": "authorization=SECRET_TOKEN"}):
            with patch.object(telemetry, "_http_exporters") as exporters:
                self.handle = telemetry.start("stdio")
                exporters.assert_not_called()
            self.assertEqual(telemetry._engine.tracer_provider.resource.attributes, {"service.name": "rest-proxy"})
            self.assertEqual(telemetry._engine.meter_provider._sdk_config.resource.attributes,
                             {"service.name": "rest-proxy"})
            self.assertEqual(telemetry.status(), {"enabled": True, "state": "local", "exporter": "none", "delivery": None})

    async def test_http_exporters_drop_network_failures_without_exception_logging(self) -> None:
        client = Mock()
        client.stream.side_effect = RuntimeError("SECRET_NETWORK_ERROR")
        delivery = telemetry._DeliveryState()
        with patch("httpx.Client", return_value=client) as constructor:
            span_exporter, metric_exporter = telemetry._http_exporters("http://example.invalid", delivery=delivery)
        self.assertEqual(constructor.call_count, 2)
        constructor.assert_called_with(timeout=1.0, trust_env=False, follow_redirects=False)
        with telemetry.operation("tool", "search_codebase"):
            pass
        with self.assertNoLogs("opentelemetry", level="ERROR"):
            self.assertEqual(span_exporter.export(self.spans.get_finished_spans()), SpanExportResult.FAILURE)
            self.assertEqual(metric_exporter.export(self.reader.get_metrics_data()), MetricExportResult.FAILURE)
        self.assertEqual(delivery.snapshot(), {"trace_export": "error", "metric_export": "error",
                                               "trace_export_failures": 1, "metric_export_failures": 1})
        for call in client.stream.call_args_list:
            self.assertEqual(call.kwargs["headers"], {"Content-Type": "application/x-protobuf"})
        span_exporter.shutdown()
        metric_exporter.shutdown()
        self.assertEqual(client.close.call_count, 2)

    async def test_collector_reply_body_is_not_consumed_and_http_failure_is_visible(self) -> None:
        released = threading.Event()
        received = []

        class Collector(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers["Content-Length"]))
                received.append(self.path)
                self.send_response(503, "SECRET_REMOTE_REASON")
                self.send_header("Content-Length", str(1 << 30))
                self.end_headers()
                released.wait(5)

            def log_message(self, *args) -> None:
                pass

        collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
        thread = threading.Thread(target=collector.serve_forever, daemon=True)
        thread.start()
        delivery = telemetry._DeliveryState()
        span_exporter, metric_exporter = telemetry._http_exporters(
            f"http://127.0.0.1:{collector.server_port}", delivery=delivery)
        try:
            with telemetry.operation("tool", "search_codebase"):
                pass
            started = time.monotonic()
            self.assertEqual(await asyncio.to_thread(span_exporter.export, self.spans.get_finished_spans()),
                             SpanExportResult.FAILURE)
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(delivery.snapshot()["trace_export"], "error")
            self.assertEqual(delivery.snapshot()["trace_export_failures"], 1)
            self.assertNotIn("SECRET", repr(delivery.snapshot()))
            self.assertEqual(received, ["/v1/traces"])
        finally:
            released.set()
            span_exporter.shutdown()
            metric_exporter.shutdown()
            await asyncio.to_thread(collector.shutdown)
            collector.server_close()
            thread.join(timeout=1)

    async def test_partial_client_start_failure_closes_first_client(self) -> None:
        client = Mock()
        with patch("httpx.Client", side_effect=[client, RuntimeError("SECRET_START_FAILURE")]):
            with self.assertRaises(RuntimeError):
                telemetry._http_exporters("http://example.invalid")
        client.close.assert_called_once()

    async def test_shutdown_cancellation_is_not_swallowed(self) -> None:
        released = threading.Event()
        try:
            with patch.object(self.engine, "shutdown", side_effect=lambda: released.wait(5)):
                task = asyncio.create_task(telemetry.stop(self.handle))
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(telemetry.status()["enabled"])
        finally:
            released.set()
            self.engine.shutdown()

    async def test_shutdown_thread_start_failure_is_safe(self) -> None:
        with patch("threading.Thread.start", side_effect=RuntimeError("SECRET_THREAD_START")):
            await telemetry.stop(self.handle)
        self.assertFalse(telemetry.status()["enabled"])
        self.engine.shutdown()

    async def test_bounded_shutdown_detaches_failed_provider_and_keeps_event_loop_responsive(self) -> None:
        released = threading.Event()
        try:
            with patch.object(self.engine, "shutdown", side_effect=lambda: released.wait(5)):
                with patch.object(telemetry, "_SHUTDOWN_SECONDS", 0.05):
                    started = time.monotonic()
                    await telemetry.stop(self.handle)
                    self.assertLess(time.monotonic() - started, 0.5)
                    self.assertFalse(telemetry.status()["enabled"])
        finally:
            released.set()
            self.engine.shutdown()

    async def test_real_loopback_otlp_protobuf_exports_safe_spans_and_metrics(self) -> None:
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

        received = []

        class Collector(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, dict(self.headers), body))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args) -> None:
                pass

        collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
        thread = threading.Thread(target=collector.serve_forever, daemon=True)
        thread.start()
        await telemetry.stop(self.handle)
        self.builder.stop()
        try:
            with patch.dict(os.environ, {
                "LM_PROXY_OTEL_ENDPOINT": f"http://127.0.0.1:{collector.server_port}/collector",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://example.invalid",
                "OTEL_EXPORTER_OTLP_HEADERS": "authorization=SECRET_TOKEN",
                "OTEL_RESOURCE_ATTRIBUTES": "project=SECRET_PROJECT",
                "HTTP_PROXY": "http://example.invalid",
            }):
                self.handle = telemetry.start("mcp")
                self.assertEqual(self.handle.state, "exporting")
                with telemetry.operation("tool", "search_codebase", surface="mcp"):
                    with telemetry.operation("graph", "graph.execute_read", backend="ladybug"):
                        pass
                engine = telemetry._engine
                self.assertTrue(await asyncio.to_thread(engine.tracer_provider.force_flush, timeout_millis=1500))
                self.assertTrue(await asyncio.to_thread(engine.meter_provider.force_flush, timeout_millis=1500))
                self.assertEqual(telemetry.status()["delivery"], {
                    "trace_export": "ok", "metric_export": "ok",
                    "trace_export_failures": 0, "metric_export_failures": 0})
                await telemetry.stop(self.handle)
            paths = {item[0] for item in received}
            self.assertEqual(paths, {"/collector/v1/traces", "/collector/v1/metrics"})
            for path, headers, body in received:
                self.assertFalse(any(name.lower() == "authorization" for name in headers))
                self.assertEqual(headers["Content-Type"], "application/x-protobuf")
                message = ExportTraceServiceRequest() if path.endswith("traces") else ExportMetricsServiceRequest()
                message.ParseFromString(body)
                rendered = str(message)
                self.assertNotIn("SECRET", rendered)
                resources = message.resource_spans if path.endswith("traces") else message.resource_metrics
                for resource in resources:
                    self.assertEqual([(attribute.key, attribute.value.string_value)
                                      for attribute in resource.resource.attributes], [("service.name", "rest-proxy")])
            trace_message = ExportTraceServiceRequest()
            trace_message.ParseFromString(next(body for path, _, body in received if path.endswith("traces")))
            spans = [span for resource in trace_message.resource_spans for scope in resource.scope_spans
                     for span in scope.spans]
            self.assertEqual(len(spans), 2)
            self.assertTrue(all(not span.events and not span.links for span in spans))
        finally:
            await telemetry.stop(self.handle)
            await asyncio.to_thread(collector.shutdown)
            collector.server_close()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()

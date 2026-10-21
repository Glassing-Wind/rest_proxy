"""Optional private OpenTelemetry providers with a strictly bounded label vocabulary.

No SDK import, exporter, global provider or network client is created at import time.
Only ``LM_PROXY_TELEMETRY_ENABLED=1`` starts instrumentation. A blank
``LM_PROXY_OTEL_ENDPOINT`` keeps it local; otherwise it is an explicit OTLP/HTTP
base URL. Bodies, arguments, query text, caller identifiers and exception details
are deliberately absent from this API.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import inspect
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable
from urllib.parse import urlsplit


_SERVICES = frozenset({"mcp", "proxy", "stdio"})
_CATEGORIES = frozenset({
    "request", "tool", "retrieval", "graph", "vector", "parser", "publication",
    "analyzer", "persistence", "learning", "embedding",
})
_BACKENDS = frozenset({
    "none", "neo4j", "postgres", "redis", "ladybug", "lancedb", "repoanalyzer",
    "githubanalyzer", "sqlite", "http", "ts_pack", "provider",
})
_SURFACES = frozenset({"internal", "mcp", "rest", "proxy", "stdio"})
_OUTCOMES = frozenset({"ok", "error", "cancelled", "unavailable", "conflict", "partial", "empty"})
_OPERATIONS = frozenset({
    "mcp.http", "evidence.read", "learning.write", "health", "live", "ready",
    "health.details", "fingerprint", "proxy.models", "proxy.local_models",
    "proxy.debug_state", "proxy.chat", "proxy.context_chat", "proxy.embeddings",
    "http.request", "http.unmatched", "graph.execute_read", "graph.execute_write",
    "vector.stage", "vector.search_text", "vector.search_vector", "vector.search_hybrid",
    "vector.count", "vector.cleanup", "repository.index", "repository.publish",
    "repository.parse", "repository.facts", "repository.chunks", "repository.embed",
    "repository.search", "repository.learning_snapshot", "analyzer.profile", "analyzer.compare",
    "analyzer.plaintext_ast", "analyzer.configuration", "learning.repository", "learning.text",
    "learning.configuration", "learning.read", "learning.suggest", "learning.delete",
    "learning.save", "learning.memory_read", "learning.memory_delete",
})
# Source-owned tool catalog names, never names supplied by a request. A new tool
# gets the bounded "other" label until this vocabulary is explicitly extended.
_TOOLS = frozenset({
    "get_repository_learning_profile", "compare_repository_learning_profiles",
    "get_learned_repository_patterns", "suggest_learned_repository_patterns",
    "learn_repository_patterns", "learn_text_patterns", "learn_configuration_patterns",
    "delete_repository_learning", "assemble_fire_context_bundle", "assemble_context_bundle",
    "save_fire_checkpoint", "resume_fire_checkpoint", "get_fire_original", "delete_fire_task",
    "purge_expired_fire_versions", "resolve_embedded_session", "configure_embedded_project_watch",
    "get_embedded_workspace_activity", "set_embedded_watch_intent", "refresh_embedded_session",
    "get_embedded_indexing_attempt", "get_embedded_project_metadata", "update_embedded_project_metadata",
    "get_embedded_relationships", "get_embedded_file_facts", "list_embedded_projects",
    "index_embedded_repository", "search_embedded_repository", "describe_embedded_file",
    "get_embedded_overview", "add_memory", "analyze_duplicate_results", "author_and_index_documentation",
    "cancel_index_job", "cleanup_stale_shadow_graph", "delete_documentation", "describe_file",
    "download_documentation", "extract_class_interface", "extract_function_body", "find_code_duplication",
    "find_definitions", "find_references", "find_symbol_usages", "get_app_flow_summary",
    "get_apple_build_summary", "get_backend_flow_summary", "get_call_chain", "get_changed_symbols",
    "get_code_communities", "get_code_importance", "get_directory_snapshot", "get_flow_summary",
    "get_index_status", "get_indexed_projects", "get_indexing_health", "get_mcp_tool_catalog",
    "get_project_overview", "get_related_files", "get_repo_dependency_summary", "get_symbol_context",
    "get_symbol_exports_summary", "get_symbol_imports_overview", "get_test_coverage_for", "git_summary",
    "grep_codebase", "index_authored_documentation", "index_local_documentation_file", "index_workspace",
    "lint_project_subset", "list_documentation_sources", "list_memories", "list_symbol_matches", "query_graph",
    "rerank_retrieval_results", "research_and_index", "research_documentation", "resolve_graph_project",
    "restart_brain_server", "search_codebase", "search_documentation", "search_memory", "suggest_indexignore",
    "swift_doc_lookup", "trace_code_ranking", "trace_graph_provenance", "trace_symbol_cross_project",
    "unwatch_project", "visualize_subgraph", "watch_project", "dispatch_deep_analysis",
})
_ATTRIBUTE_KEYS = frozenset({
    "operation.category", "operation.name", "operation.backend", "operation.surface", "operation.outcome",
})
_SHUTDOWN_SECONDS = 2.0
_lock = threading.RLock()
_engine: _Engine | None = None
_state = "disabled"
_owned_context: contextvars.ContextVar[Any] = contextvars.ContextVar("rest_proxy_telemetry_context", default=None)


@dataclass(frozen=True)
class TelemetryHandle:
    """A lifespan lease; only its final owning lease shuts down the providers."""

    token: str
    enabled: bool
    state: str


@dataclass
class _Engine:
    tracer_provider: Any
    meter_provider: Any
    tracer: Any
    duration: Any
    counter: Any
    metric_reader: Any
    empty_context: Callable[[], Any]
    span_context: Callable[..., Any]
    error_status: Any
    mode: str
    delivery: _DeliveryState | None = None
    leases: set[str] = field(default_factory=set)

    def shutdown(self) -> None:
        # Meter shutdown can make one final HTTP request, and trace shutdown can
        # drain its bounded queue. The facade runs both on a daemon thread so a
        # broken third-party shutdown cannot stall application shutdown forever.
        try:
            self.meter_provider.shutdown(timeout_millis=1000)
        except Exception:
            pass
        try:
            self.tracer_provider.shutdown()
        except Exception:
            pass


@dataclass
class _DeliveryState:
    """Fixed acknowledgements only; never endpoint, response or exception details."""

    values: dict[str, Any] = field(default_factory=lambda: {
        "trace_export": "unattempted", "metric_export": "unattempted",
        "trace_export_failures": 0, "metric_export_failures": 0,
    })
    lock: Any = field(default_factory=threading.Lock)

    def record(self, signal: str, success: bool) -> None:
        with self.lock:
            self.values[signal + "_export"] = "ok" if success else "error"
            if not success:
                key = signal + "_export_failures"
                self.values[key] = min(1_000_000, self.values[key] + 1)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.values)


@dataclass(frozen=True)
class _CapturedContext:
    engine: _Engine
    context: Any


def capture_context() -> _CapturedContext | None:
    """Capture only a facade-owned span context for an internal request bridge."""
    captured = _owned_context.get()
    return captured if isinstance(captured, _CapturedContext) else None


@contextlib.contextmanager
def use_context(captured: _CapturedContext | None):
    """Replace inherited session context with an opaque per-request context.

    ``None`` clears an inherited owner context. This accepts no wire headers or
    external trace IDs, and a context from an expired provider is ignored.
    """
    with _lock:
        selected = captured if isinstance(captured, _CapturedContext) and captured.engine is _engine else None
    token = _owned_context.set(selected)
    try:
        yield
    finally:
        try:
            _owned_context.reset(token)
        except Exception:
            pass


def _label(value: Any, allowed: frozenset[str]) -> str:
    return value if type(value) is str and value in allowed else "other"


def _endpoint(value: str) -> str | None:
    if not value.strip():
        return None
    value = value.strip()
    if len(value) > 2048 or any(ord(char) < 33 for char in value):
        raise ValueError("invalid-endpoint")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError("invalid-endpoint")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("invalid-endpoint")
    if parsed.path.rstrip("/").endswith(("/v1/traces", "/v1/metrics")):
        raise ValueError("invalid-endpoint")
    return value.rstrip("/")


def _http_exporters(endpoint: str, *, delivery: _DeliveryState | None = None) -> tuple[Any, Any]:
    """Use official OTLP codecs and SDK exporter contracts without env defaults.

    The stock HTTP exporters consume ambient credential/header settings and log
    remote reason strings. These small exporters intentionally return only fixed
    SDK results, with no retry, logging, proxy, redirect or ambient credential path.
    """
    import httpx
    from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    delivery = delivery if delivery is not None else _DeliveryState()

    class HttpSpanExporter(SpanExporter):
        def __init__(self) -> None:
            self.client = httpx.Client(timeout=1.0, trust_env=False, follow_redirects=False)

        def export(self, spans: Any) -> Any:
            try:
                body = encode_spans(spans).SerializeToString()
                with self.client.stream("POST", endpoint + "/v1/traces", content=body,
                                        headers={"Content-Type": "application/x-protobuf"}) as response:
                    success = 200 <= response.status_code < 300
                    delivery.record("trace", success)
                    return SpanExportResult.SUCCESS if success else SpanExportResult.FAILURE
            except Exception:
                delivery.record("trace", False)
                return SpanExportResult.FAILURE

        def shutdown(self) -> None:
            self.client.close()

    class HttpMetricExporter(MetricExporter):
        def __init__(self) -> None:
            super().__init__()
            self.client = httpx.Client(timeout=1.0, trust_env=False, follow_redirects=False)

        def export(self, metrics_data: Any, timeout_millis: float = 10000, **kwargs: Any) -> Any:
            try:
                body = encode_metrics(metrics_data).SerializeToString()
                with self.client.stream("POST", endpoint + "/v1/metrics", content=body,
                                        headers={"Content-Type": "application/x-protobuf"}) as response:
                    success = 200 <= response.status_code < 300
                    delivery.record("metric", success)
                    return MetricExportResult.SUCCESS if success else MetricExportResult.FAILURE
            except Exception:
                delivery.record("metric", False)
                return MetricExportResult.FAILURE

        def force_flush(self, timeout_millis: float = 10000) -> bool:
            return True

        def shutdown(self, timeout_millis: float = 30000, **kwargs: Any) -> None:
            self.client.close()

    span_exporter = HttpSpanExporter()
    try:
        return span_exporter, HttpMetricExporter()
    except Exception:
        try:
            span_exporter.shutdown()
        except Exception:
            pass
        raise


def _build_engine(endpoint: str | None, *, span_exporter: Any = None, metric_reader: Any = None) -> _Engine:
    """Lazy builder; optional exporter/reader injection is an in-process test seam."""
    from opentelemetry.context import Context
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics._internal.exemplar import AlwaysOffExemplarFilter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader, PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import SpanLimits, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON
    from opentelemetry.trace import Status, StatusCode, set_span_in_context

    resource = Resource({"service.name": "rest-proxy"})
    reader = metric_reader if metric_reader is not None else InMemoryMetricReader()
    readers = [reader]
    http_span_exporter = None
    http_metric_exporter = None
    meter_provider = None
    tracer_provider = None
    delivery = _DeliveryState() if endpoint is not None else None
    try:
        if endpoint is not None:
            http_span_exporter, http_metric_exporter = _http_exporters(endpoint, delivery=delivery)
            readers.append(PeriodicExportingMetricReader(http_metric_exporter, export_interval_millis=5000,
                                                        export_timeout_millis=1000))
        meter_provider = MeterProvider(
            metric_readers=readers, resource=resource, shutdown_on_exit=False,
            exemplar_filter=AlwaysOffExemplarFilter(), views=[
                View(instrument_name="rest_proxy.operation.duration", attribute_keys=_ATTRIBUTE_KEYS,
                     aggregation=ExplicitBucketHistogramAggregation(
                         boundaries=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60))),
                View(instrument_name="rest_proxy.operation.count", attribute_keys=_ATTRIBUTE_KEYS),
            ],
        )
        tracer_provider = TracerProvider(resource=resource, sampler=ALWAYS_ON, shutdown_on_exit=False,
                                        span_limits=SpanLimits(
                                            max_attributes=5, max_span_attributes=5, max_events=0, max_links=0,
                                            max_event_attributes=0, max_link_attributes=0,
                                            max_attribute_length=128, max_span_attribute_length=128))
        if http_span_exporter is not None:
            tracer_provider.add_span_processor(BatchSpanProcessor(
                http_span_exporter, max_queue_size=128, max_export_batch_size=32,
                schedule_delay_millis=1000, export_timeout_millis=1000,
            ))
        if span_exporter is not None:
            tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        meter = meter_provider.get_meter("rest_proxy.telemetry", "1")
        return _Engine(
            tracer_provider, meter_provider, tracer_provider.get_tracer("rest_proxy.telemetry", "1"),
            meter.create_histogram("rest_proxy.operation.duration", unit="s", description="Operation duration"),
            meter.create_counter("rest_proxy.operation.count", unit="{operation}", description="Completed operations"),
            reader, Context, set_span_in_context, Status(StatusCode.ERROR), "exporting" if endpoint else "local", delivery,
        )
    except Exception:
        if meter_provider is not None:
            try:
                meter_provider.shutdown(timeout_millis=0)
            except Exception:
                pass
        if tracer_provider is not None:
            try:
                tracer_provider.shutdown()
            except Exception:
                pass
        for telemetry_reader in readers:
            try:
                telemetry_reader.shutdown(timeout_millis=0)
            except Exception:
                pass
        for exporter in [http_span_exporter, http_metric_exporter]:
            if exporter is not None:
                try:
                    exporter.shutdown()
                except Exception:
                    pass
        raise


def start(service: str) -> TelemetryHandle:
    """Start an opt-in private provider lease without modifying global OTel state."""
    global _engine, _state
    token = uuid.uuid4().hex
    if type(service) is not str or service not in _SERVICES:
        return TelemetryHandle(token, False, "invalid-service")
    if os.environ.get("LM_PROXY_TELEMETRY_ENABLED", "") != "1":
        with _lock:
            if _engine is None:
                _state = "disabled"
        return TelemetryHandle(token, False, "disabled")
    try:
        endpoint = _endpoint(os.environ.get("LM_PROXY_OTEL_ENDPOINT", ""))
    except Exception:
        with _lock:
            if _engine is None:
                _state = "invalid-endpoint"
        return TelemetryHandle(token, False, "invalid-endpoint")
    try:
        with _lock:
            if _engine is None:
                _engine = _build_engine(endpoint)
            _state = _engine.mode
            _engine.leases.add(token)
            return TelemetryHandle(token, True, _engine.mode)
    except Exception:
        with _lock:
            if _engine is None:
                _state = "unavailable"
        return TelemetryHandle(token, False, "unavailable")


def status() -> dict[str, Any]:
    """Describe private-provider state without exposing endpoint or environment values."""
    with _lock:
        return {"enabled": _engine is not None, "state": _engine.mode if _engine is not None else _state,
                "exporter": "otlp-http" if _engine is not None and _engine.mode == "exporting" else "none",
                "delivery": _engine.delivery.snapshot() if _engine is not None and _engine.delivery is not None else None}


async def stop(handle: TelemetryHandle) -> None:
    """Release only this lease and bound the final provider shutdown to two seconds."""
    global _engine, _state
    with _lock:
        if _engine is None or handle.token not in _engine.leases:
            return
        engine = _engine
        engine.leases.remove(handle.token)
        if engine.leases:
            return
        _engine = None
        _state = "disabled"
    loop = asyncio.get_running_loop()
    completed = loop.create_future()

    def shutdown() -> None:
        try:
            engine.shutdown()
        except Exception:
            pass
        finally:
            try:
                loop.call_soon_threadsafe(finish)
            except RuntimeError:
                pass

    def finish() -> None:
        if not completed.done():
            completed.set_result(None)

    try:
        threading.Thread(target=shutdown, name="rest-proxy-telemetry-stop", daemon=True).start()
    except Exception:
        return
    try:
        await asyncio.wait_for(asyncio.shield(completed), timeout=_SHUTDOWN_SECONDS)
    except asyncio.TimeoutError:
        pass


class Operation:
    """A content-free timing scope; fixed outcomes never swallow caller failures."""

    def __init__(self, category: str, name: str, *, backend: str, surface: str) -> None:
        self.attributes = MappingProxyType({
            "operation.category": _label(category, _CATEGORIES),
            "operation.name": _label(name, _TOOLS if category == "tool" else _OPERATIONS),
            "operation.backend": _label(backend, _BACKENDS),
            "operation.surface": _label(surface, _SURFACES),
        })
        self._outcome = "ok"
        self._engine: _Engine | None = None
        self._span: Any = None
        self._context_token: Any = None
        self._started = 0.0

    def set_outcome(self, outcome: str) -> None:
        """Choose only a fixed label; caller statuses must be mapped before use."""
        if type(outcome) is str and outcome in _OUTCOMES:
            self._outcome = outcome

    @property
    def outcome(self) -> str:
        return self._outcome

    def __enter__(self) -> Operation:
        with _lock:
            self._engine = _engine
        if self._engine is None:
            return self
        self._started = time.perf_counter()
        try:
            captured = _owned_context.get()
            parent = (captured.context if isinstance(captured, _CapturedContext)
                      and captured.engine is self._engine else self._engine.empty_context())
            self._span = self._engine.tracer.start_span(
                self.attributes["operation.name"], context=parent, attributes=self.attributes,
                record_exception=False, set_status_on_exception=False,
            )
            # Keep the SDK span private. Attaching it as the global current span
            # would let unrelated instrumentation add query/body attributes.
            self._context_token = _owned_context.set(_CapturedContext(
                self._engine, self._engine.span_context(self._span, parent)))
        except Exception:
            if self._span is not None:
                try:
                    self._span.end()
                except Exception:
                    pass
            self._span = None
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc_type is not None:
            self._outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        if self._engine is None:
            return False
        attributes = {**self.attributes, "operation.outcome": self.outcome}
        try:
            if self._span is not None:
                self._span.set_attribute("operation.outcome", self.outcome)
                if self.outcome == "error":
                    self._span.set_status(self._engine.error_status)
            self._engine.duration.record(max(0.0, time.perf_counter() - self._started), attributes)
            self._engine.counter.add(1, attributes)
        except Exception:
            pass
        finally:
            if self._context_token is not None:
                try:
                    _owned_context.reset(self._context_token)
                except Exception:
                    pass
            if self._span is not None:
                try:
                    self._span.end()
                except Exception:
                    pass
        return False


def operation(category: str, name: str, *, backend: str = "none", surface: str = "internal") -> Operation:
    """Create a scope with fixed labels only; safely inactive outside owned lifespans."""
    return Operation(category, name, backend=backend, surface=surface)


def instrument(category: str, name: str, *, backend: str = "none", surface: str = "internal") -> Callable:
    """Decorate a sync/async operation without recording arguments or return values."""
    def decorate(function: Callable) -> Callable:
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with operation(category, name, backend=backend, surface=surface):
                    return await function(*args, **kwargs)
            return async_wrapper

        @functools.wraps(function)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with operation(category, name, backend=backend, surface=surface):
                return function(*args, **kwargs)
        return sync_wrapper
    return decorate

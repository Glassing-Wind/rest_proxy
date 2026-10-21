# Optional request and operation telemetry

Rest-proxy provides an opt-in OpenTelemetry layer over MCP/REST requests, tool
execution and selected repository operations. It measures syntax/fact production,
publication, retrieval and pattern learning separately from vector/model work.
Tracing is disabled by default and does not select an inference provider.

## Setup and ownership

Use the existing compatible runtime. The full/CI profiles already include the
required SDK packages. Minimal installations can select the additive `telemetry`
extra (`rest-proxy[embedded,telemetry]`) or `requirements-telemetry.txt`, which reuses
the existing 1.40.0 API/SDK/protobuf-codec pins without model/database dependencies.
Missing/incompatible SDK support reports `unavailable`; requests keep working.
This implementation does not install or upgrade the running environment.

Configure the intended owner process before starting it:

```sh
export LM_PROXY_TELEMETRY_ENABLED=1
# Optional explicit OTLP/HTTP collector base URL; no implicit exporter destination:
export LM_PROXY_OTEL_ENDPOINT=http://127.0.0.1:4318
```

`LM_PROXY_TELEMETRY_ENABLED` enables instrumentation only when its value is exactly
`1`. An unset/blank endpoint keeps SDK metric aggregation local and retains no
finished-span buffer. There is no public `/metrics` endpoint or console exporter;
configure a collector to inspect exported data. The base URL receives `/v1/traces`
and `/v1/metrics`; do not include either signal suffix. Only HTTP(S) URLs without
credentials, query or fragment are accepted. No collector is started automatically.

The HTTP MCP brain, inference proxy and STDIO entry point own provider leases in
their lifespans. The last lease closes the providers after application resource
cleanup. Providers and trace context are private; they do not replace global OTel
providers or attach spans to the global current-span API. Async tasks and native
worker threads preserve the private operation context. Stateful HTTP MCP dispatch
uses the exact request's internal context, rather than the initialization task's
inherited context; concurrent calls have separate traces. REST and MCP reuse
`Tool.run`, preserving tool functions, schemas, annotations and fingerprints.

No incoming trace headers or baggage are imported in this slice. Correlation covers
the instrumented work in one owner process; a repository analyzer's disposable
subprocess is timed as one analyzer operation. It does not emit child parser spans.
Use separate ports/profiles for the MCP brain and inference application as described
in [current architecture](current-architecture.md).

## Measurements

| Signal | Meaning |
| --- | --- |
| `rest_proxy.operation.count` | Completed logical operations, grouped by fixed attributes and outcome |
| `rest_proxy.operation.duration` | Duration histogram in seconds, including awaits and thread work inside each scope |
| Request spans | Fixed endpoint operation; body/stream completion and cancellation are included |
| Tool spans | One execution boundary per invoked tool, including nested dispatcher calls as their own operations |
| Parser spans | Native parse, static fact production and syntax-aware chunking in the embedded indexer |
| Publication/embedding spans | Owned indexing, explicit embedding batches, vector staging and graph visibility switch |
| Retrieval/storage spans | Owned retrieval, managed Neo4j/Ladybug transactions and run-published LanceDB operations |
| Analyzer/persistence spans | Repository profiles/comparison, plaintext/configuration extraction and revisioned SQLite memory |

Only five attribute keys are emitted: `operation.category`, `operation.name`,
`operation.backend`, `operation.surface` and `operation.outcome`. Values come from
source-owned finite vocabularies; unknown tool/operation labels become `other`.
The resource's service name is fixed to `rest-proxy`. Outcomes are `ok`, `error`,
`cancelled`, `unavailable`, `conflict`, `partial` and `empty`.

Tool/request outcomes describe execution/transport. Learning scopes separately map
their safely returned domain statuses, so a successful HTTP 200 can contain an
`unavailable` analyzer child. Managed transaction spans include retries and result
consumption as one logical operation; their counts are not counts of Cypher
statements or retry attempts. Nested durations overlap and should not be summed.
MCP GET streams may remain open; request duration then measures that stream's life.

Prompts, arguments, results, queries, source text, paths, project/session IDs, hashes,
headers, provider/model identities and exception descriptions/tracebacks are excluded
from this telemetry. Spans have no events/links, and metric exemplars are disabled.
This contract concerns the new traces/metrics and telemetry health fields; existing
application debug/retrieval logs have their own controls.

## Export and diagnostics

The exporter uses the pinned SDK's OTLP protobuf codecs and explicit HTTPX clients.
It does not consume ambient OTEL resource/endpoint/header settings, proxy credentials
or redirect destinations. Requests have a one-second network timeout, no retry and
unread response bodies. Traces use a queue of 128, batches of 32 and a one-second
schedule; metrics export every five seconds. Export errors fail open. Final shutdown
waits at most two seconds while its own daemon thread drains remaining work; this is
best-effort export, rather than guaranteed delivery on shutdown or power loss.

`/health/details` adds `telemetry` with fixed `enabled`, `state`, `exporter` and
`delivery` fields. States include `disabled`, `local`, `exporting`, `unavailable`
and `invalid-endpoint`. In export mode, delivery reports the last trace/metric
acknowledgement (`unattempted`, `ok` or `error`) and capped failure counts. It exposes
no collector URL, response body or exception text. `exporting` describes configured
providers; an HTTP acknowledgement does not establish durable collector storage.
Telemetry is not a readiness dependency and does not probe the collector.

This first layer does not yet instrument every PostgreSQL/Redis/provider operation,
connection-pool acquisition, legacy indexing parser, direct Neo4j auto-commit path
or backup workflow. It does not establish SLO targets, observed latency improvement,
recommendation quality or coding-outcome gains. Use actual workload measurements
before tuning pools, changing stores or asserting performance benefits.

See [acceptance evidence](../benchmarks/reports/2026-10-12/telemetry/README.md).
The implementation follows the [official Python instrumentation guide](https://opentelemetry.io/docs/languages/python/instrumentation/)
and [SDK provider/exporter contracts](https://opentelemetry-python.readthedocs.io/en/stable/sdk/trace.html),
verified against the installed 1.40.0 SDK source.

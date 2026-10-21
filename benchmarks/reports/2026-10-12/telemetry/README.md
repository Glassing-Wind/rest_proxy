# Optional private telemetry acceptance — October 12, 2026

Rest-proxy now has optional OpenTelemetry request/tool spans and operation duration/
count metrics. Embedded native parse/facts/chunking, embedding/publication, selected
graph/vector/retrieval paths and repository analyzer/pattern-memory boundaries are
separate scopes. [Setup, attributes and limits](../../../../docs/telemetry.md)
describe the opt-in runtime and additive telemetry dependency profile.

The final [isolated full gate](isolated-ci.log) passed **1021 unittest
executions across 111 suite executions, including
9 skips**, plus Ruff, graph pipeline and MCP STDIO/protocol/restart/roots checks.
These are executions rather than distinct tests. The temporary daemon enabled
local telemetry with no exporter destination; actual RepoAnalyzer/GithubAnalyzer
acceptance was enabled separately. [Gate identity](gate-summary.json) and
[final source/installed-version validation](validation.json) identify the exact
staged source. Final production bytes match the gate; later files only record receipts.

The 41 new tests comprise 20 core, 14 transport and 7 native/boundary checks. They
exercise real SDK providers/metrics/protobuf encoders, an actual disposable loopback
HTTP collector, real stateful MCP JSON HTTP initialization and concurrent POSTs,
MCP memory/REST contracts and native ts-pack/Ladybug/LanceDB/SQLite fixtures.
Typed analyzer responses and synthetic vectors establish plumbing and retention,
not model, recommendation or coding-outcome quality. The broader gate also runs
actual optional analyzer components on disposable text/repository fixtures.

Independent review and regressions cover fixed attribute/cardinality vocabularies,
resource/header/endpoint environment exclusion, unknown-label collapsing, absent
SDKs, startup/export/shutdown failures, bounded queue/shutdown, private nested/thread
context, streaming cancellation, managed transaction retries/consumption, retained
publication and SQLite cancellation draining. A collector fixture with a claimed
1-GiB blocked error body returns promptly because response bodies are not consumed.
Delivery diagnostics contain only fixed last-ack states and capped failure counts.

Two context/privacy issues were repaired before final acceptance: stateful SDK
session tasks inherited the initialization request's trace; each tool now uses its
exact HTTP request's retained context. Global current-span attachment also allowed
foreign instrumentation to inject source/query attributes; private spans now stay
outside that API. Concurrent requests retain separate correct traces and backend
children; mutating an unrelated ambient span cannot alter these payloads. Tool
functions, schemas, metadata and fingerprint inputs remain intact.

The first [full-gate attempt](health-contract-attempt-isolated-ci.log) stopped at an
existing assertion that `/ready` and `/health/details` had identical JSON. Readiness
behavior/payload remains unchanged; details now has additive safe telemetry fields.
Updating that assertion to check both contracts repaired the test without changing
readiness. Its [attempt summary](health-contract-attempt-gate-summary.json) is retained.
The first [focused packaging attempt](packaging-attempt.log) also caught a test
expecting a build-time requirements file in the wheel. The corrected test requires
the profile in the sdist and its pinned dependency metadata in the wheel.
[Focused packaging](packaging.log) and the final full gate pass. Installed telemetry
imports/scopes without SDK packages do not initialize clients or providers.

Validation uses the existing macOS ARM64/Python 3.14 environment. No operational
configuration, existing daemon, indexed repository data, model or dependency
environment was changed. The gate managed only its private temporary loopback
daemon/configuration and its listener was verified closed. Collector tests use
disposable loopback endpoints, not a configured operational collector. Nothing was
pushed or dispatched to hosted CI.

This is a first instrumentation layer. Pool acquisition, complete server/provider/
legacy parser coverage, cross-process/incoming trace propagation, operational SLOs,
collector durability/backup and measured performance/coding-outcome benefits remain
separate work. HTTP acknowledgements are not proof of durable delivery. Existing
application logs retain their own controls; the privacy contract applies to this
new telemetry's traces, metrics and health fields.

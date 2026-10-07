# Embedded backend route overview — October 5, 2026

`get_backend_flow_summary` now routes embedded workspaces through the shared owner
and returns JSON containing published native route declarations. This is a partial
route overview, not a reconstructed API → service → database flow. Legacy storage
keeps its existing flow behavior.

Each observation includes its file, source hash, parser-fact hash and publication
run. Native declarations do not currently supply verified source line spans, so
line fields are null. The parser recognizes the tested Next.js-style
`app/api/.../route.ts` fixture; a FastAPI decorator probe emitted no route facts.
Missing declarations therefore do not establish that an application has no routes.
Runtime registration, mounted prefixes, dynamic routing, service/database hops and
complete framework coverage remain unsupported; `coverage_complete` is false.

`api_contains` filters the published file path. Test files are excluded by default
using directory/name conventions (`test`, `tests`, `__tests__`, `test_`, `.test.`,
`.spec.`, `_test.py`); `include_tests=True` includes them. Crate, model and service
filters and table rendering return `unsupported-options` rather than being ignored.
This bridge covers the explicit backend tool; general auto/UI flow dispatch and
resolved frontend-to-backend chains still require work.

Reads hold the owner lock, verify source/fact hashes against one publication and
require no embedding model. Scanning stops at 100 files or an 8 MiB source/fact
retrieval ceiling. The requested limit is 1–100 observations, with a 44,000-byte
insertion threshold and 48,000-byte final JSON ceiling. Counts cover scanned files
only. Truncation reasons and the first unscanned file are explicit; remaining facts
can be inspected through `get_embedded_file_facts`. There is no new summary cursor.
Missing or old fact publications retain explicit statuses; hash corruption fails.

Validation includes 22 native repository tests, 14 runtime tests, two offline bounds
tests, full local CI and real STDIO/Streamable HTTP parity on a disposable route
fixture. Native checks exercise test/path filters, row limits, live-edit snapshot
stability, reopen, reindex and deletion. Offline checks cover byte/file budgets,
missing spans, old contracts and hash corruption. Synthetic vectors validate storage
and transport behavior, not semantic quality, performance or token savings.
See the [acceptance receipt](../benchmarks/reports/2026-10-05/embedded-route-overview.json).

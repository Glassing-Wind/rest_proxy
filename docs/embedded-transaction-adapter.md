# Embedded transaction adapter — October 4, 2026

`memory/embedded_ladybug.py` introduces a separate experimental Ladybug driver.
It now supports experimental application graph selection and file-outline reads;
full indexing and memory integration remain unfinished. Engine import remains lazy; no dependency was added to
the supported application install.

A driver owns one Database object and serializes operations with an async lock.
Each context-managed session owns a separate connection. Read callbacks run in
explicit read-only transactions; write callbacks commit on success and roll back
on exception or cancellation before commit. Results retain query column names
and null parameters. Native queries run in worker threads; cancellation waits for
native work to finish before rollback or connection closure. No automatic retry
is performed because write callbacks may have external side effects.

Cancellation during COMMIT may occur after a successful commit: cancellation is
not proof that a write did not happen. Publication identities and reconciliation
must address that boundary. Native work can delay cancellation; query timeouts,
crash recovery and multiple processes are not accepted by these tests. Driver
construction and connection construction remain synchronous initialization work.
Close sessions before closing the driver.

## Executed acceptance

Python 3.14.4 / Ladybug 0.21.2 on this Mac, disposable databases, no external
service connections:

```sh
.venv/bin/python test_embedded_ladybug.py
.venv/bin/python -m ruff check memory/embedded_ladybug.py test_embedded_ladybug.py
```

The initial two transaction test methods passed. They verify separate connections, committed data,
rollback after an application exception, cancellation before commit, rejected
writes in a read-only transaction, persistence after reopen, and cancellation
waiting for native worker completion. Ruff passed. This is targeted adapter
acceptance; full application CI and integrated indexing were not run for that initial
adapter checkpoint. Updated acceptance appears below.

## Next integration gates

- Replay actual tool queries against an explicit versioned schema; avoid generic
  string substitution to translate Cypher.
- Establish one mutable owner across HTTP, STDIO and indexing workers.
- Route structural facts, vectors and durable metadata through that owner.
- Coordinate graph/vector publication and deletion by run identity.
- Index and investigate a real repository with external services disabled.

See [native verification](native-embedded-verification.md) and
[indexing acceptance](../benchmarks/reports/2026-10-04/reliable-indexing.md).

## Application selection and file outlines

The graph bootstrap now selects Ladybug for `LM_PROXY_STORAGE_BACKEND=embedded`
or `LM_PROXY_GRAPH_BACKEND=ladybug`. Set `LM_PROXY_LADYBUG_PATH` to a **new**
database path; the default is `.runtime/ladybug_graph.db`. The old
`LM_PROXY_GRAPH_BACKEND=kuzu` selection is rejected with a migration message.
Existing Kuzu files are not migrated or opened by this change. The historical
adapter and feasibility probe remain for reproducibility.

Bootstrap initializes an explicit version-1 file-outline schema (File, 13 symbol
tables, CONTAINS, and a schema marker). It checks column types and primary keys,
and rejects incompatible versions. `describe_file` dispatches indexed outlines
to an explicit Ladybug query rather than rewriting Neo4j Cypher. Results are
ordered deterministically; absent files return no symbols. Embedded outlines
skip the existing Postgres semantic-preview lookup. Current local-source/live-AST
freshness behavior is retained.

**This is experimental graph read integration, not complete embedded indexing.**
The MCP indexing entry point and direct structural/semantic workers refuse
embedded mode before accessing the existing server-backed indexing pipeline.
Other graph tools and proxy memory paths still need backend integration. This
selection does not establish a complete service-free application install.

Updated acceptance: four native test methods, ten bootstrap regressions, three
file-description checks, 23 semantic-index tests and 23 indexing-health tests
passed on Python 3.14. Native tests include all 13 symbol labels, null signatures,
file scoping, schema idempotence, incompatible-schema rejection and bootstrap
with external graph construction forbidden. Full local CI passed. Receipt:
[Ladybug integration](../benchmarks/reports/2026-10-04/ladybug-integration.json).

The subsequent [owned outline publisher](owned-embedded-outlines.md) adds an OS
owner lock and atomic source/outline publication. Its standalone CLI remains
separate from the guarded MCP indexing pipeline.

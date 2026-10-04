# Embedded transaction adapter — October 4, 2026

`memory/embedded_ladybug.py` introduces a separate experimental Ladybug driver.
It is not selected by application configuration and does not replace the old Kuzu
wrapper or server backend. Engine import remains lazy; no dependency was added to
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

Both test methods passed. They verify separate connections, committed data,
rollback after an application exception, cancellation before commit, rejected
writes in a read-only transaction, persistence after reopen, and cancellation
waiting for native worker completion. Ruff passed. This is targeted adapter
acceptance; full application CI and integrated indexing were not run for this
new adapter.

## Next integration gates

- Replay actual tool queries against an explicit versioned schema; avoid generic
  string substitution to translate Cypher.
- Establish one mutable owner across HTTP, STDIO and indexing workers.
- Route structural facts, vectors and durable metadata through that owner.
- Coordinate graph/vector publication and deletion by run identity.
- Index and investigate a real repository with external services disabled.

See [native verification](native-embedded-verification.md) and
[indexing acceptance](../benchmarks/reports/2026-10-04/reliable-indexing.md).

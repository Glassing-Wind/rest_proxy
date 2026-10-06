# Embedded watch intent and session leases — October 5, 2026

The shared owner now persists typed workspace activity separately from annotations
and repository evidence. This records desired watch state and client leases; it
does not activate a worker or certify that a process/client is alive.

## Contract

An additive validated Ladybug `WorkspaceActivity` table stores one record per
published project: workspace root, revision and bounded typed JSON. The record
survives owner reopen and same-root reindex. Reads use no model. Missing records
return revision zero, watch intent false and no sessions; unpublished projects
return `not_published`.

The opt-in embedded MCP tools are:

- `get_embedded_workspace_activity(project_id)` — primary read of intent, unexpired
  leases, revision, current run and canonical root.
- `set_embedded_watch_intent(project_id, requested, expected_revision,
  expected_run_id)` — admin-classified replacement of desired watch state.
- `refresh_embedded_session(project_id, session_id, expected_revision,
  expected_run_id, lease_seconds=900)` — admin-classified refresh of an explicit
  caller-supplied lease. Use zero seconds to release it.

Writes compare revision and current publication inside a single transaction.
Conflicts return current revision/run without changing state. Watch and lease
updates share one revision, so concurrent clients must read/review before retrying.
Watch intent is boolean; a session identifier is nonempty and at most 128
characters. Leases last 60..3,600 seconds, with at most 32 active leases per project.
Refreshing an existing active lease does not consume another slot. Updating intent
preserves unexpired leases and updating a lease preserves intent.

Expiry uses wall-clock milliseconds, not verified process creation or heartbeat
transport identity. Reads omit expired leases without changing revision. Successful
writes prune expired entries; their stored bytes may remain until then or scoped
deletion. There is no expiry scheduler or historical lease log. Client identifiers
are assertions, not authenticated identities, PID handles or permission boundaries.
Machine clock changes can affect expiry.

Changing a project's source root returns `workspace_changed`, false watch intent
and no old leases. An explicit update using the new run and stored revision starts
state for that root. Old root-bound bytes remain until replacement/deletion.
Project deletion removes activity atomically with annotations, attempt metadata
and publication visibility. No state is silently restored for a reused project.

`embedded_watch_worker_active` is always false for this slice. Persisted intent is
not wired to a polling worker. Reads and writes share owner/runtime locks, so they
wait for active owned indexing to release its lock. No model lifecycle, legacy
watcher mutation or external storage connection is performed.

## Validation

Two offline tests refuse invalid leases/intent/preconditions and empty session
identifiers. Ten runtime/MCP tests pass, including exact intent dispatch while the
legacy watcher is patched to reject activation. Fourteen native repository tests
pass, including intent persistence, renewal/release, expiry, limits, stale writes,
same-root reindex, changed roots and scoped deletion.

Real sandboxed STDIO/HTTP activity reads match on the existing frozen 131-file
publication. Native disposable fixtures exercise mutations; no real IDE session or
watch is registered by acceptance. Full local CI passed. These are contract and
persistence checks, not runtime liveness or investigation-quality measurements.

[Acceptance receipt](../benchmarks/reports/2026-10-05/embedded-workspace-activity.json).
Private logs: `.runtime/embedded-activity-acceptance/` (mode 700).

## Remaining integration

Existing `sessions.json`, `active_sessions.json` and `pinned_watches.json` remain
used by standalone IDE registration, synchronous workspace discovery and legacy
supervisor/watcher code. They are not automatically imported or redirected. New
clients can explicitly call the embedded tools through the owning MCP service;
standalone registrars must not open an already-owned graph themselves.

Next integration requires a supported registrar-to-owner transport and an explicit
migration policy for client identity, expiry and old registry conflicts. Watcher
dispatch must use the owned embedded indexing path with explicit manifest selection,
model requirements and cancellation/recovery semantics. The legacy index worker
remains guarded. REST integration, historical jobs, automatic resume/retention and
FIRE checkpoint history remain pending.

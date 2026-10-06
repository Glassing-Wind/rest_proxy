# Automatic embedded lease refresh and watch dispatch — October 5, 2026

IDE clients can now keep leases alive with the existing registrar's foreground
refresh loop. The owning MCP process can also poll durable watch intent and dispatch
changed published files through its existing graph/vector publication pipeline.
Both capabilities are explicit opt-ins; no real IDE process or workspace watcher
is installed or enabled by this implementation.

## IDE refresh

```sh
.venv/bin/python scripts/register_session.py /absolute/workspace \
  --embedded --mcp-url http://127.0.0.1:8001/mcp --session-id ide-window-1 \
  --refresh --lease-seconds 900
```

Run this foreground process under the IDE/client's process lifecycle. It registers
immediately, then rereads current publication/revision and renews every third of the
lease duration. Optional `--refresh-interval` must be at least one second and at
most one third of the lease. A failed/conflicting renewal stops the loop; there is
no blind retry or legacy JSON fallback. SIGINT/SIGTERM requests stop; exit attempts
a best-effort release through the same owner. If transport fails or the process is
killed, expiry bounds the remaining lease. A timeout does not prove no write occurred.

Use a distinct session ID per concurrently managed IDE window/process. Leases are
client assertions, not authenticated process liveness. The CLI signal integration
is supported on Unix event loops; no Windows IDE adapter is certified here. The
15-second per-operation deadline also bounds waiting for owner locks. Slow indexing
can therefore stop renewal; lease expiry remains the safe fallback. Deployment of
this process under a particular IDE/supervisor remains an integration choice.

## Owned watcher

Configure the owning embedded MCP service:

```dotenv
LM_PROXY_EMBEDDED_WATCH_ENABLED=1
LM_PROXY_EMBEDDED_WATCH_INTERVAL=30
```

The interval must be 1..3,600 seconds. Durable `watch_requested` must also be true
for a published project, set through `set_embedded_watch_intent` with its current
activity revision and publication ID. The configured embedded model artifact and
already-loaded encoder must match the publication identity before changed content
can be reindexed. There is no embedding fallback or model load/unload. Unchanged
projects need no encoder connection.

Embedded startup bypasses legacy JSON watch restoration and legacy polling,
regardless of `LM_PROXY_WATCHER_ENABLED`. The new flag starts one polling task in
the shared runtime. It visits one page of at most 100 publications per cycle and
advances the project cursor between cycles. Owner/runtime locks serialize scanning,
publication, metadata updates and shutdown. `embedded_watch_worker_active` reports
an active task and eligible project intent; `last_watch_result` reports its latest
in-process outcome/error class (bounded to 100 projects, not a durable history).
Task activity alone does not prove successful indexing or a connected model.

## Manifest scope and failure behavior

Polling hashes **only current published manifest paths**, not a fresh repository
walk. Content hashes detect edits even when timestamps are unchanged. Added or
ignored files outside that manifest are not enrolled. Deleted files are omitted
from replacement snapshots; once omitted, recreation needs explicit reindexing
with an updated manifest. An all-files-deleted snapshot can publish an empty project.

Missing/changed roots, symlinks/non-files, IO errors, files over 8 MiB and total
scanned source over 64 MiB fail the cycle without replacing published evidence.
The scan is capped at 5,000 files per project. Provider/model failures preserve the
previous receipt and record index attempts when owned indexing began; earlier
configuration/scan failures are reflected in runtime status. The loop retries on
its next interval. Exceptions are reported by class, without raw source/error text.

Changed snapshots use the same owner index method, vector staging, atomic graph
receipt, attempt journal and citation checks as explicit indexing. Shutdown cancels
and drains the task before closing storage/model clients. Native graph/vector
cancellation preserves the prior publication and unpublished vector cleanup remains
explicit. This is full replacement within the published manifest, not incremental
indexing, arbitrary new-file discovery or restoration of legacy worker queues.

## Validation

Five offline session-client tests include repeated renewal/stop release. Two watcher
tests verify bounded content scanning, symlink refusal and startup routing that never
calls the legacy indexer. Seventeen native repository tests include actual background
publication, unchanged-source avoidance, outside-manifest exclusion, provider failure,
deletion/empty publication, task singleton behavior and shutdown during active embedding with receipt preservation. Full local CI passes.

Real HTTP owner/independent registrar subprocess acceptance verifies multiple lease
renewals, SIGTERM release, registration/discovery/refusal paths and unchanged legacy
registries. Disposable watcher validation uses synthetic vectors with an explicit
fixture encoder, not a production embedding fallback. No real watch/IDE configuration
or inference provider is changed. Controlled outcome/resource evaluation remains pending.

[Acceptance receipt](../benchmarks/reports/2026-10-05/embedded-refresh-and-watching.json).
Private logs: `.runtime/embedded-automation-acceptance/` (mode 700).

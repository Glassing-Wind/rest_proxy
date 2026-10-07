# Shared task registry foundation — October 6, 2026

Priority 3 local foundation implemented in `memory/task_registry.py`. SQLite tasks
retain project/workspace, goal, evidence references, read-source capability,
revision, assignment/claim expiry and a bounded checkpoint. Records use a private
state directory/file, 64 KiB limit and transactional revision checks. Connections
close after operations. No inference dependency or service lifecycle change.

`test_task_registry.py`: five offline tests pass under project Python 3.14.
They verify claim/checkpoint/reopen equality with evidence, rejection of a second
claim, project-key isolation/read-only capability validation, stale/expired/wrong
claims and rollback on oversized checkpoint. Existing `test_fire_store.py` passes.

This is trusted local library state, not an authenticated public task API. Project
keys are isolation parameters, not verified identities. No tools execute from this
module; recorded capabilities do not yet enforce downstream tool dispatch.

Only queued → claimed and active-claim checkpoint updates are implemented.
Expired claims remain blocked pending explicit reclaim/reconciliation design.
Cancellation, reclaim, completion/review/corrections, bounded attempts/history,
MCP/REST interfaces, authenticating callers, worker adapters and dispatch enforcement
remain open. No automatic cross-chat coordination, independent review, Qwen worker
or exactly-once side effects are established. Next add guarded cancellation/reclaim
and stale-worker checks before exposing task endpoints.

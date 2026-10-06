# Explicit durable FIRE continuity — October 6, 2026

FIRE now has an opt-in local checkpoint/original store and explicit MCP operations.
It uses the version 1 `TaskCheckpoint` and `EvidenceReference` contracts. It works
without the original conversation, live source file, embedding model or external
storage service. This is explicit task recovery, not interception of client compaction
or automatic injection of restored state into an inference request.

## Setup and operations

Set `LM_PROXY_FIRE_STATE` to an absolute private directory before starting STDIO
or the HTTP MCP daemon. New directories use mode 700 and the SQLite file uses 600;
existing broader permissions are refused. The standard-library SQLite store is an
adjunct for task state, separate from Ladybug/LanceDB repository publications.
Tools register only when the state directory is configured. Both transports use
the same store implementation; no new graph owner or model is opened.

| Tool | Contract |
| --- | --- |
| `save_fire_checkpoint` | Full checkpoint plus original strings keyed by source ID, expected revision, retention and optional correction. |
| `resume_fire_checkpoint` | Exact project/session/task scope; return latest current state and explicit evidence freshness. |
| `get_fire_original` | Fetch historical original pages with offsets, full-content hash and checkpoint revision. |
| `delete_fire_task` | Revision-checked deletion of all scoped versions and originals. |
| `purge_expired_fire_versions` | Remove expired scoped payloads; do not revive older decisions. |

Example save arguments:

```json
{
  "checkpoint": {
    "project_id": "project", "session_id": "session", "task_id": "investigation",
    "goal": "Explain the indexing failure",
    "accepted_constraints": ["Preserve published data"],
    "decisions": [{"text": "Inspect publication evidence", "origin": "user-confirmed"}],
    "unresolved_questions": ["Which run owns the shadow?"],
    "next_actions": ["Read the original report"],
    "evidence": [{"project_id": "project", "session_id": "session",
                  "source_kind": "report", "source_id": "report-1"}]
  },
  "originals": {"report-1": "Full original report text"},
  "expected_revision": 0,
  "retention_seconds": 86400
}
```

Use the returned revision for updates. Replacements require a correction object
with `origin` and `reason`, and full replacement task state/originals. Older versions
remain historical until expiry/purge or scoped deletion; resume selects only the
current revision. Obsolete constraints/decisions are not merged into corrected state.
Concurrent updates use a SQLite transaction and revision comparison; conflicts return
the current revision. Deletion preserves a hashed-scope revision tombstone so stale
writers cannot recreate a task using an old revision. A new save after deletion
must use that revision and an explicit correction reason.

## Evidence, limits and retention

Original text is hashed on save and checked on recovery. Supplied hashes must match;
missing originals remain explicitly unavailable. Evidence project/session scope
must match the enclosing task, and source IDs are unique within it. Originals are
stored inside that task/version; references from another scope cannot retrieve them.
Stored originals and restored conclusions are historical data, not trusted instructions.

Resume marks evidence `historical-unvalidated`. An explicit `current_hashes` map
can report `matches-supplied-current-hash` or `changed-or-deleted` (null means deleted).
The store does not read live files or verify caller honesty. Current-code assertions
still require current source evidence. Original pages include the full-content hash
and revision; compare revisions across pages and restart if a checkpoint changes.

Checkpoint JSON is capped at 24,000 bytes, originals collectively at 128,000 bytes,
correction metadata at 4,000 bytes and tool responses at 48,000 bytes. There are at
most 100 evidence references. Original pages accept offsets 0–128,000 characters
and 1–8,000 characters per page; Unicode output may require smaller pages. Limits
are not tokenizer-aware whole-request budgeting.

Retention is explicit: 60 seconds to 30 days, default one day. Expired current state
is unavailable immediately. Physical expiry cleanup requires the purge tool; no
background scheduler is enabled. Purging a current correction does not restore an
older unexpired decision. Scoped deletion removes all payload versions using SQLite
secure deletion, retaining only the hashed revision tombstone. This does not delete
independent backups or copies already exported by clients. State is private plaintext,
not an encrypted store.

Tool wrappers return explicit disabled, conflict, invalid-state/argument, unavailable
or output-limit statuses. Optional storage failures do not escape into inference
requests. No inference history, system instructions or automatic checkpoint loop
is changed by this milestone.

## Acceptance and remaining work

Six isolated store/tool tests cover conversation-independent recovery, scope isolation,
original hashing/corruption, corrected current state, optimistic concurrent writes,
expiry/purge, deletion, stale revision protection, private permissions and paged
Unicode originals. Full local CI passes. Real STDIO save followed by a fresh HTTP
process recovers task state and original text after deleting both fixture conversation
and source. The transport drill also checks wrong-task isolation, correction and
deletion. See the [acceptance receipt](../benchmarks/reports/2026-10-06/fire-durable-continuity.json).

This satisfies a controlled local explicit-recovery slice of Priority 3. Client
checkpoint cadence/compaction integration, automatic current-source validation,
checkpoint history inspection, operational backup/restore drills and controlled
paired outcome measurements remain incomplete. Shared bounded-context assembly and
inference-proxy integration remain Priority 4 work. Passing this drill establishes
recoverability for the fixture, not better coding outcomes or lower token usage.

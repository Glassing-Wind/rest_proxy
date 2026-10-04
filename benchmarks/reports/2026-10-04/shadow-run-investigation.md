# Legacy shadow run investigation — October 4, 2026

Read-only investigation of
`7f6b7aacd978::shadow::7f6b7aacd978:11055:1791067739470000128`.
The canonical project is `/Users/michaelmarler/Projects/rentallaw`. No operational
graph data or run state was changed, and no cleanup was performed.

## Confirmed failure chain

1. Job `0b72c1fe` started October 3 at approximately 22:48:59 UTC. Structural
   worker PID 11055 processed a 47-entry manifest, materializing 46 File nodes.
2. Native parsing and structural writes completed. The IndexRun recorded
   `struct_written`, finished at approximately 22:49:00 UTC, with no promotion
   timestamp. This intermediate status does not mean publication succeeded.
3. `ts_pack.finalize_struct_graph` raised a Neo4j authentication-rate-limit error
   before the wrapper reached promotion.
4. The exception handler attempted to record `finalize_failed`, but its Neo4j
   connection hit the same rate limit. The stored status therefore remained
   `struct_written` with no error field, despite the retained traceback.
5. The job controller recorded structural exit 1 and overall `failed`. Semantic
   exit was 0, but its log also records failed graph-role promotion from the same
   rate limit. Semantic process success does not prove cross-store alignment.
6. Job `03074bea` started about 69.5 seconds after the failed job, used structural
   PID 11751 and completed promotion successfully. Its logs show replacement,
   promotion, staging cleanup and a final `done` status. Subsequent structural
   runs also succeeded; the canonical project currently points to a later done run.

The active Neo4j instance's security log records successful logins followed by
three invalid-credential attempts at 22:48:59.823, .879 and .984 UTC, then rate-limit
events beginning at 22:49:00.028 UTC. This corroborates the failure mechanism.
Those entries do not identify the originating application/process. Incorrect
credentials from a particular tool, concurrent client or configuration path have
not been established; do not attribute the cause to Python 3.14 or ts-pack
compatibility from this evidence.

## Residue and counting correction

The namespace contains 582 code-indexing nodes, all carrying the failed run's
`last_seen_run`. The detailed node composition is recorded in the
[lifecycle report](../../../docs/indexing-lifecycle-safety.md).

The earlier **71 relationships** measurement counts relationships whose own
`project_id` equals the namespace. All 71 are `IMPORTS`. It is not a count of every
edge attached to the 582 nodes. The endpoint-based inspection found:

| Relationship type | Count |
| --- | ---: |
| CALLS | 541 |
| CALLS_EXTERNAL_SYMBOL | 60 |
| CONTAINS | 529 |
| EXPORTS_SYMBOL | 68 |
| HAS_CANONICAL | 7 |
| IMPORTS | 71 |
| IMPORTS_SYMBOL | 64 |
| MEMBER_OF_CLONE_GROUP | 283 |
| MEMBER_OF_FILE_CLONE_GROUP | 16 |
| **Total touching staging nodes** | **1,639** |

Of these, 1,579 connect two staging nodes; 60 cross the namespace boundary. Except
for the 71 IMPORTS edges, the inspected relationships have no `project_id`
property. Endpoint scope therefore matters when assessing cleanup effects.

## Cleanup and prevention implications

The failed persisted job is terminal, its old PID is no longer present locally,
no persisted running/cancelling jobs for this project were found, and later runs
have published successfully. These are concrete abandonment indicators. The
new cleanup policy still protects this legacy namespace because it has no
`ShadowRun` ownership record. The blocker is now legacy adjudication support,
rather than an unexplained promotion failure. Do not fabricate ownership or
change a historical run to `done` to bypass the guard.

Next work:

- Add an explicit legacy adjudication procedure using exact namespace/run/job
  evidence, current activity checks and a preview of all incident relationships.
- Make health and cleanup previews distinguish property-scoped relationship
  counts from endpoint-scoped totals and boundary edges.
- Attribute authentication failures to clients using bounded, redacted diagnostics.
  Repeated retries with invalid credentials can worsen lockout; correct the source
  before choosing recovery/retry behavior.
- Preserve failure evidence outside Neo4j when Neo4j itself prevents status writes.
  New lifecycle records protect uncertain writers but cannot resolve a lost
  terminal write or coordinate graph/vector publication by themselves.

The newly implemented atomic structural replacement prevents a separate partial
publication failure mode. It does not correct the authentication-rate-limit source.

## Retained evidence

Local job logs and state live under `.runtime/jobs/0b72c1fe/` and
`.runtime/jobs/03074bea/`. The server evidence came from the running Neo4j
Desktop instance's `security.log`; unrelated older Desktop instance logs did not
cover the target date. No credential values are included here.

[Machine-readable receipt](shadow-run-investigation.json) records source hashes,
the database snapshots, relationship counts and remaining uncertainty. Logs remain
local ignored evidence; their retention is required to reproduce this investigation.

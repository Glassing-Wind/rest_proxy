# Legacy shadow adjudication and authentication isolation

Completed October 4, 2026. The abandoned rentallaw staging graph was removed
through an explicit, guarded administrative path. This extends
[indexing lifecycle safety](indexing-lifecycle-safety.md), while preserving the
original [failure investigation](../benchmarks/reports/2026-10-04/shadow-run-investigation.md).

## Authentication trigger and fix

A monitored CI run reproduced three invalid-credential attempts followed by
Neo4j authentication rate limiting. A second monitored graph regression run
narrowed the burst to `test_index_workspace.py`.

That test loader stubbed `neo4j` only while executing the indexer module. Its
helpers import Neo4j lazily when called later, after the stub had been removed.
Because dotenv was also stubbed, those helpers could connect to the actual local
server with default credentials. Errors were handled as optional-storage failures,
allowing tests to pass while legitimate indexing clients could be locked out.

The test now retains a complete offline Neo4j client stub for each test's entire
lifetime, including lazy imports. Explicit per-test graph mocks still override it.
A regression checks that late status/run helpers use the offline factory.
All 22 semantic-indexer tests pass. Monitored graph regressions and full CI then
passed with **zero new invalid-credential or rate-limit events**.

This establishes a reproducible current trigger and its repair. The historical
October 3 server log did not identify the originating client directly; attributing
that exact burst to this test remains an inference consistent with the reproduction.
The native indexer and finalizer both receive the same explicit credentials; the
review did not find a credential mismatch between them. No password or Neo4j
authentication-protection setting was changed.

Structural Python clients now advertise process-tagged user agents. Indexing,
publication and failed graph-status writes preserve best-effort local diagnostics
under `.runtime/index-failures/`. Those records contain scope, phase, time, host,
PID, exception class and an allowlisted error category; they omit raw exception
text, credentials and credential hashes. An uncertain lifecycle terminal write
still leaves protected ownership and also attempts a local failure record.

## Explicit administrative workflow

Run with project Python from the repository root:

```bash
.venv/bin/python scripts/adjudicate_legacy_shadow.py \
  --namespace '<exact legacy namespace>' \
  --failed-job '<failed local job ID>' \
  --superseding-job '<current successful local job ID>'
```

The default is a read-only preview. It reports the content digest, node count,
all incident/tagged relationships, boundary count and evidence hashes. Applying
requires the same arguments plus `--apply --expected-digest '<preview digest>'`.

The administrative path requires:

- Exact project/run/worker linkage to a terminal failed local job and a later
  successful local job, with inactive recorded worker PIDs.
- No persisted active project jobs, and the shared project indexing lock for apply.
- A successful superseding job matching the current published graph run.
- Graph timestamps within the corresponding job intervals; an unpromoted failed
  structural run; no tracked ownership for the legacy namespace or running writer
  for that project.
- Matching `last_seen_run` on every staging node and an unchanged snapshot digest.

Before deleting anything, apply saves a private local snapshot and evidence receipt
under `.runtime/shadow-cleanup/<decision ID>/`, with restrictive permissions and
flushed writes. The snapshot includes staging nodes, incident relationships and
boundary endpoint identities; preserve it for recovery review. It is not a full
database backup or a tested general restore procedure.

A managed transaction locks the canonical Project record, rechecks graph state
and snapshot contents, deletes only exact staging nodes and their incident/tagged
edges, and records a separate `ShadowAdjudication`. Boundary endpoint nodes and
historical IndexRun status are preserved. Changed publication, active/unknown
ownership or changed staging contents refuses the operation. No ownership record
is fabricated to bypass normal cleanup protections.

## Completed cleanup and verification

The initial apply attempt refused because a newer indexing job had published
since the previous preview. No deletion occurred. Evidence was refreshed to
successful job `3f3e67a5`, published run
`7f6b7aacd978:38928:1791131090750000128`.

The subsequent apply removed **582 staging nodes and 1,639 relationships**,
including 60 boundary relationships, while preserving boundary nodes. The live
canonical node/relationship fingerprint and structural/semantic publication IDs
were identical immediately before and after cleanup. The historical failed run
remains `struct_written`, with no promotion timestamp. A separate decision record
`legacy-shadow-bdc2a48a461a457e838c927f46aeae40` records the action and evidence.

The graph now has zero shadow nodes and relationships, and all disposable test
nodes were removed. Four registered MCP checks passed: project overview, indexing
health, bounded current-source retrieval and zero-residue inspection. Cleanup and
source direct/MCP parity checks also passed. An earlier parity attempt during the
pre-fix CI authentication burst failed; that attempt is retained as evidence.

Eight adjudication/failure checks passed (four using disposable live fixtures),
alongside six live lifecycle checks, 21 indexing-health checks and full local CI.
Live checks opt in with `LM_PROXY_TEST_SHADOW_LIVE=1`; default CI skips external
fixture checks. Safety tests cover changed previews, active writers, mixed
provenance, boundary-node preservation, unchanged history and local redaction.

See the [machine-readable cleanup receipt](../benchmarks/reports/2026-10-04/shadow-cleanup.json).
Local diagnostic logs, snapshots and monitored authentication traces remain under
`.runtime/indexing-lifecycle/` and `.runtime/shadow-cleanup/`.

## Remaining operational limits

The legacy procedure depends on retained local job evidence. It does not reclaim
uncertain remote writers or `running` lifecycle records merely because a heartbeat
expired. Multi-host coordination, a tested snapshot restore procedure, lifecycle
retention and atomic graph/vector publication remain separate work.

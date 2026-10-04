# Structural indexing lifecycle safety

Implemented October 4, 2026, following the Python 3.14 / ts-pack merge.

Structural indexing builds `<project_id>::shadow::<run_id>` staging graphs. A
`ShadowRun` record now tracks the canonical project, exact namespace, run ID,
unique writer token, host, PID, start time, heartbeat and terminal state. The
wrapper owns this record through native indexing, finalization and publication.
Heartbeats are attempted every 15 seconds. Missing heartbeats never authorize
deletion; native work or a storage outage can delay them.

Normal completion records `finished`; handled failures record `failed`. Abrupt
process termination or an uncertain terminal write leaves a protected `running`
record. A separate administrative procedure for proving abandoned ownership is
still needed; cleanup offers no override for those records or legacy namespaces.

## Inspect and clean

Use `cleanup_stale_shadow_graph()` for read-only inspection. It reports exact
namespace IDs and ownership information. Counts alone do not prove staleness.

Deletion requires explicit selection:

```python
cleanup_stale_shadow_graph(
    dry_run=False,
    namespaces=["<exact namespace from inspection>"],
)
```

Only a single, matching ownership record with terminal state, writer token and
finish time is eligible. Active project indexing also blocks cleanup. Every
bounded deletion transaction locks the ownership record and rechecks those
conditions. Unknown or ambiguous owners are protected. Previously supported
unscoped deletion is now refused.

## Atomic publication and failure reporting

Canonical structural replacement, staged node/relationship promotion, staging
cleanup and the canonical identity invariant check run in one managed transaction.
A query failure or invariant violation rolls back this replacement instead of
leaving canonical deletion committed independently. Transaction retries apply to
the complete replacement. Finalization failures now return a nonzero process exit.

This transaction covers structural graph replacement. Graph/vector publication
and the later project/run status write remain separate operations. Large-repository
transaction cost and timeout behavior need measurement before changing the
120-second promotion timeout or introducing another publication strategy.

## Verification and observed legacy residue

- `test_shadow_lifecycle.py` checks explicit selection and active/unknown owner
  protection without external services.
- `LM_PROXY_TEST_SHADOW_LIVE=1 .venv/bin/python test_shadow_lifecycle.py` additionally
  uses unique disposable Neo4j project IDs. It checks expired-running protection,
  project activity, terminal cleanup, handled writer failure, nonzero finalization
  failure, and replacement rollback/success. Fixtures are removed in teardown.
- The existing indexing-health regression suite also passes.
- Full local CI passed. A native one-file indexing smoke completed with a finished
  lifecycle record, six canonical nodes and zero staging nodes. The first smoke
  attempt hit a Neo4j authentication rate limit while CI was running; a sequential
  retry passed. Both attempts are retained under `.runtime/indexing-lifecycle/`.
  Disposable fixture nodes were removed and their absence checked afterward.

Read-only inspection on October 4 still found 582 nodes in
`7f6b7aacd978::shadow::7f6b7aacd978:11055:1791067739470000128`. Its canonical
project reported `done`, while its IndexRun reported `struct_written`. It predates
tracked ownership; no deletion or terminal-state reassignment was performed. The
October 3 report's 71 relationships are a historical count, not a fresh count from
that node inspection.

Remaining work: abandoned-run adjudication/recovery, retention for lifecycle
records, coordinated graph/vector publication, and the existing retrieval golden
discrepancy. Embedded storage and FIRE persistence/budgeting remain milestones in
the [integrated platform plan](integrated-platform-plan.md).

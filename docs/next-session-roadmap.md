# GraphRAG evaluation roadmap — revised 2026-09-06

## Decision and scope

Keep retrieval behavior fixed until paired investigations reveal a reproducible weakness.
The original sequence is revised because the remote Security workflow never reached
scanning: its Gitleaks Action requires an organization license. Running the pinned,
checksum-verified CLI exposes a historical credential finding. Do not merge while
revocation is unconfirmed. Do not silently baseline or rewrite history to turn CI green.

## 1. Resolve release readiness

- `codex/enterprise-hardening` was clean and already current at `b05363c`.
- Remote CI for that revision passed (run 34045220918); Security failed (34045220901).
- Replace the licensed Action wrapper with the MIT-licensed Gitleaks CLI 8.30.1,
  verify the release archive SHA-256, scan all fetched history, and redact output.
- A Tavily-shaped credential was found in historical commit
  `04667ea6a080c8290926884d5d5ac79d74693fbb`, `session-ses_2b4d.md:768`.
  The file is absent from the current checkout. The user confirmed that revocation
  is not confirmed. Revocation/rotation is the next required owner action.
- After revocation, agree on narrowly scoped treatment of the historical finding;
  do not add an exception before then. History rewriting requires a coordinated plan.
- Update PR #1 with this session's concrete changes. The existing PR spans 232 files
  and over 53,000 added lines at the baseline; this session's targeted review is not
  a complete approval of that accumulated diff. Keep it unmerged.

## 2. Publish a paired pilot, then improve measurement

Run the existing 15 prompts in two fresh, non-inheriting agent contexts. Native uses
file/search tools; MCP uses brain tools with counted native fallbacks. Hold subject
source code at `b05363c`; record warm index health and setup independently.

Label this run a pilot: contexts share a machine, cases may be interleaved, MCP per-case
time measures tool latency while native measures end-to-end time, and token counts are estimates rather than model usage.
Do not claim end-to-end speed or token savings from these records. Correctness must
be reviewed against source; expected-evidence substring coverage is only a proxy.

Publish raw results, measurement methods, all 15 paired rows, and a case classification.
The comparator now records revision, token/timing method, and fallback counts and
rejects mismatched revisions and suppresses deltas for differing timing methods. CI validates and uploads the recorded
pilot; it does not rerun agents or impose blocking performance thresholds.

For a confirmatory run, capture runner-provided input/output usage, end-to-end timing,
actual tool logs, tool availability/model settings, and revision/index identity. Run
conditions sequentially with randomized order across repeated runs, keep grading
separate, and hide expected-evidence hints from investigating agents. Repeat with
cold/fresh, stale, and partial indexes and a second unfamiliar repository.

## 3. Select improvements from evidence

Use the pilot report to identify three priorities and cite repeated affected cases.
Treat isolated observations as hypotheses until reproduced. Separate retrieval
quality, output usability, tool selection, and index contamination. Implement behavior
changes in separate patches with regression cases and the retrieval-quality gate.

## 4. Prepare one dependency batch

The unbaselined audit reports 116 findings in 19 packages. Prepare only aiohttp,
python-multipart, and urllib3 in the first patch; defer MCP/Starlette upgrades to a
separate compatibility review. See `security/batches/2026-09-06-http-clients.patch`
and its README. Both requirement files and resolved baseline IDs move together.
Do not apply it to the live environment during measurement. Resolver/audit checks
are preparation, not runtime compatibility proof. Full CI and retrieval gates in
an isolated candidate environment remain required before adoption.

## 5. Operational cleanup

The brain dry run reports one shadow namespace with 4,478 nodes and 1,031
relationships. It does not expose the project ID in its response. Defer deletion
until measurements finish, the exact namespace is reviewed, and no active index
jobs are confirmed. Then dry-run again, clean, and verify all benchmark repositories.
Do not treat timestamp freshness alone as proof of an uncontaminated index.

## Stopping criteria

A complete, honestly labeled 15-pair pilot and three source-backed priorities;
reviewed CI fixes published to PR #1; a separate prepared dependency patch; and
explicit unresolved items for credential revocation, green Security, confirmatory
usage measurement, full PR review, and quiet-window graph cleanup.

## Post-run operational finding

The initial warm index did not stay fixed: automatic structural runs advanced while
semantic updates failed. See `benchmarks/reports/2026-09-06/post-run-index-health.md`.
PostgreSQL 17.9 is running from an installation path that no longer exists, and its
text-search library cannot load. Restore the matching installation and coordinate
any shared-database restart before reindexing. Investigate the separate native
`SELECTchunk_id` staging error in pinned ts-pack. Confirmatory benchmarking and
shadow cleanup must wait for stable indexing; no cleanup was performed.

## Recovery and first benchmark-backed fix completed

PostgreSQL 17.11 was installed and started through Homebrew. The TimescaleDB,
pg_cron, pgvector and text-search libraries were present; database connections,
full-text queries and vector operations passed. PostgreSQL is now marked as
installed on request. A subsequent rest_proxy incremental index job `9290d4a5`
completed both phases successfully; the earlier PostgreSQL/SQL errors did not recur.
The health tool reports 297/297 structural and semantic files and healthy alignment.

The first product fix excludes `::shadow::` project namespaces from exact-name
`find_definitions` results in both the database query and output filtering. This
addresses three repeated pilot cases while preserving legitimate nested projects.
Eight focused graph-query tests passed, and all three affected lookups were replayed
against the live graph before cleanup. The full retrieval-quality gate passed,
including protocol lifecycle, tool-choice, investigation workflows, live graph
regressions and MCP parity.

After the gate, the rest_proxy watcher was temporarily unpinned. The brain reported
no active jobs; a host worker check was empty. A new dry run and direct namespace
inspection confirmed only `6f8dead37cb2::shadow::6f8dead37cb2:41619:1788546631249999872`.
Cleanup removed 4,478 nodes and 1,031 relationships, leaving zero shadow residue.
The original watcher pin was restored, and a final health check was healthy.

Next: implement citation-ready bounded source output and replay tool-selection
failures; then rerun a properly instrumented paired benchmark on an isolated index.
The prepared dependency patch still needs isolated runtime validation before applying.
The historical credential remains unrevoked/unconfirmed and keeps Security blocking;
do not merge or add an exception. The original pilot artifacts remain historical
observations of the pre-fix, unstable-index condition.

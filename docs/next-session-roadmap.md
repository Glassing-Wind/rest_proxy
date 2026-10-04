# GraphRAG evaluation roadmap — revised 2026-09-06

## Current checkpoint — October 4, 2026

Fork PR #1 and rest_proxy PR #1 are merged. Post-merge hosted CI and Security
passed; both requirements files retain fork SHA
`e1c99f71478dd1d2f974cb02e4038424d21a12ce`. Earlier pending/draft entries below
describe historical checkpoints.

The next implementation adds tracked shadow ownership/heartbeats, explicit
namespace selection and terminal-state cleanup guards, atomic structural graph
replacement, and nonzero finalization failure exits. Six focused checks (including
disposable live Neo4j fixtures), the 21 indexing-health regressions and full local
CI passed. See [indexing lifecycle safety](indexing-lifecycle-safety.md).
Legacy 582-node residue remains protected because its ownership is unknown.

The [legacy run investigation](../benchmarks/reports/2026-10-04/shadow-run-investigation.md)
now confirms authentication rate limiting blocked finalization and failure-status
recording; a subsequent run published successfully. Identify the bad-authentication
client, add explicit legacy adjudication, and fix relationship previews: 71 edges
carry the namespace property, but 1,639 touch its staging nodes.

Next: abandoned-run adjudication/recovery and coordinated graph/vector publication;
resolve the existing 80-versus-81 retrieval golden discrepancy; complete owned
Ladybug/LanceDB indexing and retrieval; implement FIRE persistence/resume and
whole-request budgeting; finish distribution notices and controlled outcome
evaluations. The [smart-glasses direction](smart-glasses-project-direction.md) is
a separate project and does not extend this application's implementation scope.

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


## Citation output and selection follow-up

Added `describe_file(..., include_source=True)` for bounded current-file source,
complete numbered lines, a SHA256 snapshot hash, and explicit continuation. It
bypasses the index and includes module settings. The default outline is unchanged.
Catalog requests containing a concrete source filename now select `describe_file`;
the usage guide distinguishes known-file evidence from semantic discovery.

The source replay in `benchmarks/reports/2026-09-06/source-replay.json` covers the
primary evidence file for each of the 15 pilot cases. All 15 MCP excerpts match
native source and content hashes. It measures client operation wall time and UTF-8
bytes, with MCP session setup separate and alternating condition order. This is a
known-file tool replay, not a clean agent benchmark: it cannot establish answer
accuracy, discovery performance, or model token savings. Original pilot results
remain unchanged. Reproduce with `python scripts/replay_source_evidence.py --output
/path/to/source-replay.json`.

A confirmatory agent experiment remains pending: require isolated stable index,
identical prompts without evidence hints, consistent end-to-end timing, actual
model usage telemetry and independent grading. Do not promote replay timings to
agent performance claims. The prepared security batch and historical credential
revocation remain open as recorded above.

Validation: 98 focused tests, the full local CI checks, and the complete live
retrieval-quality gate passed after incremental index job `355542cd` completed
both phases for 300 files. Staged changes passed redacted secret scanning.


## September 13 credential resolution

The owner confirmed deletion of the exposed Tavily key. Recorded that confirmation
in `docs/security.md` and added one commit-specific Gitleaks fingerprint for the
revoked historical occurrence. Earlier entries describing revocation as unconfirmed
are historical status, superseded by this update. No history rewrite or broad
scanner exclusion was made. Remaining merge requirements still include current
remote checks and review of the accumulated branch; deletion alone does not grant
merge approval or complete the pending agent benchmark/security dependency batch.


## Tavily removal follow-up

Removed Tavily from documentation discovery, both requirements manifests, and the
example configuration. Replaced the obsolete duckduckgo_search client with
`ddgs==9.16.0` and its compatible `primp==1.3.1` requirement. Both discovery tools
use key-free web search off the event loop; Crawlee/Trafilatura still crawl and
extract known URLs. The lmproxy environment has the replacement installed, the
obsolete packages uninstalled, and its obsolete Tavily assignment removed.

Three offline tests cover response normalization, deduplication, thread dispatch,
empty/unavailable search, and fallback output. Local CI and pip check pass. A live
MCP research_documentation call returned ten results including official Crawlee
quick-start and introduction pages without Tavily. The audit currently blocks on
NLTK 3.9.4 advisories PYSEC-2026-3955 and PYSEC-2026-3954, not the changed search
packages. No baseline exceptions were added. Handle NLTK in a separate compatible
security change; the earlier HTTP-client candidate patch remains unapplied.

The complete retrieval-quality gate also passed after the Tavily removal, including
protocol lifecycle, tool-choice, live graph regressions and MCP parity.


## September 14 NLTK security batch

Upgraded NLTK 3.9.4 to 3.10.3 in both requirement files and lmproxy after isolated
candidate checks. The package audit changed from 53 advisory records to one;
removed 27 resolved IDs from the baseline, preserving the still-reported
PYSEC-2026-3740 (no published fix). Both previously blocking NLTK findings are
resolved. Counts include advisory aliases and are not counts of distinct exploits.
See `security/batches/2026-09-14-nltk-audit.json`.

Added offline checks for shared-address-space rejection under default enforcement
and text tokenization. An isolated candidate also passed Crawl4AI import/chunking.
No corpus was downloaded and no unsafe NLTK model-loading API was exercised.
This is one compatibility-tested dependency batch, not resolution of all baseline
debt. The larger PR remains open pending remote checks and full branch review.

## September 30 enterprise tooling follow-up

Fixed dependency-manifest runtime fingerprinting and source-project identity in
cross-project graph tracing. Added offline regressions and a rollback-only live
Neo4j identity regression. Removed confirmed inactive shadow residue (4,576 nodes,
379 relationships). Local CI and the full live retrieval-quality gate passed.

Ran six fresh investigation sessions and an independent source-based grading pass.
All answers passed, but the intended MCP sessions had no exposed MCP tools, so
the comparison is excluded. The runner now requires an observed successful MCP
health call rather than trusting configuration alone. Resolve isolated CLI MCP
exposure before repeating; no productivity or superiority claim is supported.
See [the dated report](../benchmarks/reports/2026-09-30/README.md) for measurements,
failed-attempt accounting, evidence and remaining experiment requirements.

## October 3 plan correction

Follow [the evidence-driven implementation plan](enterprise-tooling-plan.md).
Fresh CLI MCP availability now passed with an observed health call. Keep the
September 30 comparison excluded. Compact-profile schema counts must be measured,
not inferred from tool counts. Current-file outlines now replace indexed symbols
and explicitly report unknown content alignment; no full graph overlay is claimed.
The Compose stack is a development appliance pending actual lifecycle validation.
Kuzu's archived status reopens graph-engine selection; embedded support stays an
experimental feasibility track. See the dated report for verification and limits.

October 3 validation is complete: full CI and the live retrieval-quality gate
passed; a real isolated Compose stack passed indexing/query/restart checks and
source reads during a graph outage. The compact schema measured 3,687 tokens
versus 16,414 for all tools (77.54% reduction), not the original target.
The six-session warm-index pilot was protocol-valid, but MCP aggregate elapsed
was 1.41% greater and input tokens 71.60% greater. Independent grading passed all
citation/completeness checks; native correctness passed, MCP correctness was
partial for source-unverifiable runtime assertions and one default-threshold
overgeneralization. No superiority is supported. Embedded graph feasibility failed
query compatibility, rollback and independent connections; keep it experimental.
See [the October 3 report](../benchmarks/reports/2026-10-03/README.md) for raw rows,
grading, failed attempts and limits. Next prioritize repeated compact-profile
outcome measurements, native indexed content hashes and maintained graph selection.

## Context continuity direction

Follow [the context platform direction](context-platform-direction.md). The native
embedded target remains LadybugDB + LanceDB with Docker optional. Add a shared,
provider-independent context assembly contract: select evidence, concatenate within
complete-request token budgets, retain provenance/freshness, and checkpoint scoped
task state. Reuse memory/retrieval.py and memory/types.py foundations. Prioritize
bundle contracts and budget tests alongside embedded integration; verify MCP/REST
parity, resumption, invalidation and no duplicate injection. “Limitless context” is
persistent retrieval continuity, not unlimited model input or guaranteed recall.

## Coordinating plan

Use [the integrated platform plan](integrated-platform-plan.md) to sequence native
packaging, backend parity, context assembly and release acceptance. Keep Apple
Python untouched; retain project Python 3.11.15 while validating 3.14 separately.

## Operational Python target

The local daemon and project scripts now default to Python 3.14 through `.venv`.
Retain the old 3.11 environment for explicit rollback. Use
[the reproducible setup and rollback procedure](python314-runtime.md); the pinned
ts-pack build disables release stripping and validates the native import.

## FIRE and compaction continuity

The accepted platform name is **FIRE — Find, Integrate, Retrieve, Explain**.
Follow [the FIRE implementation slice](fire-platform.md): scoped evidence/checkpoint
contracts, provenance-preserving retrieval, budgeted bundles and opt-in resume
tools. Validate recovery after the original prompt is removed, corrections,
source changes, scope isolation and storage failure. Existing compact strings and
truncated tool records do not establish recoverable originals. Client compaction
is not automatically observable; checkpoint/resume must work explicitly.

First FIRE slice implemented: versioned EvidenceReference and TaskCheckpoint
contracts plus structured retrieved_evidence on AssembledMemory. Ranking retains
returned provenance; exact-text deduplication preserves distinct shared-prefix
evidence. Graph memory search returns source identity and scope. Four offline FIRE
regressions are included in CI; local gated checks passed on Python 3.14. Persistence,
original-evidence recovery, token budgeter and resume tools remain next steps.

## ts-pack upgrade investigation

Follow [the verified fork-upgrade investigation](ts-pack-upgrade.md). Upstream
v1.20.0 binary import/parsing passed on Python 3.14, but five required native fork
helpers are absent and process() returns a typed object instead of our dictionary
contract. A merge simulation found core/binding conflicts alongside fixture churn.
Keep the current requirement pin until an isolated integrated fork passes the
indexing/semantic contracts. Include the local Swift fix beyond the deployed pin.

The isolated codex/fire-ts-pack-120 candidate now builds a loadable macOS ARM64
Python 3.14 wheel while retaining native fork helpers. Core 313/index 144 tests
passed; two ignored cache tests passed separately; two other ignored tests remain
unrun. Final-wheel project CI passed with shared existing dependency versions.
Review preserved extraction semantics and upstream fixtures, then run clean-profile,
isolated indexing/retrieval and platform validation before publishing or repinning.
The ordinary daemon still runs the previous dependency pin.


Clean full-manifest Python 3.14 install and isolated full CI now pass without
shared dependency paths. A mixed-language fixture indexed 11 files and 64 chunks;
registered health, source, symbol and search probes passed with aligned runs.
Synthetic embeddings validate plumbing only. See the upgrade document for the
first attempt's `.env` isolation failure and verified cleanup. Remaining gates are
extraction parity, platform builds and artifact/license notices before repinning.


Declaration parity gaps were found and repaired in the candidate: ten declaration
integration checks now pass, retaining Swift Protocol/Extension fork vocabulary.
Core/index suites, rebuilt macOS and Linux ARM64 wheel contracts and repeated
isolated CI/indexing/retrieval pass. Source/archive/patch hashes are recorded in
`ts-pack-platform-parity-validation.json`. The candidate has an unexecuted native
platform workflow. Windows/x86_64 execution and notice reconciliation (historical
Neo4j crates, downloaded grammars; MPL source already bundled) remain before
commit/push and production repinning. See the upgrade document for limits.


Development/CI now uses published fork SHA
`4342fa4251d5abed1ce8c5da595680189ca2b962` in both requirements files and the
refreshed Python 3.14 daemon. Remote-SHA wheel build, contract tests, full CI,
isolated indexing/retrieval and 68 direct/MCP transport parity checks passed.
Full content goldens retain a pre-existing rental 81-vs-80 link mismatch, proven
identical under old/new wheels. macOS, Ubuntu and Windows native PR jobs all passed. See `benchmarks/reports/2026-10-04/ts-pack-cutover.json` for rollback,
source-snapshot correction and outstanding distribution notices. Fork PR #1
remains draft; rest-proxy working changes remain uncommitted.


## Project checkpoint — 2026-10-04

The accumulated platform work was reviewed for repository scope and credential
patterns, then full CI passed again. Build scripts, Docker and release installation
now default to `TSLP_OFFLINE=1`, matching the validated fork artifacts; parser
assets hydrate at runtime and this does not establish a fully offline appliance.
This checkpoint records the Python 3.14 runtime, pinned fork upgrade, optional
provider/embedded prototypes, FIRE contracts, tooling changes and dated evidence
on `codex/enterprise-hardening`. Enterprise distribution notices, embedded backend
query/transaction integration, FIRE persistence/resume and controlled outcome
improvement remain unfinished. Private environments, native build artifacts and
rollback runtime remain ignored local data.


Merge checks: both manifests now pin fork follow-up
`e1c99f71478dd1d2f974cb02e4038424d21a12ce`, restoring CLI Docker build membership.
New audit findings were repaired with isolated dependency upgrades; 12 resolved
baseline IDs removed, no new advisory exemptions. Full-history secret scan passes
with an exact recorded-hash false-positive acknowledgement and same-line future
finding regression. Patched runtime CI/pip checks and refreshed daemon pass.
See `security/batches/2026-10-04-merge-checks.json`. Fork validation and native
platform jobs pass; all-grammar Docker and refreshed main hosted checks must finish
before a merge recommendation. Enterprise packaging notices remain release work.

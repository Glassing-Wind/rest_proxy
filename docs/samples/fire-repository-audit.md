# FIRE Demonstration Audit

## Can failed indexing publish partial evidence?

A reproducible demonstration audit of Glassing-Wind/rest_proxy.

11 October 2026 | Public specimen | Focused technical assessment

Audited revision: d2ae74c4adc6233981b16d38b26ae290e023a47b

| 56 focused tests | 8 source files | 3 paired questions |
| --- | --- | --- |
| Passed locally under OS network denial | 76 indexed chunks / 125 symbols | 9 selected excerpts matched exactly |

### Verdict

The tested owner protocol preserved the previous publication after failure and refused corrupted stored chunk content. Readers use the published run, rather than an unfinished candidate. These results support the scoped publication and citation behavior tested here. [E1-E4, E9-E11]

A valid snapshot can still be out of date. Editing a live source file left the old published source available with its original valid hash. Before acting on a finding about current code, bind the evidence to a fixed revision or compare current hashes and reindex. This audit verified all eight indexed file hashes against the pinned checkout. [E1, E2]

### Commercial use

Use this sample to demonstrate a source-backed investigation service. It shows an answer, reproducible checks, evidence links and explicit unknowns. It is a self-audit of the owner's public repository, not a customer testimonial or independent product certification.

Proposed offer: $900 Repository Intelligence Audit for one repository, one subsystem or issue, and up to three agreed questions. The companion one-page summary defines the deliverable and access controls. No speed, token savings or broad production-readiness claim is made.


## Scope, provenance and method

Specimen: [Glassing-Wind/rest_proxy](https://github.com/Glassing-Wind/rest_proxy). Connected GitHub metadata confirmed public/main. A detached HTTPS Git audit copy was used. Remote HEAD remained d2ae74c4adc6233981b16d38b26ae290e023a47b at the final check. No tracked source changes, commits, pushes or production state changes were made.

| Question | Scope of answer |
| --- | --- |
| Q1. What makes a complete run visible? | Vector staging, graph transaction and published-run reads. |
| Q2. What survives failure or interruption? | Prior publication, attempt reconciliation and orphan cleanup. |
| Q3. What do citation checks prove? | Stored source/chunk integrity and the current-file freshness boundary. |

### Evidence layers

Source review: current code, tests and documentation at the pinned revision. Three parallel AI reviewers examined publication, evidence/privacy and release/service scope. This adds cross-checking; it does not constitute independent human security review.

Local execution: seven repository test scripts, production lint, a four-step disposable native-storage probe and an indexed/native evidence-access comparison. New fixture writes used separate state roots. Watchers were disabled. macOS sandbox-exec denied network operations during validation and comparison; a loopback connection probe failed with permission denied.

Remote recorded evidence: current-revision GitHub check metadata and the native job log were inspected. Earlier benchmark reports remain historical repository-authored evidence, separate from this audit's new execution.

### Publication path

| 1. Capture | 2. Stage | 3. Publish | 4. Read |
| --- | --- | --- | --- |
| Explicit source manifest; parse/hash bytes | Validate vectors; upsert run; build FTS; count rows | One graph transaction replaces originals, outlines and receipt | Read receipt; filter project/run; verify source and chunk hashes |

This is coordinated visibility across LadybugDB and LanceDB, not a distributed transaction or proof of power-loss durability. The source snapshot is captured file by file and does not itself store a Git commit identity. The fixed checkout and external hash manifest provide this audit's revision binding. [E1-E4]


## Verified findings: publication and freshness

### F1 / Verified behavior / Prior publication survives tested failures

Mechanism. The owner stages complete run chunks before calling the graph publisher. The graph transaction replaces the project's source, symbols, relationships and publication receipt together. Search resolves that receipt and filters the vector store by both project and published run. [E1:126-158; E2:151-201; E3:87-129]

Observed. Native tests passed failures at vector staging, FTS creation and graph publication; partial graph-write rollback; killed uncommitted graph writes; killed staged-run writers; cancellation and reopen. A separate probe changed a fixture file, staged a new run and injected a publication failure. The old authenticate evidence remained readable; the new changed evidence was absent from search. One orphan candidate row remained until explicit cleanup. [E9-E11; focused-probe.json]

Limits and action. Failure preservation is not an API for rolling back an already successful publication: replacement removes the earlier graph snapshot. Raw engine clients are outside the owner protocol. Cancellation or delivery failure after commit can correspond to a successful publication, so operators must reconcile the receipt and attempt state before retrying. Power loss, remote writers and arbitrary storage corruption were not established by these tests. [E1, E4]

### F2 / High operating priority / Correct hashes do not prove current source

Mechanism. Source reads require the stored source run to match the publication and verify its stored content hash. Search checks the retrieved file hash against the published manifest and recomputes the chunk-content hash. These are consistency checks, not semantic correctness or adversarial authenticity guarantees. Neither path rereads live source files to establish freshness. Source reads identify their origin as published source snapshot. [E1:140-165; E2:231-264]

Observed. After editing the separate fixture without reindexing, the stored source hash differed from the live file hash while the old source remained available. Native chunk-content corruption was refused with Retrieved chunk content hash mismatch. The comparison index's eight manifest hashes matched the pinned repository before and after the investigation. [focused-probe.json; corpus-manifest.json]

Recommendation. Attach commit SHA, run ID and source hashes to every customer finding. Before a current-code conclusion or change, compare relevant file hashes or rebuild the index. Label preserved originals as historical whenever freshness is unknown. Add acceptance for file edits, deletions and changed branches. The report demonstrates this procedure; automatic freshness enforcement remains an open product gate. [E12]


## Operating boundaries that matter to customers

### F3 / Medium operating priority / Cleanup is separate from publication

Failed runs may retain physically staged rows while the prior receipt remains visible. The native probe confirmed one candidate row and removed it from the active table with cleanup. The shared FTS index is rebuilt before graph publication; corpus statistics can therefore change as runs are staged. Published-run filtering establishes result scope, not invariant lexical rankings. No ranking-change measurement was performed. [E1:294-313; E3:87-98,136-140; E17:33-46]

Action: inspect the receipt, schedule guarded orphan cleanup and treat rankings as candidates for source review. Define storage quotas. Project deletion hides graph visibility before vector cleanup; a cleanup failure can leave inaccessible rows. Table deletion alone does not demonstrate erasure from storage histories, disk remnants, archives or exported reports. Define copy-aware retention/deletion rather than promising secure erasure. [E1:300-313; E13:74-94]

### F4 / High before shared exposure / REST reads need access control

The opt-in REST handler caps requests at 16,000 bytes and serialized results at 48,000 bytes, allowlists read tools, refuses mutations/unknown arguments and restricts search to text mode. All four REST contract tests passed. The inspected handler supplies no caller authentication or per-project authorization; its route is on the outer application before the MCP mount. Direct launch binds to loopback. No unauthorized remote access or public data exposure was demonstrated. [E6, E7]

Action: keep source access local to an authorized operator. Before any shared or remote endpoint, implement and test authenticated per-project access. Do not treat the mounted MCP transport's controls or guessed project IDs as protection for this REST route. The sample did not start an externally reachable listener.

### F5 / Medium operating priority / Private state is an operator control

FIRE checkpoint storage rejects permissive directory/file modes (700/600). Embedded owner and vector-store constructors request private modes on creation, but these inspected paths do not tighten an existing directory. This is verified source behavior, not a reproduced permissions exploit. At-rest encryption was not verified. [E1:25-28; E3:34-37; E8:20-31]

Action: precreate and check separate customer source/state/output roots, restrict parent locations and use an agreed device/backup encryption policy. Include backups, originals, logs and exported reports in retention controls. The audit findings are scoped behaviors and operating limits; no new exploitable defect or repository-wide security clearance is asserted.


## Validation results and exact limits

| Executed locally | Tests | Result / coverage |
| --- | --- | --- |
| test_embedded_ladybug.py | 4 | Pass: native transactions, rollback, cancellation and schema. |
| test_embedded_outline_indexing.py | 4 | Pass: atomic replacement, kill, ownership and manifest bounds. |
| test_embedded_repository.py | 23 | Pass: native publication/retrieval, failures, reopen and owner integrations. |
| test_embedded_runtime.py | 14 | Pass: runtime routing, contracts and encoder boundaries. |
| test_embedded_rest.py | 4 | Pass: in-process REST contracts and route gating. |
| test_embedded_restore.py | 1 | Pass: quiescent cold-copy publication/FIRE fixture. |
| test_fire_store.py | 6 | Pass: scoped continuity, corruption, permissions and revision protection. |
| Total | 56 | All passed; no skipped tests reported. |

Additional checks: four focused native probe steps passed; nine selected paired source excerpts matched; production Ruff checks and dependency consistency passed. Raw local logs and machine-readable receipts accompany the evidence bundle. Test durations in those logs are execution records, not comparative investigation measurements.

Local environment: macOS ARM64, CPython 3.14.4, Ladybug 0.21.2, LanceDB 0.39.0, PyArrow 25.0.1 and modified ts-pack 1.20.0. Parser wheel SHA-256: 3cbb22e637cc1c7fa650c9fa54683586711863d5654f42e97c5f234230043027. This matches the repository's retained-wheel receipt; it is not a fresh local native source-build attestation. The isolated environment used core pins plus recorded resolved transitive versions, not the complete pinned CI profile.

Warnings retained: Pydantic settings reported an incomplete forward-reference definition; FastAPI reported deprecated startup/shutdown event APIs. Neither failed the selected tests. Full local CI was not rerun: its service-management steps extend beyond the isolated audit scope.

Hosted evidence: eight current-revision GitHub checks report success across CI and Security. The inspected embedded-native job records 4 + 4 + 23 = 31 passing tests on Ubuntu 24.04 x64 / Python 3.14.7 with the same engine pins. This verifies hosted native acceptance at this SHA and supersedes older documentation saying it was pending. It does not establish every supported platform/install path. [E16; hosted-native-excerpt.log]

Passing the baselined dependency gate is not a fresh clean security audit. The October 4 documentation still recorded 99 existing full-profile baseline rows. This audit did not rerun vulnerability or full-history secret scanning. [E14]


## FIRE versus file search: a bounded comparison

Protocol: both methods used the same pinned eight-file corpus, the same three questions and seven literal query terms. FIRE indexed current source through the native owner and used text search plus bounded published source reads. The native arm used literal file search (rg) plus numbered direct reads. Nine source windows were fixed before execution; both methods returned byte-identical numbered excerpts for all nine. All FIRE source hashes matched pinned file bytes.

| Question | Source conclusion supported by excerpts | Selected windows |
| --- | --- | --- |
| Q1. Visibility | Complete vector staging precedes the graph receipt transaction; reads use the published run. | 3 / 3 identical |
| Q2. Failure / recovery | Old receipt survives tested failure; restart reconciles attempts; cleanup removes other runs. | 3 / 3 identical |
| Q3. Integrity / freshness | Stored hashes/run identity are checked; current file freshness needs a separate check. | 3 / 3 identical |

Indexed identity: project fire-demo; 8 files, 125 symbols and 76 chunks. Published run 3b717488-df4a-46ef-a3c7-d8734cd43120. Manifest SHA-256 64bd77fdc29c71473031e144e73129c71e043b98adab2c30365dd56fd1a1dfe4. Required storage vectors were constant synthetic 3D fixtures; only lexical search and source access were evaluated. No embedding provider or model was used.

### What this comparison establishes

The inspected indexed evidence supports the same source conclusions as direct reads at these selected windows. A separate AI reviewer checked eight files against pinned Git blobs, all nine windows (178 lines), and the run/source/content hashes of 114 recorded search hits. This is an evidence cross-check, not independently graded agent answers. FIRE adds publication/run and source-hash metadata. Raw search responses include unrelated tokenized FTS candidates; literal grep and FTS do not have identical matching/ranking semantics.

### What remains unmeasured

The operator had already reviewed the code before selecting windows. This is a functional evidence-access check, not blinded discovery or an end-to-end agent trial. The index was warm for reads; index preparation was not compared. Search results were capped at 20 per term and selected excerpts were predetermined. No comparable task timing, total model-token usage, cost, semantic relevance or coding outcome was measured. No speed, token savings or retrieval-superiority conclusion follows.

The repository's October 3 paired agent pilot is historical evidence and was not rerun. Its own report establishes no MCP advantage and lists design/grading limits. A stronger next study needs unfamiliar tasks, identical source/index identities, repeated randomized pairs, independent answer grading, recorded fallbacks, cold/warm indexing overhead and actual usage. [E15]


## Security and privacy: used controls and client terms

### Controls actually used in this demonstration

Only the owner-authorized public specimen was used. A detached audit checkout preserved source provenance. The comparison index ingested eight named production Python files. Its path manifest omitted credential/configuration files, runtime data and unrelated customer content; no comprehensive secret scan was performed. Broader documentation and tests were read for context. Fixture/state paths were separate from operational databases; watchers were disabled.

macOS OS network denial covered local validation and comparison processes, including subprocesses. No live application account, production database, external embedding/inference provider or model lifecycle operation was used. REST contract calls ran in-process. GitHub acquisition/check inspection, dependency installation and parser preparation occurred outside that network-denied execution boundary.

Processing disclosure: repository excerpts and tool outputs were analyzed in this OpenAI Codex conversation. Local databases and network-denied test runs do not make the overall audit local-only. No blanket no-cloud processing, encrypted-storage or zero-retention claim is made. Shareable evidence replaces personal workspace paths; no raw database state or native binary is included.

### Agree these controls before a private customer audit

| Control | Proposed engagement term |
| --- | --- |
| Authorization / access | Written repository authority, read-only access and an agreed path/question scope; no production credentials required. |
| Processing / providers | Name permitted locations, AI services and recipients before code is processed. Obtain approval for any external source/model use. |
| Isolation / permissions | Dedicated customer roots; verified directory/file permissions; disabled watchers; authorized operator only; authenticated project access before shared endpoints. |
| Minimization / secrets | Review the input manifest; exclude sensitive exports/configuration; redact incidental secrets and handle notice through the agreed client contact. |
| Retention / deletion | Agree source/index/original/log/report retention and backup policy; reconcile expired/deleted copies and confirm closure. No implicit retention period assumed. |
| Review / release | Check source citations and grade unknowns; public demonstrations require client authorization. Code changes, deployment and outward messages require separate scope. |

These client terms are proposed operating commitments, not features proven fully automatic in FIRE. Disk encryption, backup encryption, endpoint authorization and complete secure erasure were not verified by this sample. The public evidence package is retained as the deliverable; other audit state remains local unless separately removed.


## Prioritized next steps and unresolved gates

| Priority | Concrete next step | Acceptance condition |
| --- | --- | --- |
| Before first paid audit | Agree one subsystem and up to three questions, revision, processing permissions, access and retention. | Signed scope; readable specimen; no undisclosed provider or production access. |
| Before current-code decisions | Validate source hashes or reindex; record commit, run and relevant file hashes. | Changed/deleted/branch-switched source is flagged before a finding is treated as current. |
| Before shared source hosting | Add authenticated per-project access and verify state/backup permissions. | Unauthorized caller/project reads are denied; deployment surface tested. |
| At audit closure | Inspect publication, clean unpublished runs and reconcile all retained copies. | Documented cleanup/retention result; exceptions and backup retirement recorded. |
| Before outcome claims | Run a repeated controlled paired investigation study. | Matched tasks/context, independently graded answers, actual usage, overhead and fallback accounting. |

### Incomplete gates reported candidly

Real-model retrieval at this revision: not run; no embedding provider was used. Synthetic vectors prove storage wiring only. Semantic relevance, model artifact/runtime identity and real installed-model acceptance remain separate work.

Broad product coverage: general graph/import/route/reference parity, new-file watch enrollment, deployed IDE integration, provider continuation/tokenizer/framing and automatic source freshness are incomplete in the pinned status table. These questions were deliberately confined to the supported owner/evidence path. [E12]

Release and operations: hosted Ubuntu native acceptance is verified here; a complete supported-platform/install matrix, fresh local exact-pin native build, complete dependency/grammar/model notices and SBOM closure, operational encrypted backup/delete reconciliation and production-scale restore objectives remain open. The passing cold-copy test uses quiescent disposable fixtures, not a live backup service. [E12, E13]

Security and agent autonomy: no comprehensive penetration test, new vulnerability/secret scan, authenticated multi-tenant acceptance, real model-worker evaluation or independent human correctness/security review was performed. A successful source hash check does not validate the reasoning attached to it. [E6-E8, E12, E14]

No code remediation was required to make this demonstration reproducible. Findings are delivered as source-backed behaviors, operating boundaries and acceptance recommendations. A broader product release or patch engagement should be scoped separately.


## Evidence register and reproduction

All source links below are immutable GitHub links at the full audited SHA. Line numbers are one-based. E9-E11 support the test mechanisms; executed results are the retained logs/receipts, not source inspection alone.

| ID | Source at audited revision | Lines |
| --- | --- | --- |
| E1 | graphrag_core/indexing/embedded_repository.py | 25-28; 62-165; 294-313 |
| E2 | graphrag_core/indexing/embedded_outlines.py | 23-110; 113-202; 220-267 |
| E3 | memory/embedded_lance_runs.py | 34-98; 101-140 |
| E4 | memory/embedded_jobs.py | 28-57 |
| E5 | memory/embedded_owner.py | 8-33 |
| E6 | tools/brain/embedded_rest.py | 7-55 |
| E7 | brain_server.py | 337-361 |
| E8 | memory/fire_store.py | 20-50; 132-210 |
| E9 | test_embedded_repository.py | 101-151; 701-750; 887-977 |
| E10 | test_embedded_outline_indexing.py | 19-150 |
| E11 | test_embedded_ladybug.py | 13-148 |
| E12 | docs/five-priority-status.md | 10-14; 18-29 |
| E13 | docs/release-inventory-and-restore.md | 74-94 |
| E14 | docs/security.md | 53-61 |
| E15 | benchmarks/reports/2026-10-03/README.md | 32-62; 88-110 |
| E16 | .github/workflows/ci.yaml | 99-126; 152-153; 287-296 |
| E17 | docs/embedded-run-publication.md | 33-46 |

### Immutable source links

- E1: [graphrag_core/indexing/embedded_repository.py, lines 25-28; 62-165; 294-313](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/graphrag_core/indexing/embedded_repository.py#L62)
- E2: [graphrag_core/indexing/embedded_outlines.py, lines 23-110; 113-202; 220-267](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/graphrag_core/indexing/embedded_outlines.py#L23)
- E3: [memory/embedded_lance_runs.py, lines 34-98; 101-140](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/memory/embedded_lance_runs.py#L34)
- E4: [memory/embedded_jobs.py, lines 28-57](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/memory/embedded_jobs.py#L28)
- E5: [memory/embedded_owner.py, lines 8-33](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/memory/embedded_owner.py#L8)
- E6: [tools/brain/embedded_rest.py, lines 7-55](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/tools/brain/embedded_rest.py#L7)
- E7: [brain_server.py, lines 337-361](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/brain_server.py#L337)
- E8: [memory/fire_store.py, lines 20-50; 132-210](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/memory/fire_store.py#L20)
- E9: [test_embedded_repository.py, lines 101-151; 701-750; 887-977](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/test_embedded_repository.py#L101)
- E10: [test_embedded_outline_indexing.py, lines 19-150](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/test_embedded_outline_indexing.py#L19)
- E11: [test_embedded_ladybug.py, lines 13-148](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/test_embedded_ladybug.py#L13)
- E12: [docs/five-priority-status.md, lines 10-14; 18-29](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/docs/five-priority-status.md#L10)
- E13: [docs/release-inventory-and-restore.md, lines 74-94](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/docs/release-inventory-and-restore.md#L74)
- E14: [docs/security.md, lines 53-61](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/docs/security.md#L53)
- E15: [benchmarks/reports/2026-10-03/README.md, lines 32-62; 88-110](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/benchmarks/reports/2026-10-03/README.md#L32)
- E16: [.github/workflows/ci.yaml, lines 99-126; 152-153; 287-296](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/.github/workflows/ci.yaml#L99)
- E17: [docs/embedded-run-publication.md, lines 33-46](https://github.com/Glassing-Wind/rest_proxy/blob/d2ae74c4adc6233981b16d38b26ae290e023a47b/docs/embedded-run-publication.md#L33)
Evidence ZIP: source/corpus hashes; publication and focused-probe receipts; raw paired query/excerpt responses; test/lint/network-denial logs; exact resolved environment; GitHub metadata/native log excerpt; frozen questions; harnesses; reproduction README and SHA256SUMS. Personal paths are normalized and documented; originals remain local. No live state, credentials or native binaries are packaged.

Reproduce: acquire this exact revision, install the recorded core/native dependencies plus the required modified parser wheel, then follow evidence/README.md. The harness requires macOS sandbox-exec; another OS needs an equivalent explicitly verified network control. Recheck source hashes and read publication/attempt IDs before comparing results.


Hosted native job: https://github.com/Glassing-Wind/rest_proxy/actions/runs/37602202540/job/112730059178
CI run: https://github.com/Glassing-Wind/rest_proxy/actions/runs/37602202540
Security run: https://github.com/Glassing-Wind/rest_proxy/actions/runs/37602202462

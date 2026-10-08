# Five-priority completion status — October 6, 2026

## Isolated helper trial blocked by output budget — October 7

Literal five-field extraction fixture ran separately from live tasks. json_object
request returned HTTP 400; body not retained, exact rejection reason unverified.
One explicit json_schema request completed HTTP 200 in 10.543 s but finish_reason
length and empty final output. Provider reported 302 prompt/2048 completion tokens,
all completion tokens attributed to reasoning. No extractable result or supervisor
semantic verification possible. No further retries in this milestone.
[Dated attempt evidence](../benchmarks/reports/2026-10-07/qwen-helper-extraction-trial.json).

Three contract regressions pass. All eight hosted checks for e1c5f36 passed. No live
task mutation or model/service lifecycle changes. No conclusion about general helper
suitability or paid-token savings: direct baseline and verification usage unmeasured.
Next investigate bounded per-request reasoning controls supported by this exact model/
LM Studio runtime before changing settings or increasing budgets blindly. Continue
independent gateway journal work while inference compatibility remains unresolved.


## Bounded helper extraction contract — October 7

Helper output validation covers five allowlisted fields with unique record/unresolved
coverage, 8 KiB source and 4 KiB result budgets, bounded values and exact quote on
cited source line. Validation returns detached output explicitly marked for supervisor
verification; an incorrect inferred value with a genuine quote can still pass.
Three offline tests verify forgery/range/coverage rejection, UTF-8 limits and that
quote matching does not confer semantic approval. Focused lint passes. No model calls,
live task changes or token-savings evidence. Next isolated Qwen extraction fixture;
paid/direct comparison and supervisor verification must include correction overhead.
All eight hosted checks for 5c735c6 passed.


## Qwen helper role corrected — October 7

User clarified local Qwen is for simple work and possible paid-token reduction,
not supervision. [Helper direction](helper-agent-direction.md) assigns supervision
to Codex/user and execution/validation to deterministic tools. Historical reviewer
trials are not helper-quality evidence. Next isolated field-extraction trial with
exact source quotes, supervisor verification and controlled paired accounting.
No savings, automatic-team, framework installation or live-task retry claim.
Gateway journal implementation remains independently open. All eight hosted checks
for 41bdc09 passed. This milestone changes documentation only.


## Full gateway study and first contract — October 7

Full supplied study preserved at [research/rest-proxy-architecture-study.md](research/rest-proxy-architecture-study.md)
and reconciled with current branch. Internal v1 capture contract now validates exact
fields, event types, bounded identifiers, timestamps, canonical payload hash and UTF-8
size caps (8 KiB payload/10 KiB event). Two offline checks pass with focused lint.
Contract does not authenticate scope, redact payload, persist events or forward traffic;
no CloudEvents compliance claimed. Hash establishes identity, not truth/authenticity.
Next private SQLite journal with transactional append, duplicate-ID conflict handling,
scoped reads and subprocess crash/reopen proof, before routing. Existing inference
routes remain untouched. No framework installation or licensing changes.


## Durable gateway direction reconciled — October 7

User-provided study summary introduces configured forwarding, a separate durable
event journal, idempotent evidence projection and scoped retrieval. Local HEAD has
newer task recovery/failure reporting, but no demonstrated traffic capture pipeline.
[Concrete prototype decisions](durable-knowledge-gateway.md) preserve existing
inference behavior and exclude external-action replay. Full study file is missing;
source review remains pending. No code/runtime/license change in this slice.
Next gateway proof: offline private journal plus crash/reopen and idempotent local
projection tests, before a single controlled HTTP route. Existing task recovery and
paired investigation evaluations remain open; coordinate cloud ownership before
editing overlapping files. Do not replace revenue validation with platform scope.


## Returned-finding rejection history — October 7

Worker now best-effort records finding_rejected at returned_finding stage when
result validation, freshness checking or submission fails. Generation errors remain
separate. This category identifies the pipeline stage, not a semantic judgment or
proof the provider is at fault; persistence failures can also prevent submission.
Raw returned content/exception messages are excluded. Claims/checkpoints stay intact;
stale revision/cancel/reclaim prevents history overwrite, and no retry is triggered.

30 checks pass (worker 15, registry 12, inspection 2, CLI 1), plus focused lint.
New fixtures verify bad citation rejection and source changes before submit, without
persisting rejected model text or creating submissions. All eight hosted checks for
f97a54f passed. No live task or inference calls. Next make an explicit recovery
preview that shows revision, expiry and source ranges before another bounded run;
do not repeatedly retry the unchanged 20-second provider timeout.
[Dated evidence](../benchmarks/reports/2026-10-07/returned-finding-failures.md).


## Sanitized task inspection — October 7

Local task API now exposes inspect: revision/status/attempts, claim presence/expiry,
checkpoint presence, recovery/submission/review/assessment counts and bounded failure
metadata. Goal, workspace, bearer token, worker label, source and free-text history
are excluded. Inspection performs no mutation or provider calls. Empty failure list
explicitly does not imply success: older and uninstrumented failure paths are missing.

Two inspection checks, twelve registry and two handoff regressions pass (16), plus
focused lint. CLI process acceptance verifies safe output and unchanged revision.
All eight hosted checks for f5c29b5 passed. Live Siri task not modified/retried.
Next distinguish post-generation validation failures in durable history; generic Siri
status/cancel UI and automatic dispatch remain open.
[Dated evidence](../benchmarks/reports/2026-10-07/task-inspection.md).


## Durable generation failure metadata — October 7

One-shot worker now best-effort stores timeout/generation_error with attempt number,
stage, observed revision and unknown remote termination. Raw error text, model output,
claim tokens and usage guesses are excluded. Recording preserves claimed status and
checkpoint; cancellation/reclaim/stale revision rejects recording instead of overwriting
newer state. Current claim may report after lease expiry, but cannot perform actions.
At most five records fit within the existing transactional 64 KiB task cap.

26 worker/registry/CLI checks and focused lint pass. New fixtures verify reopen with
sanitized timeout and cancellation during generation without stale writes. Existing
live timeout is not retroactively invented as a worker record; dated evidence remains.
No new model calls or live task changes. All eight hosted checks for 77bdd1c passed.
Next expose sanitized failure summaries to task inspection and distinguish validation
failures after generation from transport/generation errors; retries remain explicit.
[Dated evidence](../benchmarks/reports/2026-10-07/task-generation-failures.md).


## Live expired-claim recovery outcome — October 7

One explicitly scoped recovery of Siri task 1214c5b2e644470197b691e7e8e849aa
replaced expired revision-8 claim and reread three smaller source ranges. Provider
ReadTimeout at the configured 20-second boundary returned no finding/usage receipt.
Reopen confirms revision 10, claimed status, attempt 3; old three-source checkpoint
archived, fresh checkpoint retained, original submission, review and assessment intact.
[Dated sanitized evidence](../benchmarks/reports/2026-10-07/siri-expired-recovery-timeout.json).
No immediate retry or model lifecycle action. Timeout does not attest server stop.

24 worker/registry/CLI regressions pass; all eight hosted checks for 10564e9 passed.
Next add durable bounded failure metadata so timeout/rejection is visible in task
history without reading private CLI files. Repeated provider timeouts block successful
live correction; continue independent failure-reporting work before further calls.
No automatic approval, completion or quality/token-savings claims.


## Explicit expired-claim worker recovery — October 7

One-shot dispatch accepts --recover-expired REASON alongside --execute. Recovery
uses revision-checked registry reclaim, rejects active/cancelled claims and retains
the five-attempt cap. Old checkpoint is archived with claim history before fresh
source is read and a new checkpoint created. Earlier submissions/reviews remain.
There is no polling or automatic retry; a failed call leaves the new claim for
explicit handling. Cancel does not stop an already running external generation.

Eleven worker, twelve registry and one CLI checks pass (24), plus focused Ruff.
New fixtures verify expired recovery with changed source and historical checkpoint,
and active-lease rejection without inference or mutation. Live Siri task was not
reclaimed or retried. Next use an explicitly reviewed recovery command with smaller
source ranges, then preserve outcome; provider timeout is not proof generation stopped.
[Dated evidence](../benchmarks/reports/2026-10-07/expired-worker-recovery.md).


## Revised local advisory trials — October 7

Both v2 synthetic trials completed, preserving raw outputs and provider-reported usage.
Full source: 17.718 s, 327 prompt/3356 completion tokens. Missing definition: 16.138 s,
282/3289 tokens. Both separated supplied read_source from execution guarantees and
identified the raw-byte error, but still accepted the task-type claim (full response
conflated task/project; missing-definition response guessed type). Full response did
not cite source for its Qwen absence judgment. Manual supervision remains required.
[Dated responses and qualitative grading](../benchmarks/reports/2026-10-07/local-advisory-calibration-v2.json).

These small synthetic trials do not establish accuracy gains, savings or commercial
readiness. Seven calibration/export tests pass; all eight CI checks for f441773 passed.
No live task or service/model lifecycle changes. Next prioritize explicit expired-claim
recovery for the timed-out Siri task, with retained partial evidence and bounded retries;
further prompt tuning must not substitute for the shared-task recovery milestone.


## Calibration source-line inspection — October 7

A bounded fixture citation resolver now returns the exact numbered source behind a
citation and rejects absent paths/lines, ambiguous sources and oversized ranges.
An existing wrong line still resolves: output explicitly labels range existence as
insufficient for semantic support. Tests reproduce line 4 versus line 5 from the
observed Qwen response. No automatic approval or semantic scorer was added.

Version-2 synthetic fixture separates passing read_source from guaranteeing its
execution, and raw-byte validation from reading. Historical v1 trials remain intact;
v2 has not been run against a model. Four calibration and three export tests plus
focused lint pass. All eight CI checks for preceding commit 7ebc8e4 passed.
Next run v2 variants and inspect each cited excerpt alongside its judgment.
[Dated evidence](../benchmarks/reports/2026-10-07/calibration-source-lines.md).


## Local advisory trials — October 7

Both synthetic variants completed against already available Qwen via numeric-loopback
LM Studio, without lifecycle or task changes. Full source: 17.404 s, provider-reported
317 prompt/3623 completion tokens. Missing definition: 12.420 s, 272/2359 tokens.
Both questioned byte/character and Qwen-invocation claims, but falsely accepted task
type rest_proxy; full response also cited intake line 4 instead of call line 5.
[Dated responses and grading](../benchmarks/reports/2026-10-07/local-advisory-calibration.json).
These are two qualitative fixtures, not general accuracy or savings evidence.
The read_source claim is ambiguous; revise it to distinguish supplied action from
implementation guarantee before scoring further. Automatic approval remains unsuitable.
Next broaden controlled fixtures and source-line checks, preserving manual supervision.
Five prompt/export regression tests pass. All eight hosted checks for 4cf303f passed.


## Calibration prompt isolation — October 7

A local CLI now emits full-source or missing-definition synthetic prompts using the
same review instructions as real exports. It whitelists only instructions, finding
and source; grading answers/scoring metadata never enter model input. Two isolation/
budget tests plus five export/capture regressions and focused lint pass. CLI smoke
verified the missing-definition variant. No model call, task mutation or quality
measurement performed. Next manually run both prompts and record claim judgments.
[Dated procedure](../benchmarks/reports/2026-10-07/advisory-calibration-prompts.md).


## Claim-level advisory prompt — October 7

Review exports now explicitly request supported, unsupported/unresolved claims and
corrections with source path/line evidence. Missing definitions must remain unresolved;
byte limits, decoded character limits and repository-wide absence are distinguished.
Seven export/capture/assessment tests and focused Ruff pass. These are contract checks,
not measured model accuracy. [Controlled fixture](../benchmarks/reports/2026-10-07/advisory-review-calibration-fixture.json)
contains four judgments and a missing-definition variant; no model trial run yet.
Next run both variants and manually grade omissions/false positives before considering
supervisor automation. Existing exported bundles use the older prompt and now reject
capture against freshly generated exports; prepare a new export before any new review.
The fixed installed Shortcut has not been updated or rerun. Live task remains claimed
at revision 8; no model lifecycle, retry, approval or inference-provider changes.


## Real Apple advisory response captured — October 7

The user supplied the Cloud result screenshot and confirmed Done. Its visible text
was transcribed and imported against the exact revision-7/submission-1 export.
Reopen verifies advisory retention at revision 8; status remains claimed, with the
original submission and correction review preserved. Four capture/assessment tests
pass. [Dated evidence](../benchmarks/reports/2026-10-07/apple-live-review.json).

This review failed the intended semantic check: it summarized the finding and
repeated `rest_proxy` as a task type. TaskRegistry.create defines it as project scope.
It also conflated a 4096-character goal limit with stdin reading (16384-byte cap).
Do not treat the result as approval or a calibrated supervisor. Model identity and
usage remain unverified. Next: explicit review formatting with caller-supplied
source contracts, then measure error detection on controlled fixtures before any
automatic task acceptance. The Shortcut still has a fixed historical bundle;
generic input/capture wiring and expired-claim recovery remain open.


## Live Apple review handoff — October 7

The user authorized the previewed FIRE task/finding/source bundle to Apple Cloud
and future reviews within that same workflow. No repeated approval is needed for
that scope; new destinations or materially different sensitive data need separate
consideration. The exact revision-7, submission-1 bundle replaced the synthetic
shortcut prompt and the run reached Show Content. The result dialog is outside the
available automation surface; output text and local import remain unverified.
Do not repeat the cloud call or invent an assessment. User dismissal of the result
can allow retained output inspection. Task status has not been changed by this run.
Hosted CI for code commit 6c0d8fd passed all eight checks. This establishes check
results, not investigation quality or an automatically invoked supervisor.


## Exact-bundle advisory capture — October 7

Local capture helper validates exported bundle against current revision/submission/
finding before storing advice. Two new tests verify reopen, replay/edited-bundle and
post-cancellation rejection. Assessment/export regressions (four tests) and focused
Ruff pass. No task approval/status change. Local 3,175-byte preview prepared for
Siri task revision 7, original submission 1, not transmitted to Cloud.
[Handoff instructions](shortcuts/fire-review-handoff.md). GUI model input/capture
wiring remains pending; the existing shortcut still runs its synthetic prompt.
Next user choice of this exact evidence bundle/destination before live cloud review.
This is not a persistent supervisor or verified model identity.


## Advisory assessment retention — October 7

TaskRegistry.record_assessment and the local JSON operation now retain an 8 KiB
maximum advisory text against an existing numbered submission and expected task
revision. Status, original finding and review decisions stay unchanged. Identity is
caller-supplied/unverified; imported text is untrusted advice, never authorization.
At most ten assessments per task; existing 64 KiB task cap still applies.

Two assessment fixtures, twelve registry, two review-export and two process-handoff
checks pass (18 focused tests), plus focused lint. Reopen preserves advisory linkage;
stale revision, invalid submission and empty/oversized text reject without mutation.
No real task assessment imported; Apple shortcut output is not automatically wired.
Next previewed bundle input and assessment capture through Shortcuts, then explicit
user review. No private cloud transmission or service/model lifecycle change.

GitHub rejected normal pushes of prior local commit 175db4c with Internal Server
Error twice. Its code remains local; last confirmed hosted revision 0646c5e passed
all eight checks. Current sync result must be checked before claiming publication.


## Apple Intelligence advisory review prototype — October 7

FIRE Review Evidence created with Use Model (Cloud) -> Show Content. Synthetic
source `def total(values): return sum(values)` and deliberately false positive-integer
validation claim produced the expected supported/unsupported/correction assessment.
[Receipt](../benchmarks/reports/2026-10-07/apple-review-prototype.json). Observed UI
model label is Cloud; actual underlying model identity and usage are unexposed.
Do not label this verified Gemini inference or Siri voice review acceptance. No
private repository content sent and no task status changed. This is one easy case,
not calibrated reviewer reliability or a persistent supervisor.

`python -m scripts.task_review_export --state PRIVATE_DIR --project PROJECT
--task-id ID --submission N` exports a bounded 8 KiB historical finding bundle with
source evidence, task/revision/submission linkage and advisory instructions. Workspace
and bearer claim metadata omitted; source/answer may still contain private content.
Two offline tests verify metadata exclusion, evidence retention, invalid submission
and oversized-bundle rejection; focused Ruff passes. Export does not transmit data.

Official [Apple Shortcuts documentation](https://support.apple.com/guide/shortcuts-mac/use-apple-intelligence-in-shortcuts-mchl91750563/mac)
supports on-device/Cloud models and action outputs. Broader Siri collaboration with
Google does not attest which underlying model this action selected. No Claude
subscription/integration is needed for this prototype. Next wire previewed explicit
bundle input and bounded assessment capture tied to submission identity, before
separate user approval or task review mutation. Real-task cloud sharing requires
specific destination/data authorization; do not export private content automatically.
Prior revision 0646c5e passed all eight hosted checks; new revision requires new CI.


## Correction feedback and live timeout — October 7

Workers now include the latest request_correction review reason in the same bounded
prompt. New fixture confirms feedback reaches the generator and two submissions
survive retry. Nine worker, twelve provider, nine dispatcher and one CLI tests pass
(31 focused checks), focused lint/diff pass.

One explicitly authorized three-source attempt on Siri task
1214c5b2e644470197b691e7e8e849aa hit the adapter's 20-second ReadTimeout. Registry
reopen confirms claimed revision 7, attempt 2, three checkpoint sources, original
submission and prior review retained. No corrected finding/usage counters, automatic
retry or model lifecycle change. [Evidence](../benchmarks/reports/2026-10-07/siri-correction-timeout.json).
Timeout does not prove generation stopped server-side; do not overlap retries.
Next add bounded explicit request-timeout configuration and safe expired-claim
resume dispatch, then decide one controlled retry. Automatic supervisor and
background scheduling remain open; the original task is not complete.


## Bounded multi-source worker — October 7

[Acceptance](../benchmarks/reports/2026-10-07/multi-source-worker.md): worker accepts
up to three explicit guarded source ranges, requires exact ordered citations for
all ranges, retains all evidence/checkpoints and keeps the 8 KiB whole-prompt cap.
Provider schema and CLI now support this contract. Thirty focused checks pass,
including correction/source-deletion/reopen retention and missing-citation rejection.
This is fixture acceptance; live multi-file model correction remains next. Siri task
stays queued revision 5, with original partial finding/review retained. No automatic
retry, background worker or supervisor adapter started. Overall priority gates remain.


## Siri task -> Qwen -> explicit Codex review — October 7

An explicit one-task CLI (`python -m scripts.task_run`) now requires --execute,
state/project/task/revision, source range and loopback endpoint/model. It calls the
existing worker once, without polling, retries, lifecycle calls or automatic review.
Missing --execute rejects before state creation; one CLI test plus five worker and
twelve provider tests pass, focused Ruff/diff checks pass.

User-authorized Siri task 1214c5b2e644470197b691e7e8e849aa was supplied only
scripts/task_capture.py lines 1–33. Already-loaded Qwen produced a source-validated
finding, review_pending revision 4, reporting 866 prompt/2414 completion tokens.
Manual Codex review requested correction: project scope was called a type, broader
coordination question lacked source coverage, and limits were keywords. Registry
reopen confirms queued revision 5 with original finding and review retained.
[Evidence](../benchmarks/reports/2026-10-07/siri-worker-review.json).
This is one real supervised handoff, not a persistent background team or automatic
Codex adapter. No automatic retry occurs. Prior cancelled Siri task stays cancelled.

Queueing uses TaskRegistry.create and private SQLite; run_worker claims a revision,
reads bounded source, checkpoints, generates and submits for review. Full team needs
multi-file evidence selection, user request preview/correction, scoped task access,
a supported supervisor adapter, bounded dispatch scheduling and failure/cancellation
handling. Next implement multi-source worker evidence without loosening citation
checks; persistent scheduling stays opt-in and requires explicit lifecycle approval.
No quality/token-savings claim, and five overall priority gates remain open.


## Siri intake accepted — October 7

User screenshots establish Siri invocation, request dictation and queued confirmation.
Reopening task 852f3af340d8442f8b04930bcfccc75d confirms queued revision 1, read_source
only, zero attempts and no claim; saved goal matches the visible transcript.
[Receipt](../benchmarks/reports/2026-10-07/siri-task-intake.json).
GUI and Siri local intake are now evidenced. Dictation misrecognized Qwen as Quin
and Codex as Kodak Kodak (also changed the opening request). Accurate intent capture
is not established. Next add request preview/confirmation with edit or cancellation
before dispatch; retain original transcript separately from user-confirmed corrections.
No automatic agent invocation occurred. Authenticated/scoped dispatch and semantic
review remain open; do not silently rewrite voice requests from guessed intent.


## Shortcut GUI intake accepted — October 7

User screenshot confirms queued task 37f060b4b4d542798757f48cbf72f7da. Reopening
private local registry confirms rest_proxy scope, queued revision 1, read_source
only, zero attempts and no claim. No agent invoked. Request text is excluded from
published evidence. [Receipt](../benchmarks/reports/2026-10-07/shortcut-task-intake.json).
GUI task intake and durable reopen are now verified; Siri voice invocation remains
untested. Next scoped worker/supervisor dispatch with explicit review. This does
not establish a persistent team or authenticated remote task interface.


## Shortcut wiring checkpoint — October 7

User enabled scripting and FIRE Capture Task is wired Ask for Input -> local
stdin capture -> Show Content, administrator execution off. Test is waiting at
runner input not exposed to app automation; GUI/voice and persisted-task acceptance
remain pending user test input. Prior revision 4479d4c passed all eight hosted checks.
See [integration research](agent-integration-research.md) for current limitations.


## Participant interfaces and learning reuse — October 7

[Integration research](agent-integration-research.md) records supported Siri,
Claude/Codex interfaces, framework choices and source-reviewed reuse candidates
from RepoAnalyzer/GithubAnalyzer. Mac task intake helper passes two offline tests;
FIRE Capture Task draft exists but script actions are disabled, so GUI/voice
acceptance remains blocked pending the user's security-setting decision. No setting
or participant configuration changed. Muse product identity remains unconfirmed.
PR4 revision c6509dd passed all eight hosted checks. Next authenticated task adapters
and supervised claim/evidence review; document AST extraction stays a separate slice.


## Explicit output budget and live handoff — October 7

The optional adapter now accepts a bounded explicit 256–4096-token budget, default
1024. Twelve provider and five worker tests pass. One synthetic trial at 4096
reached review_pending with source-validated evidence and registry reopen equality
(449 prompt / 2194 completion tokens, provider-reported; 10.669 seconds).
[Evidence](../benchmarks/reports/2026-10-07/task-output-budget.json).
Semantic review remains pending; this is no quality or savings comparison. Next
extend disposable live acceptance through explicit review and source-deletion
recovery. Authentication, cross-chat coordination and overall priority gates remain.


## Bounded rejected-attempt diagnostics — October 7

The optional adapter now separates allowlisted attempt metadata from accepted
findings. Eleven provider tests and five worker tests pass. A synthetic Qwen trial
reported `length` at the configured 1,024-token completion limit (449 prompt tokens);
no finding accepted, checkpoint preserved before disposable fixture cleanup.
[Evidence](../benchmarks/reports/2026-10-07/task-attempt-metadata.json).
Attempt metadata is caller-held, not durable task history. Next add an explicit
bounded output-budget option and test it without relaxing stop/citation validation.
No quality or savings claim; all five overall priorities retain their open gates.


This is the current summary; dated reports contain the supporting evidence.
Historical roadmap checkpoints describe their own dates and are not current
instructions. "Implemented" below means the named slice has evidence, not that
the whole priority or enterprise release is complete.

| Priority | Current status | Completed and evidenced | Remaining acceptance |
| --- | --- | --- | --- |
| 1. Reliable indexing | Core local recovery and golden-resolution slice complete; broader operations remain. | Tracked shadow ownership/heartbeat, guarded cleanup/adjudication, cancellation identity checks, publication-aware reconciliation, SIGTERM/SIGKILL preservation. Source-reviewed parser fix yields 78 correct route links versus the faulty baseline's 80; historical 81st edge cannot be reconstructed. | Keep uncertain/remote writers protected; extend operational recovery coverage before claiming coordinated multi-host recovery. Golden changes still require source review. |
| 2. Embedded storage | Substantial functional implementation; full application cutover incomplete. | Ladybug transaction adapter, owned graph/vector publication, LanceDB text/vector/hybrid reads and deletion, source/parser facts/static links, standard related-file, static symbol-reference, declared-import and backend route overview bridges, conservative Python function-import bindings, opt-in shared-owner REST evidence reads and stateless LM Studio bundle forwarding, project discovery, annotations, latest-attempt journal, IDE registration/lease renewal, opt-in watcher dispatch and guarded setup. Real 131-file/1,629-chunk indexing and MCP reads with external storage networking denied. | Broader import binding coverage and general flow query bridges, broader route/reference resolution, deployed inference-proxy acceptance and provider continuation, chosen IDE adapter deployment/legacy registry migration, new-file watch enrollment, production model/runtime identity. Latest-attempt recovery does not provide job history or automatic resume. |
| 3. FIRE continuity | Contracts/provenance and explicit local durable recovery implemented; client/operational continuity incomplete. | Versioned EvidenceReference/TaskCheckpoint contracts and structured retrieved evidence retained through ranking/assembly; offline scope/provenance/deduplication/outage checks. Scoped SQLite checkpoints/originals, explicit MCP resume/corrections, retention expiry/purge/deletion, revision protection and process-reopen recovery after removing fixture conversation/source; cold-copy restore preserves corrections, originals, tombstones and expiry. | Client checkpoint cadence/compaction integration, automatic current-source validation, history inspection, operational restore drills and controlled outcome evaluation. Historical evidence is not current-code proof. |
| 4. Bounded context assembly | Shared packing and stateless LM Studio forwarding implemented; exact provider budgeting remains incomplete. | Compact tool catalog, bounded numbered source, structured provenance and existing selection/deduplication. Compact schema pilot measured 77.54% fewer schema tokens. Deterministic cited evidence packing, canonical full supplied-request accounting with reserves, local tokenizer/byte-estimate modes, scoped FIRE snapshot-to-bundle adaptation, real MCP/REST parity and formatted LM Studio forwarding with observed usage (807 prompt/15 completion tokens in one fixture). | Provider serialization/model-tokenizer-framing attestation, automatic retrieval/source freshness and broader inference acceptance; real provider continuation/history deduplication and controlled usage measurements. Canonical-payload accounting is not verified provider usage. |
| 5. Release and outcome validation | Runtime/parser validation and pilots complete; release/outcome gates incomplete. | Project Python 3.14 cutover/rollback, published modified ts-pack pin, native fork-wheel validation, isolated installs and local CI receipts; wheel/source-archive worker inclusion, core/embedded/full dependency separation and installed native fixture/daemon REST acceptance; schema-valid incomplete installed-package SBOM, shipped notice collection, source-bound supplemental namespace notices, ten observed grammar asset/notice bindings and cold graph/vector/FIRE restore fixture. Prior paired pilot and source-based grading exist; they establish no MCP superiority. | Fresh exact-pin source-build and supported-platform install matrix, exact artifact/dependency/grammar/model notices and SBOM, operational backup retention/deletion reconciliation and restore validation, repeated controlled coding investigations with actual usage/latency/resource/fallback measurements. Qwen worker trial is one functional case, not a paired savings evaluation. |



## Optional local-provider fixture — October 7

[Loopback adapter receipt](../benchmarks/reports/2026-10-07/task-provider-adapter.md)
now verifies a constrained one-request chat adapter through the worker to retained
findings/review_pending. Three HTTP fixture tests pass; no real model contacted.
Exact returned-model identity and malformed/tool-call responses now have rejection
fixtures (five provider tests). Sanitized generation provenance now persists separately from findings, and missing/
invalid counters plus HTTP timeout fixtures pass (41 focused tests total).
An opt-in synthetic trial against already-loaded Qwen Splash returned HTTP 400;
checkpoint retained, no successful inference/usage. Bounded diagnostics now confirm
json_object is rejected: this endpoint requires json_schema or text. Explicit
JSON schema now passes HTTP fixture checks; one live retry failed local validation
(finish_reason stage) with checkpoint retained. Stable stage diagnostics now pass
fixtures; actual non-stop reason not recorded. Next retain allowlisted completion
metadata and rejected-attempt usage before deciding bounded budget adjustments; no model/service lifecycle changes are authorized. Authentication and cross-chat coordination remain open.


## Structured finding contract — October 7 acceptance

[Source-bound findings](../benchmarks/reports/2026-10-07/task-finding-contract.md)
now validate bounded schema/citations against guarded source reads and retain numbered
evidence through submission. Twenty-nine focused tests pass. Legacy raw submit stays
unvalidated; source identity checks do not establish semantic correctness or later
freshness. Structured submission now passes a nine-process handoff including reopen after
source deletion; original evidence survives both acceptance and correction cases.
An [optional one-shot worker boundary](../benchmarks/reports/2026-10-07/task-worker-boundary.md)
now passes injected-generator acceptance (34 focused tests total), disabled by
default with explicit review. No actual model has been connected. Next implement
a constrained optional loopback provider adapter and HTTP fixture acceptance. Authentication and cross-chat coordination remain open.


## Local registry foundation — October 6 acceptance

[Registry receipt](../benchmarks/reports/2026-10-06/shared-task-registry.md): scoped
SQLite create/read/claim/checkpoint/reopen and revision/expiry guards now pass twelve
offline tests, including cancellation, expired read-only reclaim and retained
finding/correction review/completion transitions. This is a trusted local library, not authenticated task endpoints or
worker execution. Original findings and review decisions survive corrections and completion.
Distinct reviewer IDs are required but are not authenticated identities. A local JSON task interface and guarded bounded read-source dispatch now pass
six additional tests (24 focused tests including FIRE regression). A deterministic subprocess handoff now passes both unchanged-source completion
and changed-source correction cases (26 focused tests total). Each of eight
operations reopens state in a separate process; two scripted roles are not independent
reviewers. Next define a bounded structured finding/evidence contract before
optional model workers. Authenticated MCP/REST
adapters, model workers and broader permissions remain open. No persistent agent
team or cross-chat synchronization is running.


## Shared task system — Priority 3 implementation direction

The [shared task system](shared-task-system.md) is primarily FIRE continuity,
with Priority 2 persistence/tool interfaces, Priority 4 worker bundles and Priority 5
restart/permission/outcome validation. Next is a scoped local task registry with
claim/revision, checkpoint, reclaim, cancellation and review fixtures. It is not
implemented yet; existing FIRE checkpoints and rental receipts are foundations.
The user started a separate rental conversation; rental journeys/fixes belong there,
and rest_proxy owns shared coordination. Separate chats do not automatically sync.

## Commercial sequencing — October 6

[Agent-team user testing](agent-team-user-testing.md) now has its first local
rental-applicant browser demonstration: one reproduced submission blocker, a narrow
working-tree fix and 13 passing focused tests. Backend fixture responses do not
establish database/payment/email acceptance. Next: a disposable full journey and
structured finding/review handoff; no persistent multi-agent runner is implemented.
A [filled, source-bound report](../benchmarks/reports/2026-10-06/rental-user-qa/investigation-report.md)
now answers three questions with four retained snapshots and a machine-readable
handoff. Four offline integrity checks pass; independent review remains open.
Full database journey awaits an explicitly isolated disposable test configuration.


The user's income requirement adds a customer-validation track; it does not change
which technical acceptance gates have passed. Next prepare a source-graded pilot
report and delivery template using authorized source, then validate the
[draft investigation offer](paid-investigation-pilot.md) with user-selected buyers.
[Commercial direction](commercial-direction.md) records pricing as a hypothesis and
keeps outreach, customer data and payment actions subject to explicit authorization.
No paying customers, guaranteed earnings or measured tool superiority are claimed.

## Evidence by priority

1. [Reliable indexing report](../benchmarks/reports/2026-10-04/reliable-indexing.md)
   and [operator recovery procedure](indexing-lifecycle-safety.md).
2. [Real embeddings acceptance](real-embedding-acceptance.md),
   [shared MCP owner](embedded-mcp-owner.md),
   [symbol/call evidence](embedded-static-relationships.md),
   [standard related-file bridge](embedded-related-files.md),
   [declared-import overview bridge](embedded-import-overview.md),
   [static symbol-reference bridge](embedded-symbol-references.md),
   [backend route overview](embedded-route-overview.md),
   [Python function-import bindings](embedded-symbol-imports.md),
   [shared-owner REST evidence](embedded-rest-evidence.md),
   [metadata](embedded-project-metadata.md), [journal](embedded-indexing-journal.md),
   [IDE leases and watcher dispatch](embedded-refresh-and-watching.md),
   [guarded watch setup receipt](../benchmarks/reports/2026-10-05/embedded-watch-setup.json).
3. [FIRE implementation and acceptance](fire-platform.md),
   [explicit durable continuity](fire-durable-continuity.md).
4. [Context contract and budgeting direction](context-platform-direction.md),
   [shared supplied-request bundle contract](context-bundle-contract.md),
   [FIRE snapshot-to-bundle adapter](fire-context-bundles.md),
   [LM Studio forwarding acceptance](context-provider-forwarding.md),
   [measured schema/outcome pilot](../benchmarks/reports/2026-10-03/README.md).
5. [Runtime setup](python314-runtime.md), [fork upgrade](ts-pack-upgrade.md),
   [integrated release requirements](integrated-platform-plan.md),
   [distribution layout acceptance](distribution-acceptance.md),
   [minimal embedded installed acceptance](minimal-embedded-install.md),
   [release inventory and cold restore](release-inventory-and-restore.md),
   [namespace supplemental notice reconciliation](namespace-notice-reconciliation.md),
   [observed grammar notice reconciliation](grammar-notice-reconciliation.md),
   [dependency repair receipt](../benchmarks/reports/2026-10-05/dependency-refresh.md).

## Work order from this checkpoint

The October 6 shared-task direction supersedes the historical work order below:
implement the minimum Priority 3 task registry and guarded restart/handoff fixture
here; continue rental application acceptance in its own conversation. Keep the
five-priority completion table's open release gates and measured-outcome requirements.

The [bounded Qwen investigation/patch/review trial](../benchmarks/reports/2026-10-05/qwen-worker-trial.md)
is complete, including a rejected truncated attempt, supervisor corrections and
an implemented optional embedding-authentication patch. It proves one functional
local-worker case; proxy-route integration and paired savings remain unverified.
Resume Priority 2 with the missing embedded query bridges and
explicit owner routing; choose and exercise one real IDE adapter before calling its
integration deployed. Keep Priority 3 checkpoint/original-evidence persistence and
Priority 4 bundle/budget contracts as the next coordinated implementation slices.
Priority 5 packaging and paired outcome measurements remain release gates.

Subsequent Priority 2 slice: [embedded related files](embedded-related-files.md)
now bridges the standard tool to cited incoming/outgoing import, call and route
file relationships. Native reopen/reindex/deletion, bounded output and real
STDIO/HTTP parity pass. Symbol-reference/import summaries and full graph parity
remain open; this file-level view does not supply them.

The [declared-import overview](embedded-import-overview.md) now bridges the standard
summary tool to verified parser observations with citation samples and explicit
scan/output limits. This completes the declared-import summary slice, not resolved
symbol-import edges or implicit usage. Symbol references, resolved bindings, route
summaries and remaining owner integrations remain Priority 2 work.

The [CLARITY/personal cockpit direction](clarity-cockpit-direction.md) is exploratory
and separate from repository tooling delivery. No audio, wearable capture, citizen
publication network or automatic cloud supervisor is implemented by these documents.

The [static symbol-reference bridge](embedded-symbol-references.md) now exposes
verified incoming call candidates through the standard tool, including workspace
deduplication, ambiguity and continuation. Native persistence and real transport
parity pass. Exhaustive references, resolved imports and route summaries remain open.

The [backend route overview](embedded-route-overview.md) now supplies cited native
route declarations through the explicit backend summary tool. Hash/budget checks,
native snapshot/reindex/deletion and real transport parity pass. Broad framework
coverage, service/database hops and general auto/UI flow dispatch remain open.

The [Python function-import binding slice](embedded-symbol-imports.md) now publishes
separate cited symbol-import candidates and adds a bounded page to the import
overview. Old indexes request reindexing for this kind. Native persistence/paging,
conservative resolution checks and transport parity pass; full-language binding
coverage and remaining ownership/flow integrations remain Priority 2 work.

[Shared-owner REST evidence reads](embedded-rest-evidence.md) now reuse the HTTP
daemon’s registered bounded tools without a second database owner. Flag/backend
gating, contract limits and real MCP/REST source parity pass. This completes the
REST read-surface slice, not inference-time bundle assembly or Priority 4 budgeting.
The scoped durable FIRE checkpoint/evidence store is the next coordinated milestone.

The [explicit durable FIRE milestone](fire-durable-continuity.md) now recovers scoped
task state and supporting originals after removing the fixture conversation/source.
Corrections, expiry/purge and deletion preserve current-state boundaries. Real
STDIO-to-fresh-HTTP recovery and local CI pass. Priority 3 remains partial at the
client/operational boundary. Next: shared bundle contracts and whole-request budgeting.

The [shared supplied-request bundle contract](context-bundle-contract.md) now packs
cited candidates against canonical request accounting and explicit reserves, with
local-tokenizer support, deterministic omissions and real MCP/REST parity. Priority 4
remains incomplete until provider formatting/usage and inference integration are
validated. Next: FIRE checkpoint-to-bundle adaptation, then one explicit provider adapter.

The [FIRE snapshot-to-bundle adapter](fire-context-bundles.md) now adds current
scoped checkpoint state and hashed originals as historical candidates, with explicit
missing/expired/stale/oversized diagnostics and corrected-state boundaries. Native
store/builder checks and real fresh-process MCP/REST parity pass. Next: one provider
request adapter with actual usage and continuation handling.

The [stateless LM Studio forwarding slice](context-provider-forwarding.md) now
connects the inference proxy to owner-built bundles and reports actual usage. Its
resident-model guard and serialized request budget pass local contracts and one
real Qwen FIRE fixture. Next: controlled coding tasks and minimal packaging; exact
chat-template attestation and provider continuation remain explicit gates.

[Distribution layout acceptance](distribution-acceptance.md) repairs missing packaged
indexing workers and verifies wheel/source-archive contents plus isolated stdlib
continuity imports. Full local CI passes. The minimal embedded dependency profile,
installed native indexing, state-path validation and paired outcomes remain open.

The [minimal embedded profile](minimal-embedded-install.md) now separates core and
native/full extras, packages `ts_diagnostics`, and supplies an explicit writable
runtime override. A fresh 48-package environment passes dependency checks, native
fixture indexing/vector search and fresh installed-daemon REST source/text reads.
Next release gates are exact artifact notices/SBOM and restore, plus exact-pin
source builds/platform coverage and controlled paired outcomes.

[Release inventory and cold restore](release-inventory-and-restore.md) now identifies
48 installed packages, copies 72 shipped notices, records four metadata/notice gaps
and hashes observed grammar assets. Cold native graph/vector/FIRE recovery passes
after removing live fixture state/source. SBOM composition is explicitly incomplete;
native/grammar/model notice reconciliation and operational backup policy remain gates.

[Namespace notice reconciliation](namespace-notice-reconciliation.md) now binds all
169 package files to exact upstream release-tree blobs and collects its root license
as supplemental evidence for two namespace packages. Upstream missing-file findings
remain visible; native/grammar/model closure and two SPDX metadata gaps remain open.

[Observed grammar reconciliation](grammar-notice-reconciliation.md) verifies ten
cached macOS libraries against their release bundle and collects nine notices at
declared source revisions. A separate incomplete grammar SBOM passes schema checks.
Other 361 bundle files, reproducible binary source builds and native closure remain
open; this does not establish a complete legal or platform acceptance.

## Native CI job recovery — October 7

[Recovered native CI job](../benchmarks/reports/2026-10-07/native-ci-patch-recovery.md)
adds hosted native lifecycle tests to the local workflow. Thirty-one local tests
pass with pinned engines; hosted Ubuntu acceptance remains open. Reconstructed
from another chat's recorded workflow, not its original patch file. No publishing,
retargeting or merge occurred. Priority 5 release gates remain open.

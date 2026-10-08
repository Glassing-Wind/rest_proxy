# Shared task system — direction and first acceptance milestone

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


Updated October 6, 2026. The user has started a separate rental-project conversation.
Keep rental implementation there and reusable coordination here in rest_proxy.
This is an implementation direction, not a deployed team or permission system.



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


## Place in the five priorities

The primary home is **Priority 3: FIRE continuity**. A shared task system extends
scoped checkpoints into durable assignments and evidence handoffs that survive
conversation compaction, worker failure and process restart. It coordinates work
across repositories without merging their application code or private scopes.

| Priority | Contribution and acceptance |
| --- | --- |
| 1. Reliable indexing | Tasks consume published usable evidence; index cancellation/recovery must remain safe. Task leases are distinct from index-writer/IDE leases. |
| 2. Embedded storage | Retain task metadata locally and expose explicit owner/scoped MCP and REST operations. Reuse existing persistence patterns; choose the task schema/store during implementation. LadybugDB/LanceDB remain the evidence foundation. |
| 3. FIRE continuity — primary | Durable task identity, revisions, assignment, claim/lease, checkpoints, handoff, resume, cancellation and corrections. Restart must not lose evidence or silently duplicate a claimed action. |
| 4. Bounded context assembly | Build each worker's bounded task/evidence bundle; distinguish prior findings from current source and avoid repeated history injection. |
| 5. Release and outcome validation | Verify restart/reclaim and permission boundaries; use rental as a practical outcome case. Track measured effort, usage and review results; passing task contracts alone proves no savings. |

This is the next small rest_proxy coordination slice within the five priorities.
Keep existing release gates open. Customer discovery and rental bug work can proceed
without waiting for a full autonomous team or new orchestration framework.

## Repository and conversation boundaries

| Workspace / conversation | Owns |
| --- | --- |
| rest_proxy | Task protocol, persistence, permissions, worker adapters, routing, FIRE evidence and team status. |
| rental | Applicant/tenant/landlord journeys, disposable backend fixtures, application fixes and regression tests. |
| rentallaw | Its legal-source provenance and domain validation, when separately scoped. |

The rental conversation's existence is user-reported; it has not been inspected or
messaged from here. No cross-chat messaging was authorized by this documentation
request. Separate conversations do not synchronize automatically. Until an adapter
exists, exchange a concise handoff containing repository/revision, task ID when
available, question, evidence, changed files, checks, limits and next action.
Do not copy credentials, raw private contacts or tenant records into handoffs.
Historical findings from the first rental demo are retained in
[the source-bound report](../benchmarks/reports/2026-10-06/rental-user-qa/investigation-report.md).
The prior rental fix was left uncommitted; current rental state must be inspected
in that conversation before edits or commits.

## First implementation milestone: durable task registry

Start with one local registry and a deterministic test worker, before adding a
model worker. Reuse scoped FIRE persistence where suitable; do not introduce a
mandatory inference service. Framework choice is separate from the task contract.

Minimum task record: stable ID, schema/revision, project and authorized workspace,
origin/goal, acceptance criteria, permitted tools/actions, evidence references,
status, assigned worker, claim expiry, bounded attempt count, checkpoint/result,
review outcome and timestamps. Keep credentials outside task payloads.

Minimum lifecycle: queued → claimed → review_pending → completed, with explicit
failed/cancelled states. Worker progress is checkpointed. Expired claims become
eligible for an explicit guarded reclaim; completion requires the current claim
and revision. A reviewer can request correction without erasing original findings.
Persist cancellation and reject later stale-worker completion.

Read/retrieval capabilities are scoped separately from source writes, browser
mutations and external actions. Enforce permissions in tool dispatch, not just
worker prompts. A task assignment must not expand the user's authorization.
Do not imply exactly-once side effects: restart recovery requires idempotency or
reconciliation before retrying an action with an uncertain outcome.

Acceptance fixture, entirely local and without external inference:

1. Create an investigation task scoped to a synthetic repository and evidence.
2. One worker claims it; another cannot acquire the active claim.
3. Save a checkpoint, close/reopen the registry and recover the assignment/evidence.
4. Reclaim an expired task under a new claim; reject old-worker updates.
5. Submit a structured finding, record review and complete under revision checks.
6. Verify cancellation, cross-project access rejection and action permission denial.

Success establishes a durable local coordination contract. It does not establish
independent reasoning quality, a running Qwen worker or a connected Codex supervisor.

## Follow-on integration

Add an optional local Qwen adapter after validating the registry. Test model/tool
compatibility, task/time/request budgets, stop conditions and structured outputs.
PydanticAI is a candidate for typed worker boundaries; no dependency is required
for the registry milestone. Evaluate more orchestration only when actual branching
or delegation warrants it. Avoid autonomous recursive delegation by default.

The browser-testing role consumes a rental task and returns reproduction steps,
expected/actual behavior and source-bound evidence. The implementer fixes rental
code in its workspace; a separate reviewer replays the failure. Begin with human
review if an independent worker is unavailable. Record reviewer identity honestly.

Siri and Muse are possible request/finding interfaces, pending supported adapters.
They are not connected. This chat is not automatically callable as a background
supervisor. Smart-glasses capture remains a separate input direction. The existing
scheduled continuation can prepare authorized local work; it is not a shared queue,
worker scheduler or authorization mechanism.

## Repository sync policy — user authorization October 7

Commit and push completed, validated changes to the existing working branch with
normal non-force pushes. Inspect hosted CI and address failures. Preserve unrelated
user/remote changes. Explicit approval remains required for merging, deployment,
release publication and operational model/service lifecycle changes. This supersedes
the automation's earlier assistant-authored blanket no-push restriction; that earlier
restriction was not an identified original user preference. Messaging/outreach and
commercial commitments still require separate explicit authorization.

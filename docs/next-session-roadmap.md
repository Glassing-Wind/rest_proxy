# GraphRAG evaluation roadmap — revised 2026-09-06

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


## Current five-priority status

Use [the October 6 completion table](five-priority-status.md) for current completed
slices, remaining gates and evidence. The dated entries below are historical
checkpoints; later entries at the top supersede pending work in older paragraphs.
In particular, IDE lease renewal, owned watcher dispatch and guarded watch setup
are implemented; deployment of a chosen IDE adapter and new-file enrollment remain.

## Current checkpoint — October 6, 2026

**Current next rest_proxy milestone: the [shared task system](shared-task-system.md),
primarily Priority 3.** Implement a minimal scoped durable task registry and local
claim/restart/review acceptance fixture before model adapters. The user has started
rental work in a separate project conversation; disposable database journeys and
application fixes belong there. This chat owns coordination and evidence handoffs.
Cross-chat synchronization and messaging are not established. Existing technical
release gates and customer-validation work remain open.

[Agent-team user testing](agent-team-user-testing.md) now has its first local
rental-applicant browser demonstration: one reproduced submission blocker, a narrow
working-tree fix and 13 passing focused tests. Backend fixture responses do not
establish database/payment/email acceptance. Rental next: a disposable full journey. Rest_proxy next: durable
structured finding/review handoff; no persistent multi-agent runner is implemented.
A [filled, source-bound report](../benchmarks/reports/2026-10-06/rental-user-qa/investigation-report.md)
now answers three questions with four retained snapshots and a machine-readable
handoff. Four offline integrity checks pass; independent review remains open.
Full database journey awaits an explicitly isolated disposable test configuration.


**Direction change: near-term income validation.** Follow the
[commercial direction](commercial-direction.md) and prepare a source-graded FIRE
pilot demonstration plus reusable report template. The first rental demonstration
and filled report are available; durable coordination is the next engineering slice. The
[draft paid offer](paid-investigation-pilot.md) needs a demonstrated delivery scope,
then user-led buyer interviews and one paid pilot. The user targets $3,000/month and reports 10,000+ unqualified contacts plus
rental-platform/rental-law repositories. Confirm repository paths, then prefer a
rental-domain demonstration and a manual shortlist of 15–20 relevant contacts.
TurboTenant is one potential prospect; Meta SDK exploration stays separate and
bounded. No outreach, qualified buyer access or revenue is established. Keep the five engineering priorities and exact release
gates; resolve packaging work according to the intended delivery rather than
letting broad unused-grammar work postpone testing customer demand. CLARITY/glasses
remain independent longer-term directions.

[Observed grammar notices](grammar-notice-reconciliation.md) now bind ten cached
macOS libraries to the verified release bundle and retain nine declared-source root
notices. Their SBOM and local CI pass. Coverage excludes the other 361 bundle files
and independent binary source builds. Next: exact native dependency closure and
remaining release validation, then installed-model/paired coding acceptance.

[Namespace notice reconciliation](namespace-notice-reconciliation.md) now verifies
exact PyPI artifacts, installed files and immutable release-tree package blobs, and
collects a bound supplemental license for both namespace packages. Original upstream
notice omissions remain recorded. Next: native/grammar notice reconciliation; real
installed-model and paired coding outcomes, backup policy and platform gates remain.

[Release inventory and cold restore](release-inventory-and-restore.md) now passes
a schema-valid incomplete SBOM and graph/vector/FIRE cold-copy fixture recovery.
Four explicit package metadata/notice gaps and native/grammar/model closure remain
release work. Next: reconcile those exact artifacts, then installed real-model and
controlled paired coding outcomes; operational backup/deletion policy stays open.

[Minimal embedded installed acceptance](minimal-embedded-install.md) now separates
core/embedded/full extras, packages the missing parser diagnostics module and
configures writable runtime paths outside installed modules. Fresh environment
dependency checks and native fixture/fresh-daemon REST acceptance pass. Next:
exact artifact notices/SBOM and backup/restore, then real installed-model and paired
coding outcomes. Exact-pin rebuild/platform coverage remains open.

[Distribution layout acceptance](distribution-acceptance.md) now includes both
subprocess indexing workers in wheel/source archives and checks an offline isolated
artifact install. Baseline omission is reproduced; local CI passes. Next release
slice: minimal embedded dependency profile and installed-daemon/native indexing
acceptance, including writable state/runtime paths. Paired outcomes remain open.

[Opt-in LM Studio context forwarding](context-provider-forwarding.md) now formats
and recounts the serialized request, requires a resident model and forwards a
stateless bundle from the shared owner. Real Qwen usage was 807 prompt/15 completion
tokens in one disposable FIRE case; focused tests and local CI pass. Exact template
attestation, provider continuation and controlled paired outcomes remain open.
Next: controlled coding-task acceptance and minimal release packaging.

[FIRE snapshot-to-bundle integration](fire-context-bundles.md) now recovers one
scoped state/original snapshot into historical candidates without replacing caller
instructions or goal. Corrections, expiry/deletion and degradation are explicit;
real fresh-process STDIO/HTTP/REST parity passes. Next milestone: one explicit
provider request formatter/forwarder with serializer, tokenizer/framing, actual
usage and continuation handling verified.

[Shared supplied-request context bundles](context-bundle-contract.md) now preserve
citations and complete text messages while accounting canonical request JSON plus
reserves. Deterministic selection, omissions, local tokenizer mode and real MCP/REST
parity pass. This is not verified provider serialization or usage. Next work: scoped
FIRE checkpoint-to-bundle adaptation, then an explicit provider formatting/forwarding
slice with actual usage and continuation-deduplication acceptance.

[Explicit durable FIRE continuity](fire-durable-continuity.md) now stores scoped
checkpoints and hashed originals with correction, expiry/purge and deletion. Real
STDIO save to fresh HTTP resume recovers state after removing fixture conversation
and source. Current-source validation remains explicit caller work. Next milestone:
shared context-bundle contracts and whole-request budgeting; client cadence, history
inspection and operational restore/outcome work remain open.

[Shared-owner REST evidence reads](embedded-rest-evidence.md) now expose an opt-in
read endpoint on the existing HTTP daemon. Tools, citations and storage ownership
are shared with MCP; body/result bounds, mutation refusal and flag/backend gating
are tested with real transport parity. Inference-proxy bundle injection is unfinished.
Next coordinated milestone: scoped durable FIRE checkpoints and original evidence,
then shared whole-request context budgeting. Remaining Priority 2 coverage stays open.

[Python function-import bindings](embedded-symbol-imports.md) are now published as
a distinct cited relationship kind and included as a bounded import-overview page.
Aliases, relative imports, conservative exclusions, old-index status and native
persistence/paging are verified with real STDIO/HTTP parity. Next work should address
remaining owner integrations or the scoped FIRE checkpoint store; broader binding
and general flow coverage remain incomplete.

## Previous checkpoint — October 5, 2026

[Embedded backend route overview](embedded-route-overview.md) now returns verified
native route declarations through the explicit backend tool. Source/fact hashes,
filters, bounds, persistence and real STDIO/HTTP parity pass. This does not supply
service/database hops, broad framework coverage or auto/UI flow parity. Next work
should address resolved imports or remaining owner integrations using the priority table.

[Embedded symbol references](embedded-symbol-references.md) now routes the standard
tool to verified static callers with bounded multi-workspace results and explicit
partial coverage. Native snapshot/reindex/ambiguity and real STDIO/HTTP parity pass.
Next bridge work: resolved imports and route summaries; broader reference coverage
and remaining owner integrations still prevent full Priority 2 completion.

[Embedded declared-import overview](embedded-import-overview.md) now routes the
standard summary through the shared owner. Named declaration rankings, source/fact
citations, wildcard counts and scan/output bounds are verified; implicit imports
and resolved symbol edges remain unsupported. Native persistence/corruption checks
and real STDIO/HTTP parity pass. Full reference/binding/route-summary parity remains
unfinished; use the five-priority table for current scope.

[Embedded related files](embedded-related-files.md) now connects the existing
standard tool to the shared owner's published import/call/route file candidates.
Reads are cited, model-independent and bounded; native reopen/reindex/deletion,
publication guards and real STDIO/HTTP parity pass. This file-level bridge leaves
symbol-reference/import summaries and full graph parity incomplete.

The [Qwen worker trial](../benchmarks/reports/2026-10-05/qwen-worker-trial.md) now
demonstrates one local known-file investigation, patch proposal and Codex review.
The first attempt truncated; the accepted attempt required supervisor corrections.
Optional `LMSTUDIO_API_KEY` support is implemented for the embedding adapter with
credential exclusion from encoder identity and error redaction. Direct LM Studio
HTTP was tested; automatic local/cloud routing, rest_proxy worker-route acceptance
and token savings remain unverified. Use the five-priority table above for scope.

[Watch this project setup](embedded-watch-setup.md) now previews service/model/source
readiness and current manifest scope, then enables intent using exact revision/run
preconditions. Blocked or stale requests do not enable watching. CLI and MCP setup
keep global service permission separate; no real watch was enabled. Native ready/
blocked cases and real blocked-CLI acceptance pass. All seven hosted checks passed
for automatic refresh/watch dispatch commit `1d3d6d7`.

[Automatic refresh and owned watch dispatch](embedded-refresh-and-watching.md) now
provide foreground IDE lease renewal and opt-in owner polling. Polling hashes current
published manifest paths and republishes changes through the existing owned pipeline,
with matching encoder identity and prior-publication preservation on failure. Native
background task and real HTTP registrar/SIGTERM checks pass. New-file enrollment,
chosen IDE adapter deployment, richer retry/history and controlled outcomes remain
pending. All seven hosted checks passed for registrar commit `53ca2a5`.

[Embedded IDE registration](embedded-ide-registration.md) now connects the existing
registrar CLI to an explicit owning HTTP MCP endpoint, with registration/release and
unique unexpired lease discovery. Real owner/subprocess acceptance verifies no
legacy registry writes and no second graph owner. Native ambiguity/expiry/root-change
checks pass. Synchronous config/supervisor migration, chosen IDE adapter heartbeat
policy and owned watcher dispatch remain pending. All seven hosted checks passed
for the dependency repair commit `d360367`.

[Dependency audit repair](../benchmarks/reports/2026-10-05/dependency-refresh.md)
updates multidict/fsspec in both manifests after two new active-branch findings.
The audit passes with its existing exclusions unchanged; Python 3.14 install,
dependency consistency, native repository checks and local CI pass. This newer
failure supersedes earlier green-check observations for subsequent heads.
IDE registrar/discovery and embedded watcher dispatch remain next work.

[Embedded workspace activity](embedded-workspace-activity.md) now persists typed
watch intent and bounded expiring client leases under revision/publication
preconditions. Reopen, same-root reindex, root changes, expiry/limits and deletion
are verified. This records desired state, not worker activation or process liveness.
IDE registrar/discovery migration and embedded watcher dispatch remain pending;
legacy JSON registries are not redirected. All seven hosted checks passed for
the prior journal commit `fc92c90`.

[Embedded indexing journal](embedded-indexing-journal.md) now persists the latest
owned attempt and its phases/outcome. Success commits with the publication receipt;
reopen marks unmatched unfinished work interrupted. Cancellation, post-commit error,
SIGKILL recovery and prior publication preservation are verified. The primary status
read is bounded and model-independent; it waits for the owner lock rather than
providing live phase polling. Sessions/watch intent, history/resume and REST remain
pending. All seven hosted checks passed for the prior metadata commit `88d6018`.

[Embedded project metadata](embedded-project-metadata.md) adds bounded, revisioned
user/agent annotations in the shared Ladybug owner. Exact revision and publication
preconditions prevent stale writes; same-root reindex preserves notes, root changes
hide old notes, and project deletion removes them atomically. Native persistence,
conflict/rollback/isolation tests and real transport read parity pass. This is the
first application metadata slice; sessions, watchers, jobs, retention and REST remain
pending. GitHub CI review found the active PR #3 head green; reported security
failures belong to the older enterprise-hardening branch (see the metadata report).

## Prior checkpoint — October 4, 2026

[Embedded static relationships](embedded-static-relationships.md) now publish
scoped file links with callable IDs, source citations and explicit resolution rules.
A fresh 131-file real-model run produced 1,118 call and 186 import candidates;
all relationship/source/fact hashes and STDIO/HTTP evidence verified. These are
static source candidates, not runtime guarantees or full query parity. Broader
resolution and remaining reference/import query bridges remain Priority 2
work. Existing `get_call_chain` now traverses bounded published static candidates
with cycle handling, citations and explicit truncation; native and real transport
checks pass. Existing `get_symbol_context` now supports exact published symbols, bounded
original source and static caller/callee pages; ambiguous names return choices.

[Embedded parser facts](embedded-parser-facts.md) now persist hashed imports,
syntactic call observations and native route/HTTP facts with the publication.
A fresh real-model run on 131 frozen files contains 806 imports and 12,996 calls;
all source/fact hashes and STDIO/HTTP evidence verified. Call targets are not yet
resolved relationships. Full symbol/caller/import/route queries remain Priority 2
work, along with remaining application metadata and REST integration.

[Embedded project discovery](embedded-project-discovery.md) now lists durable
publication IDs/roots/runs and routes the existing project-resolution and overview
tools through the embedded owner. Canonical path/unique-name resolution refuses
ambiguity and invents no missing-project hash. Metadata survives rollback/reopen
and follows scoped deletion. Full graph queries, other workspace/session/watch/job
metadata and REST integration remain Priority 2 gates.

The [embedded MCP owner](embedded-mcp-owner.md) now exposes explicit manifest indexing,
overview, published source and scoped retrieval through a shared process runtime.
Real STDIO/HTTP MCP reads matched on the 131-file publication with external-storage
networking denied and no embedding connection. Graph bootstrap shares the graph
owner. Full graph queries, application workspace metadata and REST integration
remain Priority 2 gates; the standard index worker stays guarded.

The [real embedding baseline](real-embedding-acceptance.md) now passes strict
Jina/LM Studio indexing and reopened retrieval on 131 frozen production files /
1,629 chunks. Five source-anchor probes covered 4/5 expected files with vector
search and 5/5 with hybrid; these are not controlled coding outcomes. Provider
response indices and truncation checks are hardened, and encoder metadata persists
with the publication. Resident model-artifact binding remains unverified (CLI/API
size metadata differs from the local GGUF). The explicit MCP path above now shares its owner; full graph queries, application
metadata and REST integration remain Priority 2 implementation gates.

The [embedding provider direction](embedding-provider-direction.md) keeps LM Studio
optional and proposes FastEmbed/ONNX native and TEI service evaluations. The
configured Jina code-embedding model supplied the functional baseline above;
controlled comparisons and production model-identity acceptance remain pending. Compare measured retrieval,
resources and Python 3.14 installs before choosing a default; record preprocessing
and truncation in encoder identity. No provider or inference setup changed.

[Combined embedded publication](embedded-run-publication.md) now stages LanceDB
chunks under a fresh run and exposes them through the atomic Ladybug receipt.
Native text/vector/hybrid retrieval, failure/cancellation/crash recovery, cleanup
and deletion passed. Network-denied storage acceptance covered 130 files and
1,622 chunks using explicitly synthetic vectors; semantic quality is unmeasured.
The real-provider baseline above extends that synthetic storage proof; production
provider identity, complete graph queries and MCP/REST owner routing remain Priority 2
gates. The outline checkpoints below preceded this work.

[Owned embedded outlines](owned-embedded-outlines.md) now publish structural
file/symbol snapshots and originals atomically, protected by a local database
owner lock. Network-denied indexing/reopen inspected 128 production Python files
and 1,122 symbols. Combined publication now extends this foundation; full query
compatibility and application owner routing remain Priority 2 work.

The [embedded transaction adapter](embedded-transaction-adapter.md) now passes
native Python 3.14 rollback, cancellation, read-only and persistence checks.
Application bootstrap now selects Ladybug in embedded mode and supports explicit
file-outline schema/query reads. Kuzu selection is retired; indexing workers
refuse embedded mode until owned writes exist. The remaining query corpus,
single-owner indexing, LanceDB and metadata integration are the next Priority 2 gates.

Latest [reliable-indexing acceptance](../benchmarks/reports/2026-10-04/reliable-indexing.md)
fixes fetch-method cross-call leakage and resolves the current rental golden to
78 source-correct route links (paired baseline 80; June's historical 81st edge is
not reconstructible). Both manifests now pin published fork candidate
`6fcead43fc13b0049481ea5b5c491e02eab4ac68`; draft fork PR #2 has passed hosted
validation and Linux/macOS/Windows native wheel acceptance. Interruption fixtures
preserve published data through staging termination and uncommitted-publication
SIGKILL. Local owned-writer adjudication, persisted cancellation identity checks
and publication-aware restart reconciliation are implemented. Multi-host/uncertain
writers remain protected; coordinated graph/vector publication and embedded
integration remain next milestones. Earlier pin and pending-golden entries below
are historical checkpoints.

The [routing/framework review](evidence-routing-framework-research.md) recommends
an optional evidence router built on the current catalog/read dispatcher, evaluated
against agent-selected tools before retrieval changes. LangGraph is an optional
workflow runtime; LightRAG/Fast GraphRAG are document-retrieval benchmark candidates.
Graphiti's temporal-memory experiment belongs to the separate personal-memory
direction, with synthetic incident timelines considered independently. No framework
adoption or Ladybug/LanceDB compatibility is established. Existing recovery,
publication, embedded-storage and FIRE continuity work remains the implementation
priority; routing is a bounded follow-up evaluation.

Latest implementation: [legacy cleanup and authentication isolation](legacy-shadow-adjudication.md)
removed the abandoned 582 nodes / 1,639 incident relationships without changing
the live canonical graph fingerprint or publication IDs. No shadow residue remains.
Monitored CI traced a reproducible authentication burst to late Neo4j imports in
`test_index_workspace.py`; keeping the stub active throughout tests eliminated new
invalid-credential/rate-limit events in graph regressions and full CI. Local
redacted failure evidence now survives graph status-write outages. Legacy
adjudication is implemented; uncertain running/remote-writer recovery remains.
The entries below retain the preceding investigation checkpoints.

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

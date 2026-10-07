# Shared task system — direction and first acceptance milestone

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

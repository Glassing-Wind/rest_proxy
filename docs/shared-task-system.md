# Shared task system — direction and first acceptance milestone

Updated October 6, 2026. The user has started a separate rental-project conversation.
Keep rental implementation there and reusable coordination here in rest_proxy.
This is an implementation direction, not a deployed team or permission system.


## Local registry foundation — October 6 acceptance

[Registry receipt](../benchmarks/reports/2026-10-06/shared-task-registry.md): scoped
SQLite create/read/claim/checkpoint/reopen and revision/expiry guards now pass twelve
offline tests, including cancellation, expired read-only reclaim and retained
finding/correction review/completion transitions. This is a trusted local library, not authenticated task endpoints or
worker execution. Original findings and review decisions survive corrections and completion.
Distinct reviewer IDs are required but are not authenticated identities. Next add
a local task interface and scoped read-only tool dispatch;
MCP/REST adapters and tool-dispatch permissions remain open. No persistent agent
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

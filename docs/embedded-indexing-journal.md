# Embedded indexing journal — October 5, 2026

Owned embedded indexing now retains its latest attempt per project in Ladybug.
It records a UUID attempt ID, workspace root, candidate publication run, last phase,
status, update time and error class. It stores no raw exception message, source,
embedding response, process PID or legacy worker control.

## Lifecycle

Phases are snapshot, chunking, embedding, staging and publishing. Project/encoder identity
validation and runtime model connection happen before the owner starts an attempt;
those failures do not create a journal entry. Once started, failures/cancellation
preserve the prior published repository and record the last reached phase.

The existing publication transaction marks the attempt `published` together with
outlines, originals, relationships and the publication receipt. Attempt identity,
candidate run and running status must match or the entire transaction rolls back.
An exception delivered after commit cannot turn a published attempt into a failed
one: finalization checks the receipt before recording failure. Cancellation drains
journal finalization before releasing the owner lock. If finalization itself fails,
the original indexing error is preserved and reopening reconciles the entry.

After acquiring the exclusive graph owner, reopening reconciles leftover running
attempts. A candidate matching the publication becomes published; other unfinished
attempts become `interrupted` with `OwnerRestart` as the error class. No worker is
restarted and no candidate is promoted by recovery. The publication receipt remains
the visibility authority. Unpublished vectors are still removed only by the existing
scoped cleanup mechanism.

The journal contains only the latest attempt for each project. A new attempt
replaces it, without losing the prior published evidence. Scoped project deletion
removes its attempt in the same graph transaction as annotations and publication.
There is no attempt history, retention scheduler, cross-host execution, automatic
resume, or legacy JSON-job migration.

## Read tool

`get_embedded_indexing_attempt(project_id)` is a primary-profile opt-in embedded
MCP tool. It returns latest attempt fields, `published_run_id`, and
`automatic_resume: false`. Projects indexed before this schema was introduced return
`no_attempt`, while still reporting their current publication. Successful indexing
also returns `attempt_id` to callers.

The read shares the owner/runtime locks. Concurrent reads wait for owned indexing
to release those locks; this is durable outcome/recovery inspection, not live phase
polling during an in-process index operation. It does not cancel, signal, queue or
start a worker. The existing standard indexing worker remains guarded in embedded
mode.

## Validation

Two offline tests verify repeated-cancellation draining, exception propagation and
bounded project IDs. Nine runtime/MCP tests pass. Thirteen native repository tests
pass, including journal success, provider failure without storing message text,
cancellation, post-commit delivery failure, reopen persistence and scoped deletion.
The existing real subprocess SIGKILL fixture now verifies an interrupted publishing
attempt, its abandoned candidate, and preservation of the prior searchable receipt.

Real sandboxed STDIO/HTTP reads match on the existing frozen 131-file publication.
That older publication truthfully reports `no_attempt`; native disposable fixtures
exercise actual lifecycle records. Full local CI passed. This establishes metadata
and recovery behavior, not performance or coding-outcome improvement.

[Acceptance receipt](../benchmarks/reports/2026-10-05/embedded-indexing-journal.json).
Private logs: `.runtime/embedded-job-acceptance/` (mode 700).

## Remaining metadata work

IDE sessions and pinned-watch intent currently live in machine-local JSON registries
used by synchronous supervisor/config and legacy indexing paths. They are not
redirected into the process-owned graph here. A migration needs explicit handling
for standalone registrars, session expiry/liveness, concurrent writers and safe
watcher dispatch to the embedded owner. Persisting watch intent must not silently
activate the guarded legacy worker. Those integrations, job history/resume policy,
REST ownership and FIRE checkpoint history remain pending.

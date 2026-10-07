# Agent team and user-journey testing — October 6, 2026

Add a user-testing role to the proposed FIRE team. The user authorized local
rental-platform investigation and bug fixes. This first demonstration was performed
by the current coding agent with Playwright; no persistent multi-agent runner or
PydanticAI integration has been installed or demonstrated.

## Roles and handoffs

| Role | Task | Required output |
| --- | --- | --- |
| User / product owner | Choose the journey and acceptable outcome. | Persona, goal, scope and permitted environment. |
| User tester | Operate the actual interface as an applicant, tenant or landlord. | Reproduction steps, expected/actual behavior, screenshots, console/request evidence and environment. |
| Investigator / implementer | Trace the failure into source and make a narrow fix. | Source-cited cause, patch and regression check. |
| Reviewer | Replay the original failure and check nearby cases. | Verified result and remaining limits. |
| FIRE memory | Preserve scoped evidence and checkpoints. | Provenance, revision, retention and recoverable handoff. |

These are workflow roles; this run used one agent, not independent reviewers.
Local Qwen/PydanticAI and a supervisor are candidate implementations. Validate
structured tool outputs and model compatibility before relying on a worker.
AutoGen's current upstream recommends Microsoft Agent Framework for new projects;
Magentic-One is an orchestration pattern to evaluate, not our real-time ingestion
pipeline. [AutoGen status](https://github.com/microsoft/autogen),
[PydanticAI durability/MCP](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/).

## First completed slice

Found the rental platform at `/Users/michaelmarler/Projects/rental` and rental-law
repository at `/Users/michaelmarler/Projects/rentallaw`. The latter is not assessed
here. Rental had extensive pre-existing uncommitted changes; they were preserved.

A browser applicant selected a co-applicant, entered an invalid email, removed the
co-applicant and tried submitting. Hidden enabled controls prevented native form
validation, leaving the page at INVITED with no submission. A narrow local fix
disables inactive co-applicant inputs, restores validation when selected, and keeps
submitted applications read-only. Replay reached SUBMITTED with only PRIMARY in
the fixture request. Thirteen focused tests passed.

[Reproduction, patch and acceptance evidence](../benchmarks/reports/2026-10-06/rental-user-qa/README.md).
The rental fix and test remain uncommitted to avoid mixing this slice with the
user's existing work. The preserved patch records only this session's code delta.

## Project handoff — October 6

The user has started a rental-project conversation. Application journeys, test
backend setup and fixes continue there. rest_proxy implements the
[shared task system](shared-task-system.md), primarily Priority 3, to retain scoped
assignments and evidence across workers. No automatic cross-chat synchronization
or connected persistent team is implemented.

## Next rental milestone

Choose one full applicant or tenant journey in a disposable test database, with
payments, email and third-party accounting mocked. Verify draft resumption,
submission, refresh/reopen and a visible failure/retry state. First confirm the
local test configuration; this fixture does not establish backend persistence.
Persist a structured finding and have a separate review pass replay it before
introducing multiple autonomous workers. Keep request/time budgets and stop rules.

Use synthetic identities and local fixture/test environments by default. Live
messages, payments, invites, lease signatures, publication and destructive actions
need explicit authorization. Do not replay user credentials or real tenant data
into reports. Separate observed browser behavior from mocked API outcomes and
source-derived hypotheses. This supports the commercial demonstration; it does
not establish paid demand, lower cost or better outcomes than other tools.

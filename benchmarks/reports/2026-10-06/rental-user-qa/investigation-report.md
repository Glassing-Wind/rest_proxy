# Supervised rental repository investigation — October 6, 2026

Status: internal demonstration, not a client engagement or independently reviewed
assessment. Scope: the public applicant page's co-applicant validation and state
hydration. User authorized local investigation and reversible fixes. No remote
model or service received customer code; no customer identities were used.

## Answers and source grading

| Question | Finding | Retained source and verification | Limits |
| --- | --- | --- | --- |
| Why did removing a co-applicant prevent submission? | Hiding inputs left invalid email controls enabled; Chromium blocked the submit event. | Before/after browser steps in [receipt](README.md); fixed field participation in [application-apply.js](source/application-apply.js), lines 161–167 and 272–277. | API data/response are fixtures; no database writes. |
| Does the fix preserve active validation and submitted read-only data? | Selected invalid email still blocks in Chromium; toggling restores input participation. SUBMITTED hydration keeps inputs disabled. | Browser replay plus [three DOM-state tests](source/application-apply-ui.test.ts); [hydration](source/application-apply.js), lines 177–200. | Submitted restoration checked with DOM fixture, not persistent backend/browser reopen. |
| What is needed for the next full journey? | Explicit disposable database identity, isolation checks and external-service mocks before test writes. | [Shared Prisma client](source/prisma.ts), lines 1–17, uses DATABASE_URL. Inspection found no test database guard in the searched tests/config. | Bounded inspection, not an exhaustive infrastructure audit; no existing database connection attempted. |

Working-tree snapshots are retained with SHA-256 hashes in [finding.json](finding.json).
The recorded Git HEAD does not identify uncommitted changes. The verifier checks
hashes and citation bounds; it does not determine whether statements are true.
The investigator reread cited code, but an independent review remains pending.

## Relationship and change impact

The Add a co-applicant checkbox dispatches `change` to `syncCoApplicantFields`
(lines 272–273); that helper controls visibility and input editability (161–167).
Native browser validation runs before the form's submit listener (275–277).
The submit path builds only checked co-applicants, then hydrates the response
(see full retained JS). Hydration reuses field synchronization after locking
submitted inputs (177–200). These edges describe this page, not complete runtime
coverage or the rest of the rental platform.

Small implemented change: disable inactive controls rather than discard their
values; enable them when selected; retain submitted-state locks. No new rental
dependency. Thirteen focused tests passed; node syntax and diff checks passed.
Existing user changes are preserved; rental changes remain uncommitted. Rest_proxy
retains only this session's patch and evidence.

## Effort, acceptance and retention

No indexing was used. Native source reads, Playwright and Vitest supplied evidence.
Elapsed delivery effort, model tokens, direct costs and fallback count were not
instrumented and remain unavailable. No savings or superior-outcome claim. No
client payment, acceptance or outreach. One agent performed all roles.

Retained snapshots contain inspected source and synthetic fixture data, not secrets
or tenant records. Evidence remains in this local repository; it has not been
published. The next scope is draft/save/refresh/submit against an explicitly
isolated disposable backend, followed by structured worker/reviewer handoff.

# Rental investigation demonstration — October 7

Internal reviewable delivery, not a customer engagement. Compiled from retained
rental evidence and source-bound reports; no fresh browser run in this milestone.

## Problem and delivered behavior

An applicant could select a co-applicant, enter an invalid email, remove that
co-applicant and still be unable to submit. Hidden controls remained enabled and
participated in browser validation. The narrow fix disables inactive controls,
restores them when selected and preserves submitted-state locks. Initial before/
after browser reproduction and 13 focused checks are retained in
../../2026-10-06/rental-user-qa/investigation-report.md and its source snapshots.

A backend journey also exposed cleared notes reappearing after submission: omitted
empty input left the stored value unchanged. The rental implementation explicitly
sends cleared text and maps supplied blank text to null. API omission remains a
separate operation. Rental docs/qa/applicant-journey.md documents failure and fix;
original failing persistence is retained in the rental schema-only receipt.

## Latest acceptance available

Read /Users/michaelmarler/Projects/rental/docs/qa/multiline-note-acceptance.md and
output/playwright/rental-applicant-receipts/2026-10-08T051323Z-multiline/validation.json.
The latest receipt reports full browser pass and 30 focused tests. Saved multiline
notes survive refresh and match disposable PostgreSQL; final submit clears notes.
External integrations mocked; test services stopped. Latest local rental commit
3644f4d adds multiline acceptance. It does not identify all tested application code:
the rental working tree contains substantial unrelated modifications, so source
hashes in the receipt are essential. No production/deployment acceptance claimed.

## Five-minute internal walkthrough

1. Show original hidden-invalid-email reproduction and before/after evidence.
2. Explain the browser validation mechanism and narrow change.
3. Show saved/resumed state and PostgreSQL receipt from the disposable journey.
4. Show targeted regressions and explicit limitations.
5. Describe the deliverable: reproduction, source-grounded diagnosis, narrow fix,
   verification and handoff for one agreed workflow.

## Limits and next acceptance

No independent review, general platform audit, payment/email/accounting acceptance,
lease signing or cross-browser coverage. No measured elapsed delivery effort, model
cost, token savings or buyer demand. Native reads/browser/testing supplied the result;
FIRE retrieval and a persistent agent team were not demonstrated. Do not sell this
as an already validated autonomous product.

Next make the evidence easy to review in one screen recording or live local walkthrough
in the rental chat, then choose a bounded pilot scope with the user. No outreach,
commercial promise or recording of real applicant data authorized by this artifact.
Further framework/model tuning and gateway expansion are parked as delivery work
unless they resolve a concrete blocker. This is a direction decision, not deletion
of the documented longer-term ideas or suspension of automations.

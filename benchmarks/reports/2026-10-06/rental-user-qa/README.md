# Rental applicant user-journey receipt — October 6, 2026

Environment: macOS, Chromium through Playwright CLI, repository
`/Users/michaelmarler/Projects/rental`, current uncommitted working tree.
Read its AGENTS.md before edits. No production server, credentials, database,
Stripe, mail or external account was used. Native source reads and browser tools
were used directly; no indexed MCP investigation or local-model worker was used.
No paired time/token comparison was performed.

## Reproduction and cause

1. Serve the actual `src/public/apply.html` and assets with the fixture below.
2. Open `http://127.0.0.1:8769/apply?token=fixture` in a fresh browser session.
3. Synthetic primary applicant contact fields are prefilled by GET fixture data.
4. Select Add a co-applicant, enter `invalid-email` in its email input.
5. Unselect Add a co-applicant; click Submit application.

Before: form validity false, co-email disabled false, status INVITED; browser
console: `An invalid form control with name='' is not focusable.` No POST was made.
The change listener hid the section without disabling its inputs, so HTML native
validation still considered the inactive invalid email. This is a UI failure,
independent of backend persistence. The unrelated missing favicon returned 404.

After the [narrow patch](application-fields.patch): initial inactive inputs are
disabled; a selected invalid co-email still blocks submission; deselecting allows
submission. Observed form validity true, co-email disabled true, status SUBMITTED.
The synthetic POST body contained only the PRIMARY applicant and no screening
consent. Returned SUBMITTED is a fixture response, not a database acceptance.

[After screenshot](after.png).

## Replay and checks

Run `python3 fixture_server.py /Users/michaelmarler/Projects/rental` from this
receipt directory. Server binds loopback port 8769, reads only the public directory
and writes synthetic submission JSON to `/tmp/rental-qa-submit.json`. It is a
minimal UI fixture, not the production router. Use a fresh browser (the first
reload reused old cached JS during this run; the clean session confirmed the fix).
Replay the steps above, and verify the selected invalid-email case before removal.

`node --check src/public/assets/application-apply.js` passed.
`npm test -- tests/application-apply-ui.test.ts tests/routes.access-and-applications.test.ts`
passed: 2 files, 13 tests. Three new DOM-state tests cover inactive input toggling,
value preservation, draft hydration and submitted read-only hydration; browser
replay supplies native constraint-validation evidence. Existing ten application/
access route tests passed with their mocks. [Regression test copy](application-apply-ui.test.ts).

Rental `git diff --check` passed. No full build or production end-to-end acceptance
was run. Existing rental changes were not staged or committed. Fix remains in
`src/public/assets/application-apply.js`, regression in `tests/application-apply-ui.test.ts`.
The patch is relative to this session's starting file, not rental HEAD: do not
apply it blindly to a different revision. No claim of independent agent review.

## Structured investigation handoff — subsequent October 6 milestone

[Filled demonstration report](investigation-report.md) answers three bounded
questions and records observed versus source-derived findings. [Structured finding](finding.json)
binds four retained source snapshots by SHA-256 and records source ranges, mocked
behavior, reviewer identity and unavailable cost/usage metrics. Run
`python3 verify_receipt.py` and `python3 test_receipt.py` here: source binding passes;
four offline tests reject source drift, escaped paths and invalid citation ranges.
Hash validation is not semantic grading or independent review.

Full database acceptance is deferred: inspected shared Prisma config reads
DATABASE_URL without a test-isolation guard; no dedicated disposable test database
configuration was identified in the searched tests. No existing database or dotenv
credentials were accessed. Configure an isolated test target before backend writes.
The structured report/validation milestone proceeds independently of that blocker.

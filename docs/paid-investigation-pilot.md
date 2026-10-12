# Application workflow investigation — first practical offer

Revised October 8, 2026. Recommended first offer for user review; no outreach,
customer agreement, invoice or payment request has occurred. Price is a hypothesis,
not a measured market rate.

## Find out why users get stuck or their data changes unexpectedly

For a small software team with a reproducible problem in an existing application,
we investigate one workflow from the browser through its API and stored state.
You receive a reproduction, a source-grounded diagnosis, a focused change plan,
and an evidence walkthrough. Start with rental application draft/save/resume/submit
flows or a similar form workflow in a JavaScript/TypeScript or Python application.
Language and framework suitability are confirmed before accepting the engagement.

## Scope and price

Proposed fixed price: **$750** for one repository, one workflow and up to three
agreed questions. Cap total delivery effort at eight working hours, including
setup, investigation, report and walkthrough. Confirm environment suitability,
access and delivery date before accepting work; do not consume the cap setting up
an unsuitable system. Payment timing remains to be agreed by the user and buyer.

Typical questions:

- What sequence reproduces the reported failure?
- Which browser, API or persistence behavior causes it, and what does the evidence show?
- What narrow change and regression checks should follow?

A reproducible diagnosis is the deliverable. If the failure cannot be reproduced,
the report states attempted conditions, evidence and unresolved prerequisites;
this outcome must be accepted in the scope before work starts. Do not promise a
certain number of bugs or a guaranteed fix.

## Deliverables and acceptance

1. Reproduction steps using synthetic data, expected versus observed behavior,
   and the tested revision plus hashes when the checkout is modified.
2. Concise diagnosis with source references and browser/API/database evidence
   where accessible. Distinguish observed facts, inferred mechanisms and unknowns.
3. Focused proposed change and regression plan, including affected behavior and
   integration/coverage limits.
4. A walkthrough of up to 30 minutes, included in the effort cap, plus one bounded
   clarification pass. New workflows or changes require separate scope.

Before delivery, the agreed customer reviewer should be able to follow the
reproduction and inspect the cited evidence. Acceptance means the agreed questions
are addressed with checkable findings or explicitly agreed unresolved outcomes;
it does not certify production readiness. Record all labor and direct compute cost
from the start so the first pilot can test whether this service is sustainable.

## Fit check and boundaries

Require a responsible reviewer, one concrete workflow/problem, authorized source
access and a permitted test environment. Use synthetic records and disposable
storage for writes. Agree processing location, any external model use, retention
and deletion before handling private code or data.

Implementation, deployment, production access, real payments/messages, security or
legal certification, platform installation and ongoing support are separate scope.
A small fix can be proposed as a follow-on after diagnosis; no patch is guaranteed
inside this investigation price. Stop and rescope when setup or findings exceed
the agreed effort rather than quietly expanding the engagement.

## Evidence behind the offer

The [rental demonstration](../benchmarks/reports/2026-10-07/rental-outcome/demo.md)
shows hidden inactive email controls blocking submission and cleared notes being
preserved unexpectedly. Initial source/browser evidence and later disposable
PostgreSQL receipts support the mechanisms and tested fixes. Latest retained
acceptance reports 30 focused tests; selected source identities were verified.

External integrations were mocked, the tested checkout was modified, and there
has been no independent review or customer acceptance. FIRE retrieval, autonomous
team performance, paid-token savings and delivery effort were not measured.
Describe this as supervised investigation using code and tests, without claiming
that the platform itself produced a measured improvement.

First buyer hypothesis: an owner or engineering lead at a small software agency
or rental/software team with a current stuck-user or data-persistence problem.
TurboTenant remains a possible prospect, not a known buyer. The next validation
is a user-led problem conversation about an actual workflow, followed by a fit
check; no contact import or outreach is authorized by this document.

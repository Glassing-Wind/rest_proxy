# FIRE repository investigation pilot — draft offer

Draft October 6, 2026. Pricing and terms are proposals for the user to review;
no offer has been sent, accepted or invoiced.

## Understand the change before you make it

When an unfamiliar application needs a fix or a feature, we investigate the relevant
code and explain what the change is likely to affect. You receive a human-reviewed
report with source references, a relationship map and a practical next-step plan.

**Pilot scope:** one Python repository, one issue or proposed change, and up to
three questions agreed before work starts. A fit check establishes the supported
repository size and relevant subsystem; the whole repository is not promised
unlimited analysis. Questions might include:

- Where does this behavior start, and which functions or routes participate?
- Which observed callers or files might be affected by this change?
- What should be changed and tested, and what remains uncertain?

**Deliverables:** a concise report with file/line or symbol references, a focused
change-impact map, a proposed implementation/test plan, and a walkthrough lasting
up to 45 minutes. Findings distinguish verified source behavior from hypotheses
and known coverage limits. Reports are tied to a recorded source revision.

**Proposed pilot price:** $750, with 50% to start and the balance on delivery of the
agreed report. Investigation and report preparation are capped at eight working
hours; suitability and a delivery date must be confirmed before accepting work.
This is a price to test, not a validated market rate. Client-specific terms and
acceptance need to be agreed before any invoice or payment request.

A successful pilot answers the agreed questions with independently checkable evidence
and actionable next steps, including explicit unknowns. It does not guarantee that
a proposed change is safe to deploy or that every dependency or caller is discovered.
Code changes, production access, security certification, ongoing hosting and software
installation are separate scope. No software platform subscription is included.

The client authorizes repository access and chooses the permitted processing
location and external model use before any source is handled. Only the agreed
subset is processed; private code is not used in public demonstrations. Retention
and deletion of supplied code, generated evidence and backups are agreed explicitly.

## Pre-sale fit check

Confirm the business question, responsible reviewer, source revision/access method,
relevant language/subsystem, available tests and expected deliverable. Agree what
counts as an answer and identify obvious scope risks. If these cannot be bounded,
propose a smaller investigation rather than accepting an open-ended engagement.

Internal preparation and validation are described in
[the commercial direction](commercial-direction.md). A source-graded demonstration
must be ready before presenting this as an available service.

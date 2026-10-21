# FIRE Repository Intelligence Audit

Proposed service scope — October 11, 2026. Price and scope are introductory proposals,
not accepted commercial terms or market-validated claims.

## Understand your code before the next change

**Proposed fixed price: $900**

For teams inheriting unfamiliar code or preparing a risky change, FIRE delivers a
focused, source-backed investigation of one repository subsystem or issue.

We agree up to three questions, pin the revision, trace the relevant behavior, and
run feasible checks in an isolated environment. Each question receives a supported
answer or a clear unresolved outcome with the next verification step.

You receive:

- A downloadable report with exact revision, source links, prioritized findings and practical next steps.
- Reproducible methods, evidence records and candid limits for executed checks.
- A focused behavior/change-impact map where relevant.
- A walkthrough of up to 45 minutes and one clarification round within the agreed scope.

The [public sample](samples/fire-repository-audit.md) audits FIRE's own `rest_proxy`
repository at `d2ae74c4adc6233981b16d38b26ae290e023a47b`. It verified publication
failure preservation and citation checks, while exposing an operating boundary:
stored evidence can remain valid for an older snapshot after live source changes.
The demonstration used 56 passing focused tests and a bounded indexed/file-read
evidence comparison. It is a self-audit, not a customer testimonial, and establishes
no investigation speed/token savings. See its
[evidence archive](../benchmarks/reports/2026-10-11/FIRE-Audit-Evidence.zip) and
[revision/currentness receipt](../benchmarks/reports/2026-10-11/work-routing.md).

## Access and data controls

Before work starts, confirm fit, delivery date, read-only access, permitted source
paths, processing location, any AI/provider use and retention/deletion terms.
Exclude credentials and unrelated records; use synthetic data and disposable state
for test writes. Private source is not used in public demonstrations without
permission. Production credentials are unnecessary for a source-only audit.
Do not promise physical erasure merely because logical records were deleted; agree
how backups, exports and retained artifacts will be handled.

This scope is one repository, one issue/subsystem and up to three agreed questions.
Code implementation, production deployment, hosted software/support and a comprehensive
security audit are separate scopes. The service does not guarantee defect discovery
or certify production readiness.

## Relationship to the application-workflow offer

The earlier [proposed $750 workflow investigation](paid-investigation-pilot.md)
focuses on one browser/API/persistence workflow, caps total delivery effort at eight
hours and includes a walkthrough of up to 30 minutes. It remains a separate offer;
the $900 repository audit above does not silently change those terms. Agree which
outcome and access model fits the buyer before committing to either engagement.
Track setup, investigation, review, corrections and direct costs to validate delivery
economics. Neither price is evidence of buyer interest or a measured market rate.

**Next step:** identify the repository and up to three decisions the team needs help
making; confirm scope and data handling before granting access or accepting the engagement.

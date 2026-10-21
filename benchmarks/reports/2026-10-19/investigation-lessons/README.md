# Investigation before lessons: offline acceptance

October 19, 2026. Synthetic disposable sources and ledgers only. The
[machine-readable receipt](acceptance.json) records commands, results, boundaries
and SHA-256 fingerprints of the tested sources. Its base commit identifies the
starting point; file hashes bind the implementation before this report's commit.

New lesson proposals previously required source citations but no reviewed
investigation. Version-2 proposals now bind 1–32 exact current reviewed non-lesson
records in the same ledger. Acceptance, review-packet preparation and live release
recheck those dependencies. Review packets and release bundles use version 2 and
include the exact reviewed findings.

The separate-process CLI acceptance demonstrates:

1. Save an attributed source claim, review it as disputed, and derive a proposal
   with its exact finding revision and hash.
2. Refuse release until explicit human lesson acceptance and a separate human
   release. Preserve the finding's disagreement and uncertainty in the bundle.
3. Correct and re-review the finding. Refuse packet preparation and release even
   though the accepted lesson itself has not changed. Keep old history readable.
4. Save a new lesson revision with the corrected dependency; require another
   lesson review and release. Reopen the ledger and inspect artifacts in fresh
   processes. Failed operations leave the fixture ledger and sources unchanged.

Focused tests also cover invalid/missing/unreviewed/self references, duplicate and
stale dependencies, atomic transaction snapshots, shared findings, artifact
binding and legacy histories. Version-1 lesson records remain readable; they need
an explicit version-2 save before acceptance or a new release. Negative reviews
remain available. Historical version-1 bundles remain inspectable with their
investigation prerequisite explicitly unavailable.

Validation:

- Project runtime: **190 passed, 4 optional PDF tests skipped**, 194 discovered.
- Existing bundled PDF runtime: **4 passed**, covering those four skipped cases.
- **194 distinct tests verified**, Ruff passed, diff whitespace check passed.
- Independent review: no actionable correctness or compatibility defects found;
  the 81 ledger/reader/exchange/workflow tests passed in that review.

The dedicated lessons CI workflow now includes the separate-process investigation
test. No dependency installation, service activation or model generation was needed.

These checks establish revision/citation integrity and the review prerequisite.
They do not establish that a finding is true, that an investigation is complete,
or that a lesson improves behavior. Review identities remain operator supplied;
finding reviews may be accepted, disputed or rejected. Lesson acceptance and
release require explicit human identity records, which are not authenticated.
Historical bundle inspection checks internal consistency, not live currentness.

The existing private proposals remain pending and unchanged. No production corpus
or ledger was migrated or mutated. This implementation adds neither a review web
page nor automatic downstream memory import, training or operational permissions.

See the [authoring guide](../../../../docs/lessons-corpus.md#investigate-before-proposing-lessons)
and [exchange contract](../../../../docs/free-man.md#human-review-and-controlled-exchange).

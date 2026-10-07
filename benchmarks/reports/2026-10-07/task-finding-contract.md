# Structured source-bound finding acceptance — October 7, 2026

Local dispatcher and JSON CLI now expose `submit_finding`. Finding schema 1 requires
answer (up to 4096 characters), one to three citations and up to 20 limitation
strings. Each citation supplies relative path, start/end line and lowercase SHA-256.
Unknown fields, invalid version/types, duplicates and unavailable ranges are rejected.

Dispatch rereads each citation through active-claim/capability/workspace checks,
compares captured file hash and exact range, then submits retained numbered source
with validation status. The registry bounds the whole stored record to 64 KiB.
Retained evidence survives source removal and registry reopen. A changed source
rejects submission without changing task state. Historical retained bytes do not
prove present freshness; hash checking does not validate the answer's meaning.
Multiple reads are not an atomic repository snapshot. Source can change between
validation and submission/review; reviewer must check current source when necessary.

Nine dispatch tests, twelve registry tests, two subprocess handoff tests and six
FIRE tests pass: 29 total. Three new tests cover retained source after removal,
changed-source rejection/no transition, schema/duplicate/missing-range rejection.
Ruff passes for changed Python files. No inference or external services used.

Legacy trusted `submit` remains available for compatibility and arbitrary findings;
it does not establish source binding. Use `submit_finding` for the future evidence
worker adapter. No public authentication, autonomous worker, real independent review
or tool-quality/savings claim. Next exercise this structured operation through the
subprocess handoff and introduce a bounded worker adapter only after acceptance.

## Structured subprocess handoff — subsequent October 7 acceptance

The deterministic demonstration now uses submit_finding instead of raw submit.
Nine separate CLI processes create/claim/read/checkpoint/reopen/submit/review/reopen
and reopen again after deleting the fixture source. Both unchanged-source completion
and changed-after-submission correction preserve validated numbered originals and
hashes after source deletion. [Sanitized receipt](structured-task-handoff.json).
Two strengthened handoff tests and the other 27 focused tests pass (29 total);
new Python lint and diff checks pass. No new model or external dependency.

Source changed after submission is detected by the scripted reviewer's explicit
hash comparison, not by the generic review operation. The two roles still run
under one fixture operator and do not establish independent review or authentication.
Next design a bounded optional worker adapter using this structured contract;
keep review explicit, source freshness checks separate and inference disabled by default.

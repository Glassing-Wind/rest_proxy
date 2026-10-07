# Bounded multi-source worker acceptance — October 7

Worker now accepts a primary source plus at most two additional explicit ranges.
Guarded source reads retain existing path/range/file caps. Whole encoded input stays
at most 8 KiB; result stays 16 KiB. Multi-source prompts carry evidence_bundle;
single-source evidence field remains for compatibility. Primary source is repeated
in that compatibility field and counted within the budget. Checkpoint retains all
original sources. Finding must cite every supplied range in order with exact hash;
existing dispatcher rereads and retains source snapshots. Provider schema permits
one to three citations, matching dispatcher limits. Stop-only completion stays.

CLI --additional-source accepts explicit JSON path/start_line/end_line objects;
no model-selected file access, recursive discovery, new actions or background polling.
Too many/duplicate range specifications rejected before claim. Other read/budget/
model failures preserve existing explicit recovery behavior; no automatic retry.

Checks: eight worker, twelve provider, one CLI opt-in and nine dispatcher tests pass
(30 focused checks), plus focused Ruff and diff check. New fixtures establish two
sources retained after correction/source deletion/reopen, missing citation rejection,
and source-count rejection before claim. Fixtures are scripted, not independent
semantic review or actual model multi-source acceptance.

Prior real Siri task remains queued revision 5 awaiting correction; it was not
retried in this milestone. Next choose a bounded three-file evidence set for that
live correction and review conclusions explicitly. Automatic supervisor adapter,
request preview, authenticated task interface and background lifecycle remain open.

## Correction feedback and live timeout — October 7

Workers now include the latest request_correction review reason in the same bounded
prompt. New fixture confirms feedback reaches the generator and two submissions
survive retry. Nine worker, twelve provider, nine dispatcher and one CLI tests pass
(31 focused checks), focused lint/diff pass.

One explicitly authorized three-source attempt on Siri task
1214c5b2e644470197b691e7e8e849aa hit the adapter's 20-second ReadTimeout. Registry
reopen confirms claimed revision 7, attempt 2, three checkpoint sources, original
submission and prior review retained. No corrected finding/usage counters, automatic
retry or model lifecycle change. [Evidence](siri-correction-timeout.json).
Timeout does not prove generation stopped server-side; do not overlap retries.
Next add bounded explicit request-timeout configuration and safe expired-claim
resume dispatch, then decide one controlled retry. Automatic supervisor and
background scheduling remain open; the original task is not complete.


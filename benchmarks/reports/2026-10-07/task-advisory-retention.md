# Advisory retention acceptance — October 7

## Advisory assessment retention — October 7

TaskRegistry.record_assessment and the local JSON operation now retain an 8 KiB
maximum advisory text against an existing numbered submission and expected task
revision. Status, original finding and review decisions stay unchanged. Identity is
caller-supplied/unverified; imported text is untrusted advice, never authorization.
At most ten assessments per task; existing 64 KiB task cap still applies.

Two assessment fixtures, twelve registry, two review-export and two process-handoff
checks pass (18 focused tests), plus focused lint. Reopen preserves advisory linkage;
stale revision, invalid submission and empty/oversized text reject without mutation.
No real task assessment imported; Apple shortcut output is not automatically wired.
Next previewed bundle input and assessment capture through Shortcuts, then explicit
user review. No private cloud transmission or service/model lifecycle change.

GitHub rejected normal pushes of prior local commit 175db4c with Internal Server
Error twice. Its code remains local; last confirmed hosted revision 0646c5e passed
all eight checks. Current sync result must be checked before claiming publication.


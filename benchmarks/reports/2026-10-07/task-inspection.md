# Sanitized task inspection — October 7

16 offline checks pass (inspection 2, registry 12, handoff 2), focused Ruff passes.
Fixture records timeout, advances mocked time beyond lease, then inspects status
without mutation. Output excludes goal/source/workspace/token/worker label. Process
CLI invocation verifies operation available and revision preserved. Empty history
carries an explicit incompleteness note. No live task or provider changes.

Send local JSON request to `python -m scripts.task_local --state PRIVATE_DIR`:

```json
{"operation":"inspect","arguments":{"project":"rest_proxy","task_id":"TASK_ID"}}
```

This status operation is local and unauthenticated; existing get still returns private
full task records for trusted callers. Inspect is not a new remote endpoint or a
permission boundary. Counts/status reveal task metadata; keep local state private.
Failure metadata covers instrumented generation only; no retroactive success claim.

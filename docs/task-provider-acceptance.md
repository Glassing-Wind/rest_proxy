# Local task provider merge and operational acceptance

Delivery is an opt-in, trusted-local, read-only, one-shot CLI adapter. Every finding
requires explicit human review. No remote task API, authenticated reviewer, background
scheduler or autonomous team is delivered. `task_local` returns bearer tokens and
allows raw `submit` and caller-labelled `review`; only trusted local callers may use it.
Project names and differing reviewer labels do not establish authenticated identities.
Remote/multi-user exposure requires actor authentication and project/action authorization.

## Existing live evidence

October 7 receipts in `benchmarks/reports/2026-10-07/task-output-budget.json` and
`siri-worker-review.json` establish real JSON-schema response acceptance, exact returned
model identity, provider-reported usage, retained source and registry reopen. Manual
review requested correction of a semantic error. Later correction/recovery attempts
in `siri-correction-timeout.json` and `siri-expired-recovery-timeout.json` timed out.
These receipts are historical, not current-head acceptance or a quality/savings claim.

## Disposable current-head model gate

Use a fresh private state directory and a synthetic source file, not a live task.
Record the tested commit, Python/httpx and LM Studio versions, requested and returned
model identifier, explicit budget, elapsed time and sanitized receipt. Use an already
running, approved numeric-loopback endpoint; do not start/load/reconfigure a model.
Create a read_source task with `task_local`, then run:

```sh
python -m scripts.task_run --state PRIVATE_DIR --project fixture --task-id TASK_ID \
  --revision 1 --path fixture.py --start-line 1 --end-line 2 \
  --endpoint http://127.0.0.1:1234/v1/chat/completions \
  --model EXACT_MODEL_ID --max-tokens 4096 --execute
```

Require one call, valid exact citations, matching returned identity, review_pending,
and retained finding/evidence/generation after registry reopen. Inspect the answer
against the source, record an explicit human accept or request_correction through
`task_local`, and reopen again. Missing usage must remain unavailable. Source hashes
are identity checks, not semantic approval. The operator must retain failures honestly.

## Timeout, inspection and recovery

The provider has a fixed 20-second httpx timeout; the worker CLI has a 30-second
whole-generation deadline and a 60-second claim lease. httpx timeout is an I/O timeout,
not proof of a total inference deadline or server-side cancellation. Cancel changes
local task status only. A timed-out generation may continue remotely.

Use `task_local` with operation inspect and arguments project/task_id to retrieve
sanitized status, current revision, expiry and failure summaries. Do not infer success
from an empty history. Before retrying, check the provider operator's runtime status
and establish that the previous inference has ended. If unknown, stop and investigate.
Lease expiry alone is insufficient. No automatic retries are supported.

For an expired claimed task, use the same explicit command above with the inspected
current revision and `--recover-expired 'Reviewed recovery reason'`. Reread current
source; preserve earlier checkpoints, submissions and reviews. Active claims reject.
Attempts are capped at five; history and evidence share a transactional 64 KiB cap.
For terminal cancellation use task_local cancel with project/task_id/current revision
and reason. Cancelled tasks cannot be reclaimed.

Offline gates cover preparation errors, timeout retention, stale/cancelled claims,
expired recovery, source freshness and API rejection. A disposable real-model
current-head pass remains necessary before claiming runtime acceptance. No current
runtime result is inferred from offline fixtures or historical receipts.

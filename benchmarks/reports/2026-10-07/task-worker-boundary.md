# Optional one-shot worker boundary — October 7, 2026

`memory/task_worker.py` implements an injected asynchronous generator boundary.
Disabled by default; explicit enabled=True required before claim or generation.
Claims once, reads one guarded source range, checkpoints original evidence, calls
generator once, validates the structured finding and enters review_pending.
Only the supplied path/range/hash may be cited. No automatic review or completion.

Input capped at 8 KiB, output at 16 KiB, timeout 1–120 seconds. Generator sees goal,
bounded evidence and instructions, not claim tokens, private state path or tools.
Timeout/failure leaves a claimed task and existing checkpoint for explicit recovery;
no auto retry. Source changes/cancellation while generating reject submission.
No model provider contacted, dependency installed or operational lifecycle changed.

Five injected-generator tests and prior 29 focused tests pass (34 total). New tests
cover disabled/no claim/no call, one-call evidence retention/review pending, invented
citation rejection, cancellation during generation and timeout/checkpoint recovery.
Ruff and diff checks pass. Fake generator results do not establish Qwen compatibility,
real model quality, measured usage, token savings or independent review.

This is a trusted local callable, not a sandbox. Injected Python functions can access
their host process independently; no-tools prompt is not enforcement of arbitrary
Python. Async timeout requires cooperative generation and does not kill blocking code
or guarantee remote cancellation. Byte bounds are not tokenizer/provider budgeting.
Next implement an optional loopback provider adapter with explicit endpoint/model,
request/response constraints and recorded actual usage, tested with local HTTP fixtures
before any authorized real-model trial. Authentication, lease renewal/retention,
MCP/REST task exposure and cross-chat synchronization remain open.

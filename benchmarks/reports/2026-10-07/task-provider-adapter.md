# Optional loopback provider adapter — October 7, 2026

Work begins from merged main on codex/task-provider-adapter. PRs #2 and #3 were
merged after PR checks passed; this milestone is not authorized for automatic merge.

LocalTaskProvider is a callable for the opt-in worker. It accepts an explicit model
and numeric loopback HTTP /v1/chat/completions endpoint, disabled by default. One
nonstreaming JSON request, max_tokens 1024, no tools, 20-second HTTP timeout,
no environment proxies or redirects. Input 8 KiB, HTTP response 64 KiB, content
16 KiB. Requires stop finish reason; rejects tools/truncation/redirects. Existing
worker validates finding schema and supplied citations before review_pending.

Three HTTP fixture tests pass: end-to-end registry/worker/provider/retained finding,
disabled/no request and endpoint rejection, truncation/redirect/oversize rejection.
Provider-reported prompt/completion counts retained in adapter receipt when valid;
missing counts remain unavailable. Fixture counts are synthetic, not measured model
usage. Worker summary still says usage_measured=False and does not persist the
adapter receipt into task state yet. No claim of tokenizer budget attestation.

No LM Studio/model discovery/load, server changes, real inference or remote code
submission. No new dependency. Numeric loopback is not server identity verification;
operator must know which local service owns the endpoint. Returned model identity
is recorded but not enforced. Next expand error/model/usage acceptance and persist
usage provenance before a real opt-in trial. Review still requires separate action;
no persistent scheduler, authenticated task API or automatic chat coordination.

## Provider identity and malformed response guards — subsequent acceptance

Returned model must now exactly match the requested explicit model; missing identity
and aliases are rejected rather than silently accepted. Require exactly one choice,
a message object and valid JSON object content. Tool-call messages remain rejected.
Five provider fixtures and five worker tests pass; lint/diff checks pass. Two new
fixtures exercise missing/wrong model and empty choice/null message/tool-call/non-JSON
failures without creating a successful usage receipt. No live provider contacted.
Alias mapping is not configured. Next persist validated usage provenance and cover
missing/invalid counters and HTTP timeouts before real-model acceptance.

## Durable generation provenance — subsequent October 7 acceptance

Worker now attaches sanitized adapter provenance to the submission separately from
model-authored findings: requested/returned model, one request, tools disabled and
provider-reported prompt/completion counters. Record survives registry reopen in
HTTP fixture test. Missing/invalid/boolean/negative counters remain unavailable.
No claim of independently measured usage; worker usage_measured remains false.
Trusted local raw submit accepts optional provenance, not authenticated attestation.

Seven provider tests plus 34 task/FIRE regressions pass (41 total), including two
new invalid/missing usage and synthetic HTTP timeout cases. Lint/diff checks pass.
The prior f19cb89 PR revision's eight hosted checks passed before this update;
new revision requires its own CI. Timeout leaves no successful adapter receipt.
No real provider contacted, model loaded or lifecycle changed. Next run an opt-in
trial only against an explicitly identified already-running local model, preserving
operator control; real model identity/JSON compliance and costs remain unverified.

## Already-running provider attempt — October 7

Read-only /api/v1/models confirmed qwen3.6-35b-a3b-splash already loaded at
127.0.0.1:1234. No load/unload/start/stop request was made. A disposable synthetic
two-line function task attempted the opt-in adapter. Chat endpoint returned HTTP
400 before successful generation; active claim/checkpoint remained and no submission
or usage receipt was recorded. [Initial receipt](task-live-provider-attempt.json).

A second diagnostic attempt also returned 400; trying to inspect its streaming
error text raised ResponseNotRead because the response was not consumed before
raise_for_status. That attempt produced no replacement receipt. No further retries.
Exact server rejection reason is not established. JSON-mode compatibility is a
candidate, not a verified cause. Synthetic source only; no rental or customer code.
Elapsed time in the initial failure receipt is not successful inference latency.

PR #4 revision d7633e2 has all eight hosted checks passing. Live-model acceptance
remains blocked on the 400. Next implement bounded error-body consumption with a
fixture proving safe diagnostics, then verify supported structured response format
before another live trial. Do not load a different model or silently relax the
validated finding contract. No merge or external publication performed.

## Bounded rejection diagnostics — subsequent October 7 acceptance

ProviderHTTPError now captures at most 4096 response bytes before raising. Generic
exception text includes status only; raw diagnostic text is a separate untrusted
attribute, not logged or persisted by the worker. Oversized diagnostics truncate
and close the stream. Two new fixtures verify bounded/private exception text and
small JSON diagnostics without ResponseNotRead. Nine provider and five worker tests
pass, plus lint/diff checks. Diagnostic data may contain sensitive server text;
callers must inspect deliberately, not blindly publish it or treat it as instructions.

One synthetic diagnostic trial against the same already-loaded Qwen confirmed the
400 reason: `'response_format.type' must be 'json_schema' or 'text'`. The adapter
currently requests json_object. [Receipt](task-provider-rejection-detail.json) contains
only synthetic trial status and that error. Checkpoint retained, no successful model
usage; no lifecycle requests. Exact response-format incompatibility is established.

Next implement bounded explicit json_schema output with HTTP fixture acceptance,
then retry the synthetic task once. Keep model/citation constraints; do not relax
to arbitrary text or automatically retry failure. PR revision 067a06e's eight hosted
checks passed before this update; current revision needs its own CI. No merge.

## Explicit JSON-schema request — subsequent October 7 acceptance

Replaced rejected json_object mode with strict json_schema task_finding, following
[official structured-output documentation](https://lmstudio.ai/docs/developer/openai-compat/structured-output).
Schema bounds answer/limitations, forbids extra object fields and requires one cited
path/range/hash. Local dispatch continues independent source/schema checks; provider
format promises do not replace validation. Existing HTTP fixture now asserts exact
response format, no extra properties and one citation. Nine provider tests and lint
pass; full worker/dispatch/registry/handoff/FIRE regression rerun below.

One planned retry against already-loaded Qwen ran for 6.169 seconds and failed local
validation with ValueError rather than HTTPStatusError. The receipt does not capture
the precise failed validation stage; do not infer truncation, model identity failure
or malformed JSON from that alone. Checkpoint survived; no successful submission or
usage receipt. [Attempt receipt](task-json-schema-attempt.json). No further retry or
model/service lifecycle change. Next retain bounded validation-stage diagnostics to
identify this new failure before another trial; live acceptance remains incomplete.

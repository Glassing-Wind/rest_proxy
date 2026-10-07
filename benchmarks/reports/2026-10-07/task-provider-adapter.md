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

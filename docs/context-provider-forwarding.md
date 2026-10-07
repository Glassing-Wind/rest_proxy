# Opt-in LM Studio context forwarding

Implemented October 6, 2026. The existing inference route and provider selection
remain unchanged. Enable `LM_PROXY_CONTEXT_FORWARD_ENABLED=1` before proxy startup
and use `POST /v1/context/chat/completions`. The current provider must be `lmstudio`.

The proxy requests the shared bundle from the owning HTTP daemon at
`LM_PROXY_CONTEXT_OWNER_URL` (default `http://127.0.0.1:8001/evidence/read`). Enable
that daemon's context and embedded REST flags; FIRE additionally needs its configured
state store. Both owner and LM Studio endpoints must be HTTP loopback URLs with an
explicit port. This slice uses the daemon's existing access boundary.

Example body:

```json
{
  "model": "qwen3.6-35b-a3b-splash",
  "fire": true,
  "expected_revision": 1,
  "context_request": {
    "scope": {"project": "example", "session": "session", "task": "investigation"},
    "goal": "Explain helper's return value",
    "instructions": "Cite the supporting source.",
    "messages": [{"role": "user", "content": "What does helper return?"}],
    "tools": [],
    "context_capacity": 8192,
    "output_reserve": 128,
    "framing_reserve": 256,
    "safety_margin": 256
  }
}
```

Omit `fire` for supplied-evidence assembly. Recovery options require FIRE selection;
required FIRE snapshots must be current and unexpired. Source freshness is caller
supplied through `current_hashes`; recovered originals remain historical evidence.
See [bundle contracts](context-bundle-contract.md) and
[FIRE adaptation](fire-context-bundles.md) for scope and evidence requirements.

Instructions become a system message, the goal becomes a labeled user message,
and preserved messages include the labeled evidence bundle. The canonical serialized
provider body is counted again, including model, tools and generation options.
Output reserve must be 1–4096. Tools require OpenAI function schemas; this route
does not execute them. It requests deterministic, non-streaming generation with
`reasoning_effort: none`. Provider compatibility is limited to the tested model.

The optional local tokenizer artifact uses `LM_PROXY_CONTEXT_TOKENIZER_FILE`, with
its SHA-256 identity recorded; otherwise counting uses UTF-8 bytes. Padding and
truncation are disabled. Neither method attests the provider's applied chat template.
The formatted count plus output/framing/safety reserves must fit declared capacity.
The declaration must also fit the loaded model's reported context length.

Before inference, `/api/v1/models` must report exactly one matching loaded LLM
instance. No model lifecycle endpoint is called. Optional `LMSTUDIO_API_KEY` is
used for both local model discovery and completion; cloud credentials are not reused.
A concurrent model lifecycle change between preflight and inference remains a race;
this guard does not lock LM Studio's model lifecycle.

Native completion output includes `x_context` with selected IDs, omissions,
recovery revision, formatting accounting and actual provider usage. Usage checks
are observations after inference. Stateless requests only: provider continuation
and hidden provider context are rejected. This route bypasses legacy memory/history
injection; callers must supply their intended history explicitly.

## Acceptance and remaining work

Four focused script tests and the full local CI gate pass. A disposable FIRE fixture
used the actual proxy ASGI route, a real loopback owner daemon and real LM Studio
Qwen inference. It returned the expected source-cited answer in 1.212 seconds,
with 807 prompt tokens and 15 completion tokens. Serialized byte accounting was
2,194; with reserves it was 2,834 against declared capacity 8,192. Loaded model
instances were unchanged. Operational services and indexes were not reconfigured.
[Acceptance receipt](../benchmarks/reports/2026-10-06/context-provider-forwarding.json).

This is one synthetic functional case, not a paired coding-outcome or token-savings
measurement. Exact model/tokenizer/chat-template attestation, streaming, provider
continuation, automatic retrieval/freshness, other providers and deployed TCP proxy
acceptance remain open. Next validation should exercise controlled coding tasks
with source grading, actual usage, latency and native fallback counts.

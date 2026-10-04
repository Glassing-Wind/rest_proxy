# OpenAI inference with GPT-6 Sol

The opt-in OpenAI provider uses `gpt-6-sol` with low reasoning through `/v1/responses`.
The public interface remains `/v1/chat/completions`. LM Studio remains the default
provider. `/v1/embeddings`, local model administration, indexing, and the optional
summarizer retain their existing local configuration.

## Activate a canary

Supply `OPENAI_API_KEY` through your private environment or `.env` (never commit it).
Then configure:

```dotenv
LM_PROXY_PROVIDER=openai
LM_PROXY_OPENAI_MODEL=gpt-6-sol
LM_PROXY_OPENAI_REASONING_EFFORT=low
```

Start a separate canary process using the project's Python environment:

```bash
uvicorn proxy:app --host 127.0.0.1 --port 8001
```

Set the client's base URL to `http://127.0.0.1:8001/v1` and its model to
`gpt-6-sol`. Explicit client model IDs are respected; the configured model applies
when the caller omits `model`. Existing `LM_PROXY_MODEL_ALIASES` mappings also apply.
Cloud routing never silently falls back to a local model or Chat Completions.
OpenAI credentials are sent only to `LM_PROXY_OPENAI_BASE_URL` (default
`https://api.openai.com/v1`), not to local embeddings or administration routes.
`/v1/models` performs authenticated OpenAI discovery. A missing key returns HTTP 503.

```bash
curl --fail-with-body http://127.0.0.1:8001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-6-sol","messages":[{"role":"user","content":"Reply with ready."}],"max_completion_tokens":512}'
```

## Compatibility and state

- Function schemas, explicit strictness, named tool choice, multiple call IDs,
  structured output formats, output-token limits, refusals, and reasoning effort
  are translated to Responses. Non-strict Chat function schemas remain non-strict.
- Reasoning defaults to `low`; explicit supported efforts are preserved. `minimal`
  maps to `low`. Astra's `none` maps to `low`. Sampling/logprob fields are omitted
  for reasoning requests; Sol/Luna at `none` may forward sampling parameters.
- Every request sends the complete supplied transcript. Clients must continue
  sending full Chat history, including all tool calls and tool results.
  `previous_response_id` is intentionally rejected at this compatibility boundary.
- `store` defaults to `false`. The adapter requests encrypted reasoning and keeps
  typed response output in a bounded, process-local cache: 64 entries, at most
  256 KB each, with a 30-minute TTL. Keys include upstream URL, model, explicit
  session ID when present, and normalized history. Nothing is written to the
  legacy LM Studio response-ID file. Restart/eviction replays the caller's full
  transcript without cached reasoning. Supplying a unique `session_id` per
  conversation is recommended. This is a single-user proxy, not a tenant auth layer.
- Streaming maps Responses events to Chat SSE with stable upstream tool call IDs,
  contiguous tool indexes, correct finish reasons, and optional usage chunks
  (`stream_options.include_usage`). Failed or prematurely terminated streams emit
  an error instead of a successful finish. No retry happens after emitted output.
- Tool result compaction respects `LM_PROXY_MAX_TOOL_MESSAGE_CHARS` (default
  200000; nonpositive disables compaction). Function JSON schemas are not compacted.
- Optional memory injection adds bounded context without deleting tool history.
  Retrieval has a two-second budget. Persistence runs in the background with a
  ten-second budget and cannot turn a successful completion into a failure.
- This adapter supports text, image input, and function tools. Audio, legacy
  `functions`/`function_call`, native Responses continuation, and `n > 1` are rejected.

## Optional proxy-owned codebase search

Client-supplied tools (including MCP tools exposed by the client) are returned to
the client for execution. The OpenAI path does not inject a tool the client cannot
execute by default.

To let the proxy execute its own `codebase_search`, set
`LM_PROXY_OPENAI_CODEBASE_SEARCH=1` and supply the indexed `project_id` in each
Chat request. Local memory retrieval and store modules must be available. A
client-supplied function with that name always retains client ownership.

Private searches use up to five model turns, with a 15-second timeout per search
and bounded results. Optional service failures become unavailable-search results.
If private search is enabled, streaming is buffered until the tool loop produces
an answer or client-owned calls; private tool calls never appear in client SSE.
Usage includes every model turn in that loop. Mixed private/public calls execute
private work once and return public calls; cached typed output preserves the
private results for the client's next turn.

## Validation and rollout

Offline contracts require no cloud key or running databases:

```bash
python test_openai_provider.py
python test_memory_mode.py
python test_handlers_persistence.py
python -m py_compile proxy/app.py proxy/openai_provider.py mcp_server.py brain_server.py
```

The new contract suite is also included in `scripts/run_ci_checks.sh`.
Before directing production traffic to the canary, use a fixed sample of repository
investigations and retrieval questions against both the existing local model and
Sol. Include a streamed two-tool round trip, long history, structured JSON output,
missing optional services, and a model-access/rate-limit failure. Record task
success, tool argument validity, time to first output, total latency, input/output
and reasoning tokens, cached tokens, and cost per successful task. Debug usage
telemetry is available through `LM_PROXY_DEBUG`; avoid enabling existing verbose
request logging on sensitive workloads. Gate promotion on no protocol regressions
and quality at least matching the baseline, within the deployment's latency and
spend budgets. Live quality, cost, latency, and model access are not established by
the offline suite.

Rollback: set `LM_PROXY_PROVIDER=lmstudio`, restore the client's local model ID
(or its original aliases), and restart the proxy. The embedding index needs no
migration or rebuild. Astra and Luna remain explicit model choices for later
workload evaluations; the optional local summarizer is unchanged.

## Official references

- [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol)
- [GPT-6 migration](https://developers.openai.com/api/docs/guides/latest-model#migration-quickstart)
- [Responses migration and conversation state](https://developers.openai.com/api/docs/guides/migrate-to-responses)

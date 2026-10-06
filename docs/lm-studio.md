# LM Studio integration

Official documentation checked October 5, 2026. This guide separates LM Studio
capabilities from our current implementation.

LM Studio is an optional embedding adapter and a separate optional inference proxy
backend. Our Ladybug/LanceDB owner accepts an embedding callback; selecting LM
Studio for embeddings does not require selecting it for inference. See
[embedding provider direction](embedding-provider-direction.md) for evaluation gates.

## API surfaces

Our provider requires native v1 model-management endpoints, introduced in LM Studio
0.4.0, and uses the OpenAI-compatible embeddings endpoint. Older `/api/v0/*` routes
are not its contract. [Official REST overview](https://lmstudio.ai/docs/developer/rest).

| Endpoint | Repository use |
| --- | --- |
| `GET /api/v1/models` | Discover embedding models and loaded instances. |
| `POST /api/v1/models/load` | General provider auto-load, when enabled. |
| `POST /api/v1/models/unload` | Explicit general provider lifecycle operation. |
| `POST /v1/embeddings` | Generate vectors. |
| `/v1/chat/completions`, `/v1/responses` | Separate optional inference proxy paths. |

The native model list supplies `key`, `type`, and `loaded_instances` with instance
configuration. Use it to verify readiness. With JIT loading enabled, `/v1/models`
can list downloaded models that are not loaded. [Native model list](https://lmstudio.ai/docs/developer/rest/list),
[JIT behavior](https://lmstudio.ai/docs/developer/core/headless).

## Start and verify

With LM Studio and `lms` installed, enable the server in the Developer tab or run:

```bash
lms server start --port 1234 --bind 127.0.0.1
lms server status
curl --fail http://127.0.0.1:1234/api/v1/models
curl --fail http://127.0.0.1:1234/v1/models
```

The explicit port avoids inheriting a previously used port. Our HTTP MCP owner,
normally on port 8001, is a separate service.
[Server CLI reference](https://lmstudio.ai/docs/cli/serve/server-start).

After loading the intended embedding model, a small functional probe is:

```bash
curl --fail http://127.0.0.1:1234/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"text-embedding-jina-embeddings-v2-base-code","input":["FIRE repository embedding probe"]}'
```

Use the identifier from your server's model list. The probe sends sample text and
can trigger server-side JIT loading if enabled. A vector response establishes basic
operation, not retrieval quality.
[Official embeddings reference](https://lmstudio.ai/docs/developer/openai-compat/embeddings).

## Configuration and batching

These values match `LMStudioConfig.from_env()` defaults. They are repository
defaults, not a measured optimum for every model or Apple Silicon machine.

```dotenv
LMSTUDIO_BASE_URL=http://127.0.0.1:1234
LMSTUDIO_EMBED_MODEL=text-embedding-jina-embeddings-v2-base-code
LMSTUDIO_CONTEXT_LENGTH=2048
LMSTUDIO_EVAL_BATCH_SIZE=512
LMSTUDIO_EMBED_TIMEOUT_S=120
LMSTUDIO_AUTO_LOAD=true
LMSTUDIO_MAX_BATCH_SIZE=64
LMSTUDIO_MAX_BATCH_TOKENS=131072
LM_EMBED_CONCURRENCY=4
LMSTUDIO_INPUT_TOKEN_MARGIN=0.9
LMSTUDIO_ESTIMATED_CHARS_PER_TOKEN=3
```

`LMSTUDIO_BASE_URL` is a server root without `/v1`; it falls back to
`LM_PROXY_MEMORY_EMBEDDING_BASE_URL`, then `LM_BASE`. The model falls back to
`LM_PROXY_MEMORY_EMBEDDING_MODEL`; request batch size falls back to
`LM_EMBED_BATCH_SIZE`. Without an explicit token batch limit, the provider derives
it from context length multiplied by maximum request batch size.

Request batch size counts texts. The token budget and characters-per-token estimate
control client batching/input normalization; they are not exact tokenizer counts.
Context length bounds each input. Strict embedded indexing rejects text altered by
provider normalization.

LM Studio documents `eval_batch_size` as effective only for LLMs using its
llama.cpp engine; embedding load configuration contains `context_length`.
Our general provider still sends this legacy setting and retries without it when
the error explicitly identifies embedding-model rejection of that field. It is
not a documented embedding throughput control.
[Official load schema](https://lmstudio.ai/docs/developer/rest/load).

Benchmark an already loaded model with client auto-load disabled:

```bash
LMSTUDIO_AUTO_LOAD=false .venv/bin/python scripts/benchmark_lmstudio_embeddings.py \
  --batch-sizes 16,32,64,128,256
```

The script uses a repeated sample corpus; `--input-file` accepts texts separated
by a line containing `---`. Requested batches remain subject to the provider's
token budget. Compare representative chunk lengths and record model/runtime,
hardware, concurrency and cold/warm conditions. This script does not establish
better coding outcomes or a preferred provider.

## Strict embedded indexing and watching

`StrictLMStudioEncoder` forces client auto-load off and accepts loopback endpoints
only. It requires the configured embedding model to be already loaded with exactly
one instance and a context length at least as large as the configured input budget.
It checks instance/configuration stability and validates vectors. It does not
load, unload or download models and has no synthetic fallback.

The embedded runtime uses `LM_PROXY_EMBEDDED_MODEL_ARTIFACT` to fingerprint a local
model file and requires `LM_PROXY_MEMORY_EMBEDDING_DIM` to match vector dimensions
(default 768). A file fingerprint does not attest resident server weights or runtime
version. See [real-model acceptance](real-embedding-acceptance.md) and
[embedded MCP owner setup](embedded-mcp-owner.md).

Server-side JIT, idle TTL and auto-eviction settings are independent of
`LMSTUDIO_AUTO_LOAD`. CLI-loaded models have no TTL by default; JIT-loaded models
can expire while idle. Keep the intended instance available and inspect its
configuration after restarting or reloading. The strict adapter rejects unloaded
or changed instances instead of silently reloading them.
[Lifecycle settings](https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict).

Source, text and metadata reads do not require a model. Indexing, vector/hybrid
search, watch readiness and changed-file dispatch require the configured encoder.
Watching remains opt-in: use [Watch this project setup](embedded-watch-setup.md)
to check readiness and [refresh and watching](embedded-refresh-and-watching.md)
for dispatch limits and failure recovery.

## Authentication and deployment

LM Studio supports API tokens from 0.4.0 onward. Authentication is disabled by
default; enabling it requires a valid bearer token on requests.
[Official authentication guide](https://lmstudio.ai/docs/developer/core/authentication).

`LMStudioEmbeddingProvider` now reads optional `LMSTUDIO_API_KEY` when the provider
is constructed and sends it as a bearer token on both native lifecycle and embedding
requests. Empty/unset values preserve unauthenticated operation. `OPENAI_API_KEY`
does not authenticate this client. Recreate the provider after rotating the token;
changing the environment does not update an existing provider. Supply credentials
through your environment/secret management, never checked-in configuration.

Credentials stay outside the config dataclass, strict encoder descriptor and encoder
identity. Returned HTTP error text redacts exact occurrences of the configured token.
Offline transport tests verify the header on all four endpoint paths, no-token
behavior, error redaction and stable encoder identity across credential changes.
The shared LM Studio server's authentication settings were not changed; live
authentication enforcement remains a deployment check. The strict adapter remains
loopback-only. See [the worker trial and supervisor review](../benchmarks/reports/2026-10-05/qwen-worker-trial.md).

LM Studio offers the standalone `llmster` daemon and desktop background operation.
Docker is not required for this integration. Follow the
[official headless guide](https://lmstudio.ai/docs/developer/core/headless) when
choosing that deployment; it does not establish our adapter's authenticated or
remote deployment readiness.

The optional inference proxy uses `LM_BASE`. Its
`LM_PROXY_USE_RESPONSES_API=1` switch routes tool-using requests through
`/v1/responses`; it does not enable embeddings, watching or durable FIRE memory.
Stateful server chat remains bounded by model context and is distinct from
persistent evidence retrieval.

## Splash, Qwen and Harmony

Splash is an inference engine, Qwen is the model family, and Harmony is the
conversation format used by OpenAI `gpt-oss`. They occupy different layers.
LM Studio's September 18 announcement describes Splash support for
Qwen3.6-35B-A3B and Qwen3.8-27B, requiring Bionic 1.1.5+, M3+ hardware,
macOS 26.4+ and at least 36 GB unified memory (48 GB recommended).
[Official Splash integration](https://lmstudio.ai/blog/splash-engine).

Inco documents standalone Splash Chat Completions, Responses and Messages APIs,
including streaming and tool calls. Its standalone default port 8000 overlaps our
usual inference-proxy port; use distinct ports if evaluating that deployment.
Standalone API support is not a completed test of the LM Studio backend through
our proxy. [Inco engine documentation](https://inco.ai/blog/splash/).

LM Studio supported `gpt-oss` before Splash was introduced. Installing Splash does
not establish that Splash runs `gpt-oss`; a compatible server handles Harmony
formatting. [LM Studio gpt-oss integration](https://lmstudio.ai/blog/gpt-oss),
[OpenAI Harmony reference](https://developers.openai.com/cookbook/articles/openai-harmony).

See [cockpit worker/supervisor direction](clarity-cockpit-direction.md) for the
proposed bounded local-worker role, disclosure controls and evaluation requirements.
No automatic delegation or model replacement is enabled by this documentation.

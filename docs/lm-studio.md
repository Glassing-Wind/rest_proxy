# LM Studio Embeddings

The local embedding integration intentionally uses two LM Studio API surfaces:

- `/api/v1/*` for model discovery, load, and unload lifecycle.
- `/v1/embeddings` for OpenAI-compatible embedding requests.

## Configuration

```dotenv
LMSTUDIO_BASE_URL=http://127.0.0.1:1234
LMSTUDIO_EMBED_MODEL=text-embedding-jina-embeddings-v2-base-code
LMSTUDIO_CONTEXT_LENGTH=2048
LMSTUDIO_EVAL_BATCH_SIZE=512
LMSTUDIO_EMBED_TIMEOUT_S=120
LMSTUDIO_AUTO_LOAD=true
LMSTUDIO_MAX_BATCH_SIZE=256
LMSTUDIO_MAX_BATCH_TOKENS=524288
LM_EMBED_CONCURRENCY=4
```

Older `LM_PROXY_MEMORY_EMBEDDING_*` names remain supported. Prefer the
`LMSTUDIO_*` names for new configuration.

## Start and verify

Enable the developer server in the LM Studio application or run:

```bash
lms server start
curl http://127.0.0.1:1234/api/v1/models
curl http://127.0.0.1:1234/v1/models
```

Benchmark the configured provider with:

```bash
python scripts/benchmark_lmstudio_embeddings.py
```

For Apple Silicon, start with a 2,048-token context, evaluation batch size 512,
embedding concurrency 4, and request batches of 64–256. Reduce request batch
size for longer chunks before increasing timeouts. Always benchmark model and
batch changes on the target machine.

Some runtimes reject `eval_batch_size` during model load. The provider retries
with `context_length` only so that this incompatibility does not fail the whole
embedding path.

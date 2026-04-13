# Memory-Aware Agent Gateway

This document describes the memory layer added to the LM Studio proxy (`proxy.py`). All features are **optional** and gated by environment variables. If any dependency (Postgres, Redis) is unavailable, the proxy continues to work normally. Memory errors are logged but never break the response path.

---

## Architecture

```
proxy.py  (unchanged routes)
│
├── memory_types.py      – Dataclasses: ConversationTurn, MemorySummary, ToolOutput, etc.
├── memory_summary.py    – Summarization (LLM or deterministic) + tool-output compaction
├── memory_store.py      – Redis hot-state + Postgres durable storage
├── memory_retrieval.py  – Embedding provider interface + memory assembly
└── memory_bootstrap.py  – Neo4j GraphRAG initialization (run at startup)
```

### Data Flow

```
POST /v1/chat/completions
       │
       ▼ (LM Studio / OpenAI response)
       │
       ├─ non-stream path ──► _persist_memory_best_effort()  (asyncio.ensure_future)
       │
       └─ stream path ──────► _persist_memory_best_effort()  (asyncio.ensure_future, after full stream)
```

`_persist_memory_best_effort` does:
1. Appends user + assistant turns to Redis recent-turns list.
2. Updates rolling summary (via LLM if configured, else deterministic).
3. Inserts durable `conversation_turns` + `memory_summaries` rows into Postgres.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LM_PROXY_MEMORY_ENABLED` | `0` | Master switch – set to `1` to allow proxy memory features |
| `LM_PROXY_MEMORY_MODE` | `stateless` | `stateless/off` disables automatic rolling memory, `assist` enables bounded recent-turn + summary help for smaller/local models, `full` also enables broader retrieval features |
| `LM_PROXY_NEO4J_URI` | `bolt://127.0.0.1:7687` | Neo4j Bolt connection URI |
| `LM_PROXY_NEO4J_USER` | `neo4j` | Neo4j username |
| `LM_PROXY_NEO4J_PASSWORD` | - | Neo4j password |
| `LM_PROXY_MEMORY_ENABLE_PERSISTENCE` | `0` | Enable Neo4j durable persistence |
| `LM_PROXY_MEMORY_ENABLE_REDIS` | `0` | Enable Redis hot session state |
| `LM_PROXY_MEMORY_ENABLE_EMBEDDINGS` | `0` | Enable embedding + pgvector retrieval |
| `LM_PROXY_MEMORY_ENABLE_RETRIEVAL` | `0` | Enable vector similarity retrieval |
| `LM_PROXY_PG_DSN` | _(empty)_ | Postgres connection string (psycopg v3 format) |
| `LM_PROXY_REDIS_URL` | `redis://localhost:6379/0` | Redis URL |
| `LM_PROXY_MEMORY_SESSION_NAMESPACE` | `lmproxy` | Redis key namespace |
| `LM_PROXY_MEMORY_MAX_RECENT_TURNS` | `20` | Max turns kept in Redis list |
| `LM_PROXY_MEMORY_RETRIEVAL_K` | `4` | Number of similar snippets to retrieve |
| `LM_PROXY_MEMORY_TOOL_RAW_MAX_CHARS` | `8000` | Max chars stored for raw tool output |
| `LM_PROXY_MEMORY_TOOL_SUMMARY_MAX_CHARS` | `400` | Max chars for compact tool output summary |
| `LM_PROXY_MEMORY_SUMMARIZER_MODEL` | _(empty)_ | Model for LLM rolling summary (optional) |
| `LM_PROXY_MEMORY_SUMMARIZER_BASE_URL` | same as `LM_BASE` | Base URL for summarizer LLM |
| `LM_PROXY_MEMORY_EMBEDDING_MODEL` | _(empty)_ | Model for embeddings (optional) |
| `LM_PROXY_MEMORY_EMBEDDING_BASE_URL` | same as `LM_BASE` | Base URL for embedding endpoint |
| `LM_PROXY_MEMORY_EMBEDDING_DIM` | `768` | Embedding vector dimension for pgvector column |

---

## Postgres Schema

Five tables are created idempotently on startup via `memory_bootstrap.py`:

| Table | Purpose |
|---|---|
| `conversation_turns` | Every user + assistant turn, with a compact column for retrieval |
| `memory_summaries` | Rolling / checkpoint summaries |
| `tool_outputs` | Raw + compact tool output records |
| `memory_checkpoints` | Structured working memory snapshots |
| `memory_embeddings` | pgvector embeddings for semantic retrieval |

**pgvector** is required only if `LM_PROXY_MEMORY_ENABLE_EMBEDDINGS=1`. The bootstrap automatically runs `CREATE EXTENSION IF NOT EXISTS vector`.

### Minimal Postgres Setup

```bash
# Create the database (example: local socket auth)
createdb lm_proxy_memory

# Or with a full DSN
psql postgres -c "CREATE DATABASE lm_proxy_memory;"
```

Then set `LM_PROXY_PG_DSN=postgresql:///lm_proxy_memory` (or a full connection string).

---

## Redis Schema

Keys are namespaced as `{namespace}:{session_id}:{suffix}`:

| Suffix | Type | Content |
|---|---|---|
| `:state` | String (JSON) | Structured working memory dict |
| `:summary` | String | Rolling summary text |
| `:turns` | List | Recent turn JSON objects (capped) |

All keys have a 7-day TTL.

---

## Session ID Strategy

Derived deterministically per request:
1. **Explicit**: `session_id` or `x_session_id` field in the request body.
2. **Derived**: SHA-256 hash of the system prompt + first user message (first 16 hex chars).  
   Conversations that restart from the same system prompt naturally share memory.

---

## Failure Modes

| Failure | Behavior |
|---|---|
| Redis unavailable | Turns/summary silently skipped; proxy continues normally |
| Postgres unavailable | Durable inserts silently skipped; proxy continues normally |
| pgvector not installed | Embedding table creation skipped; retrieval disabled |
| Memory modules missing | Import error logged; proxy runs with `_MEMORY_ENABLED` effectively off |
| Any memory error | Caught and logged via `debug_log`; never propagates to response |

---

## Structured Working Memory (Future)

`memory_types._empty_working_memory()` defines the JSON schema stored in Redis:

```json
{
  "goal": "",
  "current_focus": "",
  "files_touched": [],
  "recent_errors": [],
  "decisions": [],
  "open_issues": [],
  "next_actions": []
}
```

Clients can write this state via `memory_store.set_session_state()`. The assembly helper in `memory_retrieval.assemble_memory()` renders it into compact text for future prompt injection.

---

## Memory Assembly (Prompt Augmentation)

`memory_retrieval.assemble_memory(session_id, query_text)` returns an `AssembledMemory` object with:
- `rolling_summary` – concise session history
- `working_memory` – structured state dict
- `recent_turns` – last N turns from Redis
- `retrieved_snippets` – top-K pgvector hits (if embeddings enabled)
- `assembled_text` – pre-formatted block for prompt injection

The proxy now treats automatic prompt memory as mode-driven:
- `stateless` / `off`: no automatic rolling-memory persistence or injection
- `assist`: bounded recent-turn + summary persistence/injection for small local models
- `full`: broader retrieval behavior, including embedding-backed memory when enabled

For capable stateful clients, `stateless` is the safe default. `assist` is the intended opt-in mode when the model benefits from compact rolling memory.

---

## Setup & Run

```bash
# 1. Install dependencies
conda activate lmproxy
pip install -r requirements.txt

# 2. Ensure .env is configured (example values already in .env)
# LM_PROXY_MEMORY_ENABLED=1
# LM_PROXY_MEMORY_ENABLE_PERSISTENCE=1
# LM_PROXY_MEMORY_ENABLE_REDIS=1
# LM_PROXY_PG_DSN=postgresql:///lm_proxy_memory
# LM_PROXY_REDIS_URL=redis://localhost:6379/0

# 3. Create Postgres DB (if not already done)
createdb lm_proxy_memory

# 4. Start proxy (schema bootstrap runs automatically at startup)
uvicorn proxy:app --host 0.0.0.0 --port 8000

# Optional: enable debug logging to see memory events
# LM_PROXY_DEBUG=1
```

---

## Tool Output Compaction

Tool outputs (role=`tool` messages) are the highest-priority content to compact.

- Raw output stored in Postgres (capped at `LM_PROXY_MEMORY_TOOL_RAW_MAX_CHARS`)
- Compact summary generated immediately (max `LM_PROXY_MEMORY_TOOL_SUMMARY_MAX_CHARS`)  
- Only compact summaries appear in retrieved memory snippets; raw tool output never re-enters prompts

The existing proxy-side `filter_messages_for_proxy()` already truncates tool messages in live prompts via `LM_PROXY_MAX_TOOL_MESSAGE_CHARS`. The memory layer adds a second, more aggressive compaction layer for durable storage.

---

## Adding Embeddings

1. Set `LM_PROXY_MEMORY_ENABLE_EMBEDDINGS=1`
2. Set `LM_PROXY_MEMORY_EMBEDDING_MODEL=<your-embedding-model-key>`
3. Ensure pgvector is installed: `psql lm_proxy_memory -c "CREATE EXTENSION vector;"`
4. Set `LM_PROXY_MEMORY_EMBEDDING_DIM` to match your model's output dimension
5. Restart proxy – bootstrap will create the `memory_embeddings` table and HNSW index

---

## Limitations (v1)

- No automatic working memory updates from assistant content (requires future extraction logic).
- Rolling summary checkpointed to Postgres on every turn (minor write amplification).
- Prompt injection of assembled memory is not wired by default (assembly exists, injection is a one-liner future addition).
- Single Postgres connection per process; adequate for proxy loads; add `psycopg_pool` for high concurrency.
- HNSW index requires pgvector ≥ 0.5; falls back to ivfflat (or no index) on older versions.

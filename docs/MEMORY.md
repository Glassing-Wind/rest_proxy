# Memory-Aware Agent Gateway

This document describes the optional memory and retrieval layer used by the
FastAPI proxy package (`proxy/`) and GraphRAG MCP tools. All features are
gated by environment variables. If an optional dependency such as Postgres,
Redis, Neo4j, or pgvector is unavailable, request handling continues with safe
fallbacks. Memory errors are logged but do not break the response path.

October 16 source audit: this guide's legacy automatic memory path is distinct from
the [explicit SQLite FIRE checkpoint/original store](fire-durable-continuity.md) and
the [shared context builder](context-bundle-contract.md). It does not guarantee a
complete transcript archive. See [current findings](stack-and-context-strategy.md)
and the [persistent-context roadmap](persistent-context-roadmap.md).

---

## Architecture

```
proxy/  (FastAPI routes and request handlers)
│
├── memory/types.py                 – Dataclasses and structured memory records
├── memory/summary.py               – Rolling summaries and tool-output compaction
├── memory/store*.py                – Redis/Postgres/Neo4j storage helpers
├── memory/retrieval.py             – Prompt memory assembly
├── memory/docs_retrieval.py        – Documentation retrieval core
├── memory/code_retrieval*.py       – Code retrieval orchestration and loaders
├── memory/retrieval_*.py           – Ranking, surface, duplicate, and policy helpers
└── memory/bootstrap.py             – Durable schema/bootstrap helpers
```

### Data Flow

```
POST /v1/chat/completions
       │
       ▼ (LM Studio / OpenAI response)
       │
       ├─ eligible non-stream response ──► scheduled _persist_memory_best_effort()
       │
       └─ streaming helper ─────────────► response-ID state; no equivalent persistence hook
```

`_persist_memory_best_effort` does:
1. Appends user + assistant turns to Redis recent-turns list.
2. Updates rolling summary (via LLM if configured, else deterministic).
3. Attempts durable turn/summary writes through the Neo4j graph helpers.

Cached stateful continuations can return before persistence. The Redis cache stores
500-character user/assistant snippets and is trimmed before durable writes; writes
are best-effort and do not acknowledge an archive watermark. Current source is
[handlers_memory.py](../proxy/handlers_memory.py),
[handlers_streaming.py](../proxy/handlers_streaming.py) and
[store_graph_ops.py](../memory/store_graph_ops.py). Older comments that describe
these conversation writes as PostgreSQL are stale.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LM_PROXY_MEMORY_ENABLED` | `1` | Master capability switch; automatic behavior still requires a non-off memory mode |
| `LM_PROXY_MEMORY_MODE` | `stateless` | `stateless/off` disables automatic memory, `assist` enables bounded recent-turn and summary help, and `full` enables broader retrieval; `hybrid` is a compatibility alias for `full` |
| `LM_PROXY_NEO4J_URI` | `bolt://127.0.0.1:7687` | Neo4j Bolt connection URI |
| `LM_PROXY_NEO4J_USER` | `neo4j` | Neo4j username |
| `LM_PROXY_NEO4J_PASSWORD` | - | Neo4j password |
| `LM_PROXY_MEMORY_ENABLE_PERSISTENCE` | `1` | Allow durable persistence when the selected mode enables it |
| `LM_PROXY_MEMORY_ENABLE_REDIS` | `1` | Allow Redis hot session state when the selected mode enables it |
| `LM_PROXY_MEMORY_ENABLE_EMBEDDINGS` | `0` | Enable optional embedding/vector operations under the selected memory mode |
| `LM_PROXY_MEMORY_ENABLE_RETRIEVAL` | `1` | Enable retrieval when embeddings and the selected mode allow it |
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

## Durable storage roles

Current helper implementations use these roles; the earlier PostgreSQL conversation
table descriptions did not match the graph-write source:

| Store / source | Purpose |
|---|---|
| Neo4j `MemoryTurn`, `MemorySummary`, `ToolOutput`, `MemoryCheckpoint` | Legacy conversation/summary/tool/checkpoint helper records; best-effort paths are not full-history guarantees. |
| Neo4j memory embedding helpers | Optional vectors associated with legacy memory records. |
| PostgreSQL `codebase_embeddings`, `doc_embeddings` | Service-backed repository/document vectors and full-text retrieval. |
| SQLite FIRE snapshots/originals | Separate explicit scoped recovery with revision checks, retention and paged originals. |

The [PostgreSQL bootstrap](../memory/bootstrap.py) creates the codebase/document
tables and requests pgvector when that path is used. The
[graph helpers](../memory/store_graph_ops.py) and
[memory embedding helpers](../memory/store_embeddings.py) govern legacy memory writes.

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
2. **Project fallback**: a sanitized/truncated project path extracted from the prompt;
   this intentionally shares a memory bucket across conversations about that path.
3. **Hash fallback**: SHA-256 of system prompt plus first-user prefix (first 16 hex chars).

These heuristics are not authenticated identities or exact project/session/task scope.
The legacy preference lookup also derives the project from the first colon-separated
session component, which can be the namespace. The new continuity path should use
explicit exact scope, as FIRE and the shared builder already do.

---

## Failure Modes

| Failure | Behavior |
|---|---|
| Redis unavailable | Turns/summary silently skipped; proxy continues normally |
| Neo4j unavailable | Legacy conversation/summary/vector writes are skipped or return safe defaults; no durable save is implied |
| Postgres unavailable | Service-backed repository/document retrieval degrades according to its optional-storage path |
| pgvector not installed | PostgreSQL code/document vector schema or retrieval is unavailable; this does not disable Neo4j conversational or embedded retrieval |
| Memory modules missing | Import error logged; proxy runs with `_MEMORY_ENABLED` effectively off |
| Any memory error | Caught and logged via `debug_log`; never propagates to response |

---

## Structured Working Memory (Future)

`memory/types.py` defines the structured working-memory shape stored in Redis:

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

Clients can write this state through the memory store helpers. The assembly
helper in `memory/retrieval.py` renders it into compact text for prompt
injection when memory mode enables it.

---

## Memory Assembly (Prompt Augmentation)

`memory.retrieval.assemble_memory(session_id, query_text)` returns an
`AssembledMemory` object with:
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

# 4. Start proxy (schema bootstrap runs automatically when enabled)
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
- Automatic prompt memory is mode-driven; `stateless` remains the safe default.
- The storage layer uses pooled connections where configured, and falls back
  cleanly when persistence is disabled or unavailable.
- HNSW index requires pgvector ≥ 0.5; falls back to ivfflat (or no index) on older versions.

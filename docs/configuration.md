# Configuration

Configuration is loaded from environment variables and `.env`. Start from
`.env.example`, but enable only the integrations you intend to run.
See the [current architecture](current-architecture.md) for application entry points,
deployment profiles and ownership/trust boundaries.
See [health and readiness](health.md) for `/live`, `/ready`, `/health/details` and
the optional `LM_PROXY_READY_REQUIRED` dependency contract.

## Memory modes

`LM_PROXY_MEMORY_MODE` controls automatic persistence and prompt augmentation:

| Value | Behavior |
|---|---|
| `off` or `stateless` | No automatic memory persistence or prompt injection. This is the default. |
| `assist` | Bounded recent-turn and summary assistance; embeddings stay off. |
| `full` | Persistence, retrieval, and embeddings may run when their feature switches are enabled. |
| `hybrid` | Compatibility alias for `full`; prefer `full` in new configuration. |

The individual memory switches default to enabled for persistence, Redis, and
retrieval, while embeddings default to disabled. They have no automatic proxy
effect while the mode is `off`.

## Minimal proxy-only configuration

```dotenv
LM_BASE=http://127.0.0.1:1234
LM_PROXY_MEMORY_MODE=off
LM_PROXY_GRAPH_ENABLED=0
LM_PROXY_DEBUG=false
```

## Full indexed configuration

```dotenv
LM_PROXY_MEMORY_ENABLED=1
LM_PROXY_MEMORY_MODE=full
LM_PROXY_MEMORY_ENABLE_PERSISTENCE=1
LM_PROXY_MEMORY_ENABLE_REDIS=1
LM_PROXY_MEMORY_ENABLE_RETRIEVAL=1
LM_PROXY_MEMORY_ENABLE_EMBEDDINGS=1

LM_PROXY_PG_DSN=postgresql://USER@127.0.0.1:5432/lm_proxy_memory
LM_PROXY_REDIS_URL=redis://127.0.0.1:6379/0

LM_PROXY_GRAPH_ENABLED=1
LM_PROXY_NEO4J_URI=bolt://127.0.0.1:7687
LM_PROXY_NEO4J_USER=neo4j
LM_PROXY_NEO4J_PASSWORD=CHANGE_ME
LM_PROXY_NEO4J_DB=neo4j
```

When `LM_PROXY_NEO4J_DB` is omitted, the service-backed readers use **`neo4j`**,
matching the Compose/CI database convention. Keep an explicit
`LM_PROXY_NEO4J_DB=proxy` if that is the name of your existing configured database.
The default change does not rename databases, move stored data or rewrite your
machine's `.env`; check the selected database before enabling service-backed writes.

Compose and live graph CI use the **Neo4j 5.26** server baseline. The minor image
tag remains mutable; it is not a patch/digest reproducibility guarantee. Compose
does not load APOC or grant unrestricted APOC procedures. New plugins require a
concrete application need and an explicit compatible, restricted configuration.

## Embedded graph thread budget

`LM_PROXY_LADYBUG_THREADS` accepts an integer from **1 to 64**, validated before
opening graph state. It sets the native Ladybug database thread budget; it does not
add parallel application owners or configure embedding/GPU workers. Explicit
`LadybugGraphDriver(..., max_num_threads=N)` takes precedence over the environment;
`EmbeddedRepositoryOwner(..., graph_threads=N)` supplies that override. The default
remains 2 until workload measurements justify another value.

## Local paths

Avoid committing machine-specific absolute paths. `LM_PROXY_RG_PATH` may be
omitted when `rg` is on `PATH`. Use `LM_PROXY_TS_PACK_CACHE_DIR` only when the
default user cache is unsuitable.

## Failure behavior

Redis, Postgres, Neo4j, and embedding failures are handled at I/O boundaries so
the proxy request path can continue with reduced capability. Indexing and tools
that explicitly require a missing integration return a diagnostic instead.

See [Memory](MEMORY.md) for the detailed memory variables and
[LM Studio embeddings](lm-studio.md) for local embedding settings.

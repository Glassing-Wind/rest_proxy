# Configuration

Configuration is loaded from environment variables and `.env`. Start from
`.env.example`, but enable only the integrations you intend to run.
See the [current architecture](current-architecture.md) for application entry points,
deployment profiles and ownership/trust boundaries.
See [health and readiness](health.md) for `/live`, `/ready`, `/health/details` and
the optional `LM_PROXY_READY_REQUIRED` dependency contract.

## Choose a storage profile

| Profile | Storage | Tool route |
| --- | --- | --- |
| Embedded | Local LadybugDB and LanceDB; no separate Neo4j/Postgres/Redis servers for the supported repository slice. | Explicit publication/source/relationship tools plus bounded common search and health bridges. |
| Service-backed | Neo4j and Postgres/pgvector; Redis for optional session/lock features. | Existing `index_workspace`, `get_indexing_health` and `search_codebase` workflows. |

These are selectable paths in the same application, with incomplete feature parity.
There is no automatic data migration between them. Embedded selection does not
disable the separate legacy inference-memory integrations; turn those off explicitly
for an embedded-only deployment. See [embedded installation](minimal-embedded-install.md)
for native dependencies and [embedded MCP owner](embedded-mcp-owner.md) for tool scope.

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

## Embedded repository configuration

Install the embedded dependencies before using this profile. Replace the example
absolute paths with private writable locations outside the installed package:

```dotenv
LM_PROXY_STORAGE_BACKEND=embedded
LM_PROXY_GRAPH_BACKEND=ladybug
LM_PROXY_EMBEDDED_STATE=/absolute/private-state
LM_PROXY_RUNTIME_DIR=/absolute/private-runtime
LM_PROXY_CONFIG_DIR=/absolute/private-config
LM_PROXY_MEMORY_EMBEDDING_DIM=768
# Optional Ladybug CPU thread budget; unset/blank defaults to 2:
LM_PROXY_LADYBUG_THREADS=2

LM_PROXY_MEMORY_ENABLED=0
LM_PROXY_MEMORY_MODE=off
LM_PROXY_MEMORY_ENABLE_PERSISTENCE=0
LM_PROXY_MEMORY_ENABLE_REDIS=0
LM_PROXY_MEMORY_ENABLE_EMBEDDINGS=0
LM_PROXY_MEMORY_ENABLE_RETRIEVAL=0
LM_PROXY_WATCHER_ENABLED=0
LM_PROXY_EMBEDDED_WATCH_ENABLED=0

# Required for real indexing and vector/hybrid queries, not published text/source reads:
LM_PROXY_EMBEDDED_MODEL_ARTIFACT=/absolute/model.gguf
```

The dimension (768 in this example) must match both the stored schema and the
configured embedding model. Those legacy memory switches do not disable explicit
embedded embedding operations.
Current MCP indexing/vector queries use an already loaded local LM Studio embedding
model. The artifact path supplies a fingerprint; it does not load a model or attest
the resident weights. Use `mode="text"` explicitly for model-free published search;
the embedded search tool defaults to hybrid. The retained Neo4j Python client is an
import dependency, not a requirement to run a Neo4j server in this profile.

`LM_PROXY_LADYBUG_THREADS` accepts an integer from **1 to 64**, validated before
opening graph state. It sets the native Ladybug database thread budget; it does not
add parallel application owners or configure embedding/GPU workers. Explicit
`LadybugGraphDriver(..., max_num_threads=N)` takes precedence over the environment;
`EmbeddedRepositoryOwner(..., graph_threads=N)` supplies that override. The default
remains 2 until workload measurements justify another value.

Discover committed state with `list_embedded_projects`, `get_embedded_overview` and
`get_embedded_indexing_attempt`; read with `describe_embedded_file` and
`search_embedded_repository`. Common `get_indexing_health` now hashes only published
manifest paths against current bytes and reports latest indexing attempts, without
an encoder. It refuses symlink traversal; platforms lacking the safe POSIX reader
report freshness as unknown. Deep `audit=true` fidelity/integrity checks remain
unsupported. Common `search_codebase(mode="text")` is model-free; its default
`precise` aliases native hybrid and requires the matching encoder. Native search
returns JSON run/hash receipts, not the service-backed rendering/ranking contract.
Standard indexing is still guarded in embedded mode. See
[common-tool coverage](embedded-mcp-owner.md#october-11-common-tool-bridges).

Use one owner process per embedded state directory. Multiple MCP clients can share
one HTTP daemon; independent STDIO/HTTP processes cannot own the same state together.
Keep endpoints on loopback unless authentication and project isolation are tested.
Use disposable state for validation and agree source/provider/retention scope before
processing customer data. REST evidence and context tools have separate opt-in flags;
this example does not enable them or start a watcher.

## Service-backed indexed configuration

Unset the embedded storage/graph selectors in the process configuration when
choosing this profile; merely enabling Neo4j or Postgres does not override them.

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

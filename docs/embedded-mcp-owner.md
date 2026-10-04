# Embedded MCP owner — October 4, 2026

Priority 2 now has an explicit experimental MCP path to the combined Ladybug and
LanceDB owner. STDIO and Streamable HTTP register the same explicit tool group. This is a
usable file-outline/source/retrieval slice; full graph compatibility, existing
application workspace metadata and REST context-bundle integration remain unfinished.

## Configuration and tools

Set these before starting the server:

```sh
export LM_PROXY_STORAGE_BACKEND=embedded
export LM_PROXY_GRAPH_BACKEND=ladybug
export LM_PROXY_EMBEDDED_STATE=/absolute/private-state
export LM_PROXY_MEMORY_EMBEDDING_DIM=768
# Required only for indexing and vector/hybrid queries:
export LM_PROXY_EMBEDDED_MODEL_ARTIFACT=/absolute/model.gguf
# Disable the separate optional inference-memory pool and watcher for storage-only acceptance:
export LM_PROXY_MEMORY_ENABLED=0
export LM_PROXY_WATCHER_ENABLED=0
.venv/bin/python mcp_server.py
```

Use the same configuration with `uvicorn brain_server:app --host 127.0.0.1 --port
8001` for Streamable HTTP MCP at `/mcp`. Use the combined owner's state directory
containing `graph` and `vectors`. Existing standalone Ladybug databases and Kuzu
files are not migrated. Dimension must match the stored vector schema; a separate
state directory is required for fixture vectors of another dimension.

| Tool | Behavior |
| --- | --- |
| `index_embedded_repository(source_root, project_id, paths)` | Wait for complete replacement from an explicit relative-path manifest. Omitted files disappear. Return publication/count/encoder metadata. |
| `list_embedded_projects(limit, after)` | Discover committed project IDs, canonical roots and run IDs with bounded pagination. |
| `get_embedded_file_facts(project_id, file_path, limit, offset)` | Read paginated hashed import/call/native route observations. Older snapshots explicitly require reindexing for this contract. |
| `get_embedded_overview(project_id)` | Read run/manifest identity, file/symbol counts and retrieval configuration without a model. |
| `describe_embedded_file(project_id, file_path, ...)` | Read numbered, bounded original source and outlines from the publication, with run ID and source SHA256. |
| `search_embedded_repository(project_id, query, mode, limit)` | Search published text/vector/hybrid chunks with scope and hash checks. Text mode needs no encoder; other modes require matching encoder identity. |

Project IDs are explicit (nonempty, at most 128 characters, without colons).
Indexing uses an already loaded loopback LM Studio embedding model through the
strict adapter. It does not load/unload/download models or substitute synthetic
vectors. The artifact argument fingerprints a local file; resident weights and
runtime version are not attested. See [real embedding acceptance](real-embedding-acceptance.md).

Tools register only when embedded selection and the state directory are explicitly
configured. The primary discovery profile includes the three read tools; use the
full tool profile to discover indexing. Catalog entries explain their scope.
Registration is discovery, not an authorization boundary: these tools inherit the
server's local filesystem privileges and existing deployment access controls.

## Ownership and failure behavior

One lazy process runtime serializes operations, owns both engines and reuses a
strict encoder after its first real-embedding operation. Failed encoder setup is
closed and can be retried; already published source/text reads remain available.
Closing drains active runtime operations, releases the owner and closes the encoder.
Both transport shutdown paths close the runtime. Native local locks still refuse
another process opening the same state. Serve multiple clients through one daemon;
independent STDIO and HTTP processes cannot simultaneously own one state directory.

When the state path is configured, graph bootstrap reuses this owner's graph
instead of opening another Ladybug database. Existing Neo4j query tools do not gain
full compatibility from this shared handle. Use the explicit embedded tools above
for this slice. Existing `index_workspace`/structural/semantic workers remain guarded
before external storage writes; their error points to the experimental manifest path.
No automatic watcher or incremental-job bridge is added.

## Executed evidence

Five offline runtime/MCP checks cover registration/schema dispatch, text/source reads
without an encoder, query identity refusal, failed encoder cleanup, draining shutdown
and resetting the process owner. Five native combined-owner test methods include a
new integration check: indexing through the runtime, bootstrap handle reuse,
competing owner refusal, direct/SDK source equality, original source surviving a live
file edit, shutdown release and text/source reads after reopening without a model.
The previous stage/publication failure, cancellation and crash checks still pass.
Full local CI passes; offline runtime tests are gated, native tests remain optional.

A real transport check reopened the existing **131-file / 1,629-chunk / 768-dimensional**
Jina publication sequentially through STDIO and HTTP MCP. Overview, bounded source
and text-search citation outputs were identical. STDIO had all networking denied;
the HTTP server allowed only its temporary local transport port. External-storage
networking was denied, memory pool and watcher disabled, and no embedding-model
connection was required. This check reads the frozen prior publication; it does not
claim a new real-model indexing run or independent coding-outcome improvement.

```sh
.venv/bin/python test_embedded_runtime.py
.venv/bin/python test_embedded_repository.py  # native optional extras
.venv/bin/python scripts/check_embedded_mcp.py \
  --state /absolute/private-state --project-id proof \
  --file memory/embedded_lance_runs.py --query validate_vector \
  --dimension 768 --sandbox  # macOS process network policy
```

[Acceptance receipt](../benchmarks/reports/2026-10-04/embedded-mcp-owner.json).
Private logs live in `.runtime/embedded-mcp-acceptance/` (mode 700).
Next: full call/import/route query compatibility, application workspace metadata,
REST integration, production encoder identity and packaging/restore acceptance.

[Durable project discovery](embedded-project-discovery.md) now also routes the
existing `resolve_graph_project` and `get_project_overview` tools to committed
embedded metadata. Other legacy workspace and graph-query workflows remain incomplete.

[Published parser facts](embedded-parser-facts.md) add source/fact hash verification
and bounded syntactic call/import/route evidence. Target resolution remains incomplete.

# rest_proxy: Advanced AI Proxy and GraphRAG Engine

`rest_proxy` is a high-performance FastAPI server designed to sit between an LLM client and an inference provider (like LM Studio). It provides advanced memory management, structural code indexing, and semantic search capabilities using GraphRAG techniques.

## 🚀 Key Features

- **MCP Server Integration**: Seamlessly connects to Antigravity or Claude Desktop via the Model Context Protocol.
- **GraphRAG Architecture**:
  - **Structural Indexing**: AST-based code analysis stored in **Neo4j**.
  - **Semantic Indexing**: Vector embeddings stored in **PostgreSQL (pgvector)**.
- **Smart Memory**: 
  - Rolling conversation summaries.
  - Context-aware retrieval for long-running sessions.
- **Robust Tooling**: 
  - `search_codebase`: Multi-layered semantic search.
  - `visualize_subgraph`: Mermaid diagrams for code structure.
  - `find_callers` / `find_definitions`: Instant static analysis.
  - `find_code_duplication`: Finds duplicate code via hashes, winnowing, and semantic similarity.
    - Winnowing guarantee: matches shorter than `t = w + k − 1` are not guaranteed; small blocks use k-gram/token fallback.
    - Tune with `winnow_*` parameters for repo size and noise tolerance.
- **Reliability & Auditing**:
  - `get_indexing_health(audit=True)`: Deep structural verification (Level 2).
    - **Internal Import Resolution**: Validates that imports link to actual file nodes.
    - **Symbol Density**: Detects "phantom" files with 0 symbols.
    - **Structural Connectivity**: Identifies isolated source files via WCC analysis.
- **LM Studio Optimization**: Automatic model-fallback logic for easy embedding generation.

## 🛠 Prerequisites

- **Python 3.11+** (Conda recommended).
- **PostgreSQL** with `pgvector` extension.
- **Neo4j** (Aura or Local).
- **Redis** (for caching).
- **LM Studio** (for local embeddings).

## 🏃 Quick Start

1. **Configure Environment**: Copy `.env.example` (if available) to `.env` and fill in your database credentials.
2. **Start the Shared MCP Brain**:
   ```bash
   ./scripts/start_brain_server_daemon.sh
   ```
3. **Connect clients to** `http://localhost:8001/mcp`
4. **Index your Workspace**:
   Use the `index_workspace` tool from your AI assistant to perform a full sync.

## ts-pack Dependency Policy

This repo currently pins a forked `tree_sitter_language_pack` commit for GraphRAG
fidelity and database-selection behavior. The update and CI policy for that
fork is documented in [docs/ts_pack_fork_policy.md](/Users/michaelmarler/Projects/rest_proxy/docs/ts_pack_fork_policy.md).

## LM Studio Local Embeddings

For new local embedding work we use a hybrid LM Studio integration:

- native v1 REST API under `/api/v1/*` for model lifecycle
  - `GET /api/v1/models`
  - `POST /api/v1/models/load`
  - `POST /api/v1/models/unload`
- OpenAI-compatible `POST /v1/embeddings` for embedding generation

This split is intentional. LM Studio recommends the native v1 API for new
projects, but embeddings are still documented under the OpenAI-compatible
surface.

### Why this repo uses both API surfaces

- `/api/v1/*` gives explicit model management and load configuration.
- `/v1/embeddings` is still the documented embeddings endpoint.
- Keeping lifecycle and embeddings separate makes the provider easy to swap
  later if we move to a different local backend.
- Some LM Studio embedding runtimes reject `eval_batch_size` on model load.
  The provider handles this cleanly by retrying the load with `context_length`
  only instead of failing the whole embedding path.

### Environment

```bash
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

Compatibility note:
- older repo paths still honor `LM_PROXY_MEMORY_EMBEDDING_MODEL`,
  `LM_PROXY_MEMORY_EMBEDDING_BASE_URL`, and `LM_EMBED_BATCH_SIZE`
- the new `LMSTUDIO_*` names are the preferred configuration surface

### Starting LM Studio as a service

GUI:
- open LM Studio
- enable the Developer server on `localhost:1234`

Headless / service style:

```bash
lms server start
```

Then verify:

```bash
curl http://127.0.0.1:1234/api/v1/models
curl http://127.0.0.1:1234/v1/models
```

### Benchmarking and tuning on Apple Silicon

Run:

```bash
python /Users/michaelmarler/Projects/rest_proxy/scripts/benchmark_lmstudio_embeddings.py
```

Recommended first-pass tuning for a MacBook Pro M3-class machine:
- `LMSTUDIO_CONTEXT_LENGTH=2048`
- `LMSTUDIO_EVAL_BATCH_SIZE=512`
- `LMSTUDIO_MAX_BATCH_SIZE=64`, `128`, then `256`
- `LM_EMBED_CONCURRENCY=4`

Keep request batch size constrained by token length, not just document count:
- short code/doc chunks: request batches of `128-256` are reasonable to test
- longer chunks: reduce request batch size before raising timeout values

Model note:
- `text-embedding-jina-embeddings-v2-base-code` is a good starting point for
  local code embeddings because it is relatively small and benchmark-friendly
- on Apple Silicon, larger MLX-native embedding models may eventually win on
  throughput, but should be benchmarked separately instead of assumed faster

## HTTP MCP Operation

The recommended deployment mode is a single shared Streamable HTTP MCP daemon:

```bash
./scripts/start_brain_server_daemon.sh
./scripts/brain_server_status.sh
./scripts/restart_brain_server.sh
./scripts/stop_brain_server.sh
```

Point MCP-capable clients at:

```text
http://localhost:8001/mcp
```

Supported client mode today:

- shared HTTP MCP daemon at `http://localhost:8001/mcp`
- manual watcher activation via `watch_project(path)` / `unwatch_project(path)`
- opt-in standards-based root sync via `watch_project()` for roots-capable clients
- no automatic workspace inference by default for shared HTTP clients

This is intentional. Transport sessions are not treated as trustworthy repo
identity across mixed IDE/client setups.

After restarting the daemon, refresh or reconnect the MCP client so it picks up
the new process and current tool list.

To verify that you are talking to the current daemon process instead of a stale
client session, check the HTTP diagnostics:

```bash
curl -sS -D - http://127.0.0.1:8001/fingerprint
curl -sS -D - http://127.0.0.1:8001/health
```

Look for:

- `x-graphrag-boot-id`: changes after a real restart
- `x-graphrag-tool-fingerprint`: changes when registered wrappers, delegated
  MCP implementation modules, shared graph/index/memory helpers, or the HTTP
  transport runtime change, and when pinned runtime/CI dependencies change
- `x-graphrag-session-known: 0`: the client is sending no MCP session or a stale one

The JSON bodies also include `boot_id`, `fingerprint`, `uptime_seconds`, and a
`session` object so you can tell whether the server recognizes the incoming
`Mcp-Session-Id`.

For a protocol smoke check against the live daemon:

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python \
  /Users/michaelmarler/Projects/rest_proxy/scripts/check_mcp_protocol.py
```

For a narrow end-to-end MCP tool parity check against the live daemon:

```bash
/Users/michaelmarler/Projects/rest_proxy/scripts/run_mcp_tool_parity_smoke.sh
```

This verifies a small high-value subset of retrieval tools through the actual
MCP transport and compares the outputs to direct in-process tool invocation, so
transport wiring drift is caught separately from ranking regressions. It also
checks compact contracts for the retrieval-QA support tools over deterministic
caller-supplied candidates.

For a restart regression that verifies stale MCP sessions are rejected after a
daemon restart:

```bash
./scripts/check_mcp_stale_session_restart.sh
```

See also:

- [docs/mcp_conformance_checklist.md](/Users/michaelmarler/Projects/rest_proxy/docs/mcp_conformance_checklist.md)
- [docs/mcp_tool_coverage_tiers.md](/Users/michaelmarler/Projects/rest_proxy/docs/mcp_tool_coverage_tiers.md)
- [docs/tool_trust_status.md](/Users/michaelmarler/Projects/rest_proxy/docs/tool_trust_status.md)

## Retrieval Quality Gate

For the standard user-trust gate, run:

```bash
/Users/michaelmarler/Projects/rest_proxy/scripts/run_retrieval_quality_gate.sh
```

This is the main retrieval-quality path for the repo. It runs:

- MCP initialize/delete and stale-session restart lifecycle checks
- canonical tool-choice evals
- product-shape checks that keep secondary architecture tools behind the
  preferred onboarding flow
- catalog intent checks for support-tool phrasing such as definition navigation,
  ranking debug, duplicate-result review, and changed-code review
- catalog flow-routing checks that separate full-stack app flow from backend
  request/database flow
- positive full-stack UI-to-route/API parity on the indexed `rental` benchmark,
  while preserving the separate empty-coverage diagnostic
- backend route-handler fallback parity on `rental`, without inventing
  route-specific service/database bindings from file-level graph edges
- Spring controller route inventory parity on Petclinic, backed by declared
  mapping facts from the pinned ts-pack fork rather than search heuristics
- catalog route/controller intent checks that route handler questions to
  `search_codebase`
- health-gated live graph regressions against the indexed benchmark repos
- MCP transport parity smoke checks against the live daemon

Use this when you want the quickest answer to:

- do the retrieval tools still choose the right tool families?
- do the real benchmark repos still return healthy, aligned, useful results?
- did a ranking change break a real investigation workflow?
- did direct tool behavior drift from actual MCP transport behavior?

For a concrete MCP-only workflow pass over the preferred indexed-repo
investigation stack, run:

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python \
  /Users/michaelmarler/Projects/rest_proxy/scripts/run_mcp_investigation_pass.py \
  --workflow-id rest_proxy_preferred_investigation_stack
```

This uses the live MCP transport only. It checks the catalog recommendation,
index health, project overview, semantic search, symbol context, and call-chain
flow against
[`benchmarks/mcp_investigation_workflows.json`](/Users/michaelmarler/Projects/rest_proxy/benchmarks/mcp_investigation_workflows.json).
If the pass stops, it prints `FIRST TRUST HESITATION` with the workflow, step,
tool, and first missing or suspicious evidence.

## Live Graph Regressions

For a narrow live retrieval/tool-eval pass against the current indexed benchmark
workspaces, run:

```bash
/Users/michaelmarler/Projects/rest_proxy/scripts/run_live_graph_regressions.sh
```

This exercises the focused real-user regression set in
[`benchmarks/live_graph_goldens.json`](/Users/michaelmarler/Projects/rest_proxy/benchmarks/live_graph_goldens.json)
through [`test_live_graph_tools.py`](/Users/michaelmarler/Projects/rest_proxy/test_live_graph_tools.py),
including:

- model inference selection
- provider wiring
- tool-result to message conversion
- gRPC request routing
- Apple/Xcode build graph orientation
- Spring owner request routing and controller workflows
- Rust command dispatch
- Rust routing and serve entrypoints
- TypeScript schema conversion and symbol resolution
- Go router registration and request handling
- multi-step investigation workflows that chain search, symbol context, references, and directory snapshots
- Java Spring controller and request-flow entrypoints
- Kotlin interceptor chain classes and methods
- SwiftNIO package orientation and export/import surface summaries

To run a narrower subset or pass extra flags directly, invoke the harness:

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python \
  /Users/michaelmarler/Projects/rest_proxy/test_live_graph_tools.py \
  /Users/michaelmarler/Projects/pydantic-ai \
  /Users/michaelmarler/draw-things-community \
  /Users/michaelmarler/Projects/uv \
  /Users/michaelmarler/Projects/axum \
  /Users/michaelmarler/Projects/zod-upstream \
  --regressions-only \
  --verbose-progress \
  --fail-fast \
  --case-id pydantic_ai_model_inference_selected_search
```

## Enterprise Retrieval Eval

For broader retrieval trend artifacts, run:

```bash
python /Users/michaelmarler/Projects/rest_proxy/scripts/run_enterprise_eval.py --skip-graph
```

Use this when you want:

- duplicate-collapse benchmark summaries
- historical JSON artifacts under `.runtime/enterprise_eval/`
- trend comparison across retrieval metric changes

This is intentionally separate from the retrieval quality gate. The quality
gate answers "would I trust the tools right now?"; enterprise eval answers
"how are the retrieval metrics trending over time?".

Current baseline:

- the validated benchmark corpus is on the current semantic contract
- semantic-contract upgrades refresh unchanged chunk metadata in place during
  incremental indexing, without recomputing embeddings
- dispatcher and routing telemetry summaries are current-first
- primary retrieval/orientation paths are semantic-role-first, with legacy path
  fallback mostly limited to older metadata shapes and smaller helper surfaces

If you need a more narrative status update and the remaining cleanup backlog,
see:

- [/Users/michaelmarler/Projects/rest_proxy/docs/tool_trust_status.md](/Users/michaelmarler/Projects/rest_proxy/docs/tool_trust_status.md)
- [/Users/michaelmarler/Projects/rest_proxy/docs/retrieval_architecture_plan.md](/Users/michaelmarler/Projects/rest_proxy/docs/retrieval_architecture_plan.md)

## STDIO Fallback

The legacy stdio MCP path is still available as a fallback for clients that
cannot connect to the shared HTTP daemon.

To start the stdio server directly:

```bash
python mcp_server.py
```

To use the supervisor wrapper:

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python \
  /Users/michaelmarler/Projects/rest_proxy/scripts/graphrag_mcp_supervisor.py
```

To restart the supervised stdio child:

```bash
./scripts/restart_graphrag_mcp.sh
```

## FastAPI Proxy

Start the HTTP proxy separately when you want the LLM proxy itself:

```bash
uvicorn proxy:app --host 0.0.0.0 --port 8000
```

## Codex App MCP Launch

For the Codex app, prefer the shared HTTP daemon configuration:

```toml
[mcp_servers.graphrag-brain]
url = "http://localhost:8001/mcp"
```

If you need a stdio fallback instead, use the supervisor wrapper:

   ```bash
   /opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python \
     /Users/michaelmarler/Projects/rest_proxy/scripts/graphrag_mcp_supervisor.py
   ```

Recommended working directory:

```bash
/Users/michaelmarler/Projects/rest_proxy
```

Useful env vars in the Codex app MCP config:

```bash
PYTHONUNBUFFERED=1
LM_PROXY_MEMORY_ENABLED=1
LM_PROXY_NEO4J_OP_PREFIX=lmproxy
```

Optional Swift enrichment:

```bash
LM_PROXY_SWIFT_SOURCEKITTEN=1
```

When enabled, structural indexing performs a best-effort SourceKitten pass for
Swift files to enrich symbol nodes with Swift-specific metadata. If
`sourcekitten` is unavailable or fails, indexing continues without enrichment.

## Graph Regression Checks

Run the focused GraphRAG regression suite with:

```bash
./scripts/check_graph_pipeline.sh
```

This covers parser-fact precision, semantic indexer helper behavior, asset-graph
route attribution, flow-summary stability, and the integrated fixture contract.

## CI Checks

Run the gated local CI surface with:

```bash
./scripts/run_ci_checks.sh
```

This runs:

- Ruff across the Python service and tooling surface
- the GraphRAG regression suite
- CI-safe service, memory, docs, and cross-project tests
- the live `tree_sitter_language_pack` contract check in `test_ts_pack_contract.py`

Baseline dependency auditing is available separately with:

```bash
./scripts/run_dependency_audit.sh
```

## Watcher Behavior

Background file watching is manual by default.

Use the MCP tools:

- `watch_project` to pin a repo for background watching
- `unwatch_project` to stop background watching

The watcher does not automatically start just because a repo is open in an IDE.
This avoids incorrect cross-IDE attribution when clients share the same HTTP MCP
daemon.

## Release Process

Repository versioning now uses:

- [VERSION](/Users/michaelmarler/Projects/rest_proxy/VERSION) as the canonical release version
- [CHANGELOG.md](/Users/michaelmarler/Projects/rest_proxy/CHANGELOG.md) for release notes history
- [.github/workflows/release.yaml](/Users/michaelmarler/Projects/rest_proxy/.github/workflows/release.yaml) for tag-driven GitHub releases

To cut a release:

1. Update `VERSION`
2. Add the matching `## [x.y.z]` section to `CHANGELOG.md`
3. Push tag `vX.Y.Z` or run the release workflow manually

## 📁 Architecture

- `proxy.py`: Core FastAPI proxy logic.
- `mcp_server.py`: MCP protocol implementation and tool definitions.
- `graph_indexer.py`: Neo4j structural analysis logic.
- `vector_indexer.py`: Postgres semantic indexing logic.
- `memory_store.py`: Centralized persistence layer (Redis, Postgres, Neo4j).
- `memory_retrieval.py`: Embedding generation and search logic.

## Neo4j GenAI Plugin Notes

If you use Neo4j's GenAI plugin for embeddings or text generation, the current
plugin uses **Cypher 25** functions under `ai.text.*` (the old `genai.vector.*`
calls are deprecated). If your database defaults to Cypher 5, prepend queries
with `CYPHER 25`.

Embeddings (current API):
- `ai.text.embed(resource, provider, config)`
- `ai.text.embedBatch(resources, provider, config)`
- Provider discovery: `CALL ai.text.embed.providers()`

Provider config keys (from official docs):
- OpenAI (`openai`): `token`, `model`, optional `vendorOptions`.
- Azure OpenAI (`azure-openai`): `token`, `resource`, `model`, optional `vendorOptions`.
- Vertex AI (`vertexai`): `model`, `project`, `region`, exactly one of `apiKey` or `token`.
- Bedrock Titan (`bedrock-titan`): `accessKeyId`, `secretAccessKey`, `model`, `region`.

OpenAI base URL override: `genai.openai.baseurl` (applies to `ai.text.*` calls).

## 🛡 License

MIT

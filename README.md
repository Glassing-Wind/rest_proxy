# rest_proxy

`rest_proxy` is a FastAPI inference proxy and an indexed GraphRAG tool server for
AI coding agents. It combines semantic retrieval, structural code relationships,
documentation search, durable memory, and repository operations behind a Model
Context Protocol (MCP) interface.

It supports investigation of large or unfamiliar repositories. Exact text search is
appropriate for known names; the indexed tools support architecture,
callers, implementations, request flow, related files, cross-project dependencies,
and duplicate code. Choose the smallest call that supplies the required evidence;
speed and token advantages require comparable measurements.

Start with the [current architecture](docs/current-architecture.md) for the MCP and
optional inference boundaries, deployment profiles, storage ownership and acceptance
limits. Dated research and benchmark reports describe their recorded revisions.

## Capabilities

- Structural indexing through Neo4j or the explicit embedded LadybugDB path.
- Semantic code retrieval through Postgres/pgvector or embedded LanceDB; the
  authored-document retrieval pipeline remains service-backed.
- Ranked search with exact fallbacks and duplicate-aware reranking.
- Symbol context, definitions, references, callers, and call-chain traversal.
- Project, directory, request-flow, and dependency summaries.
- Optional Redis-backed session state and durable conversation memory.
- Shared Streamable HTTP MCP daemon with a legacy STDIO fallback.
- Health, freshness, transport-parity, and retrieval-quality checks.

External integrations are optional at proxy startup and should fail open.
Choose one storage profile for the intended workflow; full tool parity between
profiles is not established.

## Storage profiles

| Profile | Repository storage | Dependencies and current scope |
| --- | --- | --- |
| Embedded | LadybugDB graph and LanceDB chunks/vectors in a private local state directory. | No Neo4j, Postgres or Redis server is required for the supported embedded repository tools. Disable separate legacy memory/persistence/Redis paths. |
| Service-backed | Neo4j graph and Postgres/pgvector retrieval; Redis is optional. | Retains the existing service-backed indexing, retrieval and memory workflows. Enable only the services those features use. |

These are two paths in the same application/MCP architecture. Running both stacks
is not required, and switching selectors does not migrate existing data or make
every tool backend-neutral. Embedded real-vector indexing and vector/hybrid queries
currently require a configured local LM Studio embedding service and model artifact
identity. Published source, overview and text-search reads need no embedding model.
An embedding service is separate from database storage.

See [Configuration](docs/configuration.md), the
[minimal embedded installation](docs/minimal-embedded-install.md) and
[embedded MCP owner](docs/embedded-mcp-owner.md) for profile-specific setup and limits.

See [native ARM validation](docs/arm-validation.md) for artifact fingerprints,
Ladybug thread budgets, the disposable benchmark and the new ARM CI matrix. Native
execution and small-fixture timings do not establish a general speed advantage.

## Requirements

- Python 3.14 is the reference CI/appliance runtime and recommended for development;
  Python 3.11 remains the package compatibility floor.
- `rg` (ripgrep) for exact-search fallbacks.
- LM Studio or another compatible inference endpoint for proxy requests and
  local embeddings.
- Embedded profile: LadybugDB, LanceDB and the pinned modified parser fork.
- Service-backed profile: Neo4j for its graph tools, Postgres with pgvector for
  its retrieval/memory features, and Redis for optional session state/locks.

You can run the HTTP proxy without enabling every indexed or memory feature.

## Embedded installation

A [minimal embedded profile](docs/minimal-embedded-install.md) uses LadybugDB and
LanceDB with the modified parser fork, explicit writable state paths and optional
LM Studio embeddings. The core package installs separately from `embedded`, `full`
and `dev` extras. Installed native fixture acceptance passes on macOS ARM64 / Python
3.14; broader platform, restore and outcome gates remain open.

## Quick start

Create an environment and install dependencies:

```bash
./scripts/setup_project_python.sh
source .venv/bin/activate
```

Create local configuration:

```bash
cp .env.example .env
```

Review `.env` and select the storage profile you intend to use. Replace the Neo4j
and Postgres placeholders only for the service-backed features you enable. See
[Configuration](docs/configuration.md) for safe minimal examples.

Start the shared MCP daemon:

```bash
./scripts/start_brain_server_daemon.sh
./scripts/brain_server_status.sh
```

Connect an MCP client to `http://127.0.0.1:8001/mcp`. For the service-backed profile,
use `get_indexing_health` and, when indexing is needed, `index_workspace` with the
absolute repository path. For embedded setup, follow the explicit manifest/tools
in [embedded MCP owner](docs/embedded-mcp-owner.md); standard health/search/indexing
have different coverage: common health/search now have bounded embedded routes,
while indexing still requires the explicit embedded manifest tool.

Verify the daemon:

```bash
curl -sS http://127.0.0.1:8001/health
python scripts/check_mcp_protocol.py
```

Run the inference proxy separately when needed:

```bash
uvicorn proxy:app --host 127.0.0.1 --port 8000
curl -sS http://127.0.0.1:8000/health
```

The proxy exposes OpenAI-compatible `/v1/models` and `/v1/chat/completions`
routes and forwards requests to `LM_BASE`, which defaults to LM Studio at
`http://127.0.0.1:1234`.

Set `LM_PROXY_PROVIDER=openai` and supply `OPENAI_API_KEY` to use GPT-6 Sol
through OpenAI Responses while retaining the Chat Completions client interface.
Local embeddings and GraphRAG services continue using their existing configuration.
See [OpenAI setup, validation, and rollback](docs/openai.md).

## Recommended agent workflow

For ChatGPT architecture planning and Codex implementation, start with the
[ChatGPT and Codex workflow](docs/chatgpt-codex-workflow.md). It includes the roles
of Antigravity, Claude, Siri, Linear and cloud execution, source orientation,
reusable handoffs, focused checks and Git completion guidance.

The proposed [Repository Intelligence Audit](docs/fire-repository-audit-offer.md)
includes a revision-bound demonstration and evidence bundle. Its focused acceptance
does not certify the current branch or establish investigation speed/token savings.

For a service-backed indexed repository:

1. `get_indexing_health` — verify freshness and alignment.
2. `get_project_overview` — orient on the repository.
3. `search_codebase` — find the likely implementation surface.
4. `get_symbol_context` — inspect one symbol with bounded source evidence.
5. `get_call_chain` or `find_references` — trace behavior across files.

Use `grep_codebase` for exact text and `get_mcp_tool_catalog` when the correct
specialized tool is unclear.

For an embedded publication, begin with `list_embedded_projects`,
`get_embedded_overview` and `get_embedded_indexing_attempt`. Use
`search_embedded_repository(mode="text")` for stored text and
`describe_embedded_file` for published source. `index_embedded_repository` accepts
an explicit complete replacement manifest; omitted files disappear from the next
publication. Verify relevant current source before acting on snapshot evidence.

Common `get_indexing_health` now checks embedded manifest hashes against current
files without a model; its deeper audit remains unsupported. Common
`search_codebase(mode="text")` reads embedded chunks without external databases or
an encoder, with run/hash receipts and explicit native-policy limits. Default
`precise` maps to native hybrid and requires the matching local encoder. See the
[bounded bridge acceptance](benchmarks/reports/2026-10-11/embedded-tool-routing.md).

## Validation

Run the gated local checks:

```bash
./scripts/run_ci_checks.sh
```

This checks the production Python surface, runs GraphRAG regressions, exercises
MCP protocol behavior, and runs CI-safe service and persistence checks.

Focused checks:

```bash
python -m py_compile proxy/app.py mcp_server.py brain_server.py
./scripts/check_graph_pipeline.sh
python scripts/run_tool_choice_eval_suite.py
```

See [Evaluation](docs/evaluation.md) for live graph checks, retrieval quality,
and the native-vs-MCP agent benchmark.

## Architecture

- `proxy/`: FastAPI proxy, request handlers, routing, filtering, and memory.
- `brain_server.py`: shared HTTP MCP daemon.
- `mcp_server.py`: STDIO fallback and indexing CLI.
- `tools/brain/`: search, graph, documentation, memory, and code intelligence.
- `tools/hands/`: indexing, project watching, and developer operations.
- `scripts/index_workspace.py`: structural and semantic indexing pipeline.
- `graphrag_core/`: graph configuration, parser facts, manifests, and watcher.
- `memory/`: durable storage and retrieval policy.

The current boundaries are described in
[Current architecture](docs/current-architecture.md), with shared retrieval ownership
in [Retrieval architecture](docs/retrieval_architecture.md).

## Documentation

Start with the [documentation index](docs/README.md). It links to configuration,
operations, LM Studio setup, tool usage, memory, evaluation, and design records.

The pinned `tree_sitter_language_pack` fork and CI policy are documented in
[ts-pack fork policy](docs/ts_pack_fork_policy.md).

## Release process

`VERSION` and the version in `pyproject.toml` must match.

1. Update both version values.
2. Add the matching section to `CHANGELOG.md`.
3. Push tag `vX.Y.Z` or run the release workflow manually.

## License

MIT. See [LICENSE](LICENSE).


The [evidence-driven tooling plan](docs/enterprise-tooling-plan.md) distinguishes
verified tooling behavior from embedded-backend experiments and productivity
claims. See [development appliance setup](docs/development-appliance.md) for
portable Compose configuration and its validation limits.

## Product direction

**FIRE — Find, Integrate, Retrieve, Explain** names the platform direction:
persistent evidence and task continuity across context compaction, with a bounded
working set for each model request. See [FIRE contracts and acceptance](docs/fire-platform.md).

**GOOFY — Graph-Oriented Operations For You** is a proposed assistant/workflow name
alongside the FIRE evidence core. Package names, APIs and MCP tools retain their
current names; the proposal does not imply an autonomous supervisor is implemented.

The planned native embedded distribution combines LadybugDB and LanceDB with
persistent context and bounded, evidence-backed working sets. Context assembly
selects and concatenates relevant source and task memory for MCP clients and an
optional REST inference proxy. Docker is optional packaging. This direction is
not yet a fully integrated embedded backend or an unlimited model context window.
See [context platform direction](docs/context-platform-direction.md) and
[native verification](docs/native-embedded-verification.md).

The [integrated platform plan](docs/integrated-platform-plan.md) coordinates
implementation milestones, native packaging, context continuity and Python migration.

See [Python 3.14 runtime setup and rollback](docs/python314-runtime.md).

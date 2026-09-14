# rest_proxy

`rest_proxy` is a FastAPI inference proxy and an indexed GraphRAG tool server for
AI coding agents. It combines semantic retrieval, structural code relationships,
documentation search, durable memory, and repository operations behind a Model
Context Protocol (MCP) interface.

It is most useful for large or unfamiliar repositories. Exact text search remains
the fastest option for known names; the indexed tools add value for architecture,
callers, implementations, request flow, related files, cross-project dependencies,
and duplicate code.

## Capabilities

- Structural indexing backed by Neo4j.
- Semantic code and documentation retrieval backed by Postgres/pgvector.
- Ranked search with exact fallbacks and duplicate-aware reranking.
- Symbol context, definitions, references, callers, and call-chain traversal.
- Project, directory, request-flow, and dependency summaries.
- Optional Redis-backed session state and durable conversation memory.
- Shared Streamable HTTP MCP daemon with a legacy STDIO fallback.
- Health, freshness, transport-parity, and retrieval-quality checks.

External integrations are optional at proxy startup and should fail open.
Individual indexed features naturally require their corresponding service.

## Requirements

- Python 3.11 or newer.
- `rg` (ripgrep) for exact-search fallbacks.
- LM Studio or another compatible inference endpoint for proxy requests and
  local embeddings.
- Neo4j for structural graph features.
- Postgres with pgvector for semantic retrieval and durable memory.
- Redis for optional hot session state and distributed indexing locks.

You can run the HTTP proxy without enabling every indexed or memory feature.

## Quick start

Create an environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create local configuration:

```bash
cp .env.example .env
```

Review `.env` and enable only the services you intend to use. Replace the Neo4j
and Postgres placeholders before enabling graph or persistence features. See
[Configuration](docs/configuration.md) for safe minimal examples.

Start the shared MCP daemon:

```bash
./scripts/start_brain_server_daemon.sh
./scripts/brain_server_status.sh
```

Connect an MCP client to `http://127.0.0.1:8001/mcp`, then call
`get_indexing_health` and `index_workspace`. The latter accepts the absolute path
of the repository to index.

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

## Recommended agent workflow

For an indexed repository:

1. `get_indexing_health` — verify freshness and alignment.
2. `get_project_overview` — orient on the repository.
3. `search_codebase` — find the likely implementation surface.
4. `get_symbol_context` — inspect one symbol with bounded source evidence.
5. `get_call_chain` or `find_references` — trace behavior across files.

Use `grep_codebase` for exact text and `get_mcp_tool_catalog` when the correct
specialized tool is unclear.

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
[Retrieval architecture](docs/retrieval_architecture.md).

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

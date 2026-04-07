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

After restarting the daemon, refresh or reconnect the MCP client so it picks up
the new process and current tool list.

## STDIO Fallback

The legacy stdio MCP path is still available as a fallback for clients that
cannot connect to the shared HTTP daemon.

To start the stdio server directly:

```bash
python3 mcp_server.py
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

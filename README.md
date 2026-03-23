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
- **LM Studio Optimization**: Automatic model-fallback logic for easy embedding generation.

## 🛠 Prerequisites

- **Python 3.11+** (Conda recommended).
- **PostgreSQL** with `pgvector` extension.
- **Neo4j** (Aura or Local).
- **Redis** (for caching).
- **LM Studio** (for local embeddings).

## 🏃 Quick Start

1. **Configure Environment**: Copy `.env.example` (if available) to `.env` and fill in your database credentials.
2. **Start the Proxy**:
   ```bash
   python3 mcp_server.py
   ```
3. **Index your Workspace**:
   Use the `index_workspace` tool from your AI assistant to perform a full sync.

## 📁 Architecture

- `proxy.py`: Core FastAPI proxy logic.
- `mcp_server.py`: MCP protocol implementation and tool definitions.
- `graph_indexer.py`: Neo4j structural analysis logic.
- `vector_indexer.py`: Postgres semantic indexing logic.
- `memory_store.py`: Centralized persistence layer (Redis, Postgres, Neo4j).
- `memory_retrieval.py`: Embedding generation and search logic.

## 🛡 License

MIT


"""tools/docs/usage_guide.py — GraphRAG usage guide tool."""

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_graph_usage_guide() -> str:
        """
        Returns a comprehensive guide on how to best use this GraphRAG MCP.
        Ideal for 'onboarding' new agents or refreshing search strategies.

        Covers importance metrics, community detection, health checks, and global memory.
        """
        return """
# GraphRAG MCP Tool Usage Guide

### Recommended Workflow:
1. `index_workspace(project_path)` → returns immediately with a `job_id`
   - **Normal path**: wait for `DONE`, then the project should be graph-ready for normal tool use.
2. `get_index_status(job_id)` → poll `RUNNING` / `DONE` / `FAILED` + recent logs
3. `get_indexing_health(project_path, audit=true)` → Level 2 Parsing Fidelity (resolution rate, symbol density)
4. `get_project_overview(project_path)` → health, architecture clusters, key files, and Apple/Cargo build context when present
5. `get_directory_snapshot(project_path, directory_path)` → top files, symbols, **UI asset wiring**, local Apple/Cargo build context, and Cargo crate dependency context for a folder
6. `get_code_importance(project_path)` → PageRank-ranked files, now grouped by Cargo crate when present
6a. `get_code_communities(project_path)` → architectural clusters; Rust workspace fallback now groups by Cargo crate instead of only top-level directory
7. `search_codebase([project_path], query, ...)` → semantic search + metadata filters, including Cargo crate context/filtering for Rust workspaces
8. `get_symbol_context(project_path, symbol_name)` → definition + callers + callees + source

### Workspace Context & Troubleshooting:
- The MCP resolves workspace context from the active project path or the IDE session registry.
- Session-scoped workspace resolution is used internally so multi-workspace IDE sessions can target the correct project without mutating global environment state.
- **Troubleshooting**: If the server crashes or dependencies like `starlette` are missing, ensure you are running in the `lmproxy` conda environment:
  `conda activate lmproxy` (Python 3.11.15)

### Call Graph Traversal:
- `get_call_chain(project_path, symbol_name, depth=3, direction='down', file_path=None, signature=None)` → trace CALLS N hops
- `get_call_chain(project_path, symbol_name, depth=3, direction='up', file_path=None, signature=None)` → trace callers N hops

### File & Symbol Inspection (no indexing required):
- `describe_file("", "/abs/path/file.swift")` → fast outline
- `describe_file(project_path, "rel/path")` → symbols + semantic preview
- `get_directory_snapshot(project_path, directory_path, limit=5)` → architectural onboarding + **UI/Asset wiring** (HTML -> JS -> API) + Apple/Cargo ownership context
- `get_related_files(project_path, file_path)` → structurally related files; for Rust workspaces this now prefers same-crate and directly connected crate files before generic import similarity
- `list_symbol_matches(project_path, query, limit=30)` → name/signature substring matches (supports EnumCase)
- `extract_function_body(file_path, symbol_name)` → exact source by AST
- `extract_class_interface(file_path, class_name)` → public method signatures
- `find_symbol_usages(file_path, symbol_name)` → intra-file usages by AST
- `swift_doc_lookup(file_path, symbol_name)` → indexed Swift docs/USR when available, then SourceKitten fallback

### Documentation Tools:
- `research_and_index(topic, query, max_urls=5)` → search + crawl + index in one call
- `research_documentation(topic, query)` → web search for doc URLs
- `download_documentation(urls, topic)` → crawl + embed + index
- `search_documentation(query, topic?)` → RRF hybrid search over indexed docs (topic optional; global search is supported)
- `delete_documentation(topic, url_patterns?, allow_all=False, dry_run=False)` → delete doc chunks
- `list_documentation_sources(topic?, limit=20)` → list topics or domains
- `llms.txt` is validated; invalid links fall back to sitemap discovery

Doc indexing tips:
- Prefer llms.txt or direct markdown URLs for clean ingestion
- Keep topics narrow; avoid indexing test/demo topics you won't query
- `search_documentation` without a topic excludes sources containing test/tmp/scratch/demo/sample/experimental/staging/draft

### Graph & Search Utilities:
- `git_summary(project_path)` → branch + status + recent commits
- `get_changed_symbols(project_path, since='HEAD~1')` → which functions changed
- `grep_codebase(project_path, pattern)` → exact text search (ripgrep)
- `find_references(project_path, symbol_name)` → graph + text references
- `find_code_duplication(project_path, min_similarity=0.92, max_pairs=50, min_tokens=80, same_file_min_tokens=20, max_same_file_pairs_per_file=8, per_chunk=5, sample_size=500, include_paths?, exclude_paths?)` → duplicate detection with exact/normalized hashes, winnowing + small-input fallback, and semantic similarity
  - Winnowing guarantee: matches shorter than `t = w + k − 1` are not guaranteed; small blocks use k-gram/token fallback
  - Tune with `winnow_*` parameters to balance recall vs noise
  - Same-file output can be capped per file and filtered by `same_file_min_tokens`
  - Clone group relationships are `MEMBER_OF_CLONE_GROUP` and `MEMBER_OF_FILE_CLONE_GROUP` (not `IN_*`), and symbol file path is stored on `filepath`
- `get_related_files(project_path, file_path)` → structural neighbors
- `visualize_subgraph(project_path, symbol_name)` → Mermaid subgraph
- `query_graph(cypher)` → raw Neo4j Cypher
- Cypher tip: most relationships do not carry `project_id`; filter on nodes instead (e.g., `MATCH (a {project_id:$pid})-[r]->(b {project_id:$pid})`)
  - Fallback grep uses `rg`; set `LM_PROXY_RG_PATH` if MCP does not inherit your shell PATH
- `trace_symbol_cross_project(symbol, source_project, target_project)` → cross-project trace
- `get_test_coverage_for(project_path, file_path)` → tests that cover a file
- `scripts/check_live_graph_matrix.sh [--reindex --mode incremental]` → standard live smoke matrix for rental, FrameCreator, and draw-things-community using the `lmproxy` runtime
- `get_symbol_imports_overview(project_path, limit=20, include_implicit=false)` → summarize explicit symbol import edges; set `include_implicit=true` only when you intentionally want heuristic Swift implicit-import edges
- `get_symbol_exports_summary(project_path, limit=20, include_paths?, exclude_paths?, symbol_prefix?)` → summarize EXPORTS_SYMBOL edges
- File-level `IMPORTS` edges are now expected to come from `index_workspace`; there is no separate import rebuild tool in the normal workflow.
- Symbol-level `IMPORTS_SYMBOL` / `EXPORTS_SYMBOL` edges are expected to come from `index_workspace` in the normal workflow. `IMPLICIT_IMPORTS_SYMBOL` is heuristic and should be treated as optional/experimental rather than part of the default graph contract.
- `cancel_index_job(job_id)` → cancel a running indexing job
- `get_app_flow_summary(project_path, ui_contains?, model_contains?, service_contains?, include_tests=false, limit=20, as_table=false)` → UI → API → Service → DB paths (includes external API calls)
- `get_backend_flow_summary(project_path, api_contains?, crate_contains?, model_contains?, service_contains?, include_tests=false, limit=20, as_table=false)` → API → Service → DB paths (includes external API calls and grouped Cargo crate context for Rust workspaces)
- `get_apple_build_summary(project_path, source_contains?, resource_contains?, target_contains?, scheme_contains?, limit=20, as_table=false)` → Apple source → resource → target → scheme → workspace paths
- `get_flow_summary(project_path, mode='auto', ui_contains?, api_contains?, crate_contains?, model_contains?, service_contains?, include_tests=false, limit=20, as_table=false)` → UI, backend, Apple build, or CLI flow (auto tries UI → backend → Apple → CLI)
  - Tip: set `include_tests=true` when you want coverage paths from test files too
- `get_topology_summary(project_path)` and `get_heuristic_flow_summary(project_path)` also group Rust workspace results by Cargo crate when available
- Launch edges: enable `TS_PACK_LAUNCH_EDGES=1` to emit `LAUNCHES` file edges; set `TS_PACK_DEBUG_LAUNCH=1` to log launch resolution counts per file during indexing
- `get_indexed_projects(query?)` → list indexed repo paths (filters by id prefix or path substring)

### Memory Tools:
- `search_memory(workspace_id, query, global_search=True)` → semantic recall across past sessions; use at session start to load prior context
- `list_memories(workspace_id, include_global=True)` → audit all stored durable memories for a project
- `add_memory(workspace_id, text)` → persist a durable fact, decision, or next step

**When to call `add_memory` proactively (don't wait to be asked):**
- After discovering a non-obvious config requirement, library gotcha, or API quirk
- After making an architectural decision — record the rationale
- When finishing a task — record what was done and what the next steps are
- When you hit a blocker — record it so the next session starts with context

Note: Rolling session summaries are auto-generated by the proxy gateway for chat clients. MCP agents should use `add_memory` as their durable memory layer.
"""

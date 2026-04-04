"""tools/docs_tools.py — usage guide and documentation helpers."""

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
2. `get_index_status(job_id)` → poll `RUNNING` / `DONE` / `FAILED` + recent logs
3. `get_project_overview(project_path)` → health, architecture clusters, key files
4. `get_code_importance(project_path)` → PageRank-ranked files (requires Neo4j GDS for PageRank)
5. `search_codebase([project_path], query, include_metadata=False, dedupe_files=True, include_debug=False, max_per_file=0, max_per_dir=2, meta_boost=0.005, mode="precise", fallback="none", fallback_ratio=0.4, fallback_max=12, fallback_glob="", exclude_tests=true, languages?, min_imports=0, min_symbols=0, require_diagnostics=False, require_context=False, include_paths?, exclude_paths?)` → semantic search + metadata filters
6. `get_symbol_context(project_path, symbol_name)` → definition + callers + callees + source

### Call Graph Traversal:
- `get_call_chain(project_path, symbol_name, depth=3, direction='down', file_path=None, signature=None)` → trace CALLS N hops
- `get_call_chain(project_path, symbol_name, depth=3, direction='up', file_path=None, signature=None)` → trace callers N hops

### File & Symbol Inspection (no indexing required):
- `describe_file("", "/abs/path/file.swift")` → fast outline
- `describe_file(project_path, "rel/path")` → symbols + semantic preview
- `list_symbol_matches(project_path, query, limit=30)` → name/signature substring matches (supports EnumCase)
- `extract_function_body(file_path, symbol_name)` → exact source by AST
- `extract_class_interface(file_path, class_name)` → public method signatures
- `find_symbol_usages(file_path, symbol_name)` → intra-file usages by AST
- `swift_doc_lookup(file_path, symbol_name)` → SourceKitten docs + location

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
- `get_symbol_imports_summary(project_path, limit=20)` → summarize IMPORTS_SYMBOL edges (deprecated; use get_symbol_imports_overview)
- `get_symbol_imports_overview(project_path, limit=20, include_implicit=true)` → summarize explicit + implicit symbol import edges
- `get_symbol_exports_summary(project_path, limit=20, include_paths?, exclude_paths?, symbol_prefix?)` → summarize EXPORTS_SYMBOL edges
- `rebuild_symbol_graph(project_path)` → rebuild symbol-level IMPORTS/EXPORTS graph
- `cancel_index_job(job_id)` → cancel a running indexing job
- `get_app_flow_summary(project_path, ui_contains?, model_contains?, service_contains?, include_tests=false, limit=20, as_table=false)` → UI → API → Service → DB paths (includes external API calls)
- `get_backend_flow_summary(project_path, api_contains?, model_contains?, service_contains?, include_tests=false, limit=20, as_table=false)` → API → Service → DB paths (includes external API calls)
 - `get_flow_summary(project_path, mode='auto', ui_contains?, api_contains?, model_contains?, service_contains?, include_tests=false, limit=20, as_table=false)` → UI, backend, or CLI flow (auto tries UI → backend → CLI)
  - Tip: set `include_tests=true` when you want coverage paths from test files too
  Example:
  `get_app_flow_summary("/Users/michaelmarler/Projects/rental", ui_contains="lease-detail", model_contains="Lease", service_contains="Lease", limit=50, as_table=true)`
- Launch edges: enable `TS_PACK_LAUNCH_EDGES=1` to emit `LAUNCHES` file edges; set `TS_PACK_DEBUG_LAUNCH=1` to log launch resolution counts per file during indexing
- `get_language_pack_status()` → available vs manifest languages (auto-download status)
- `get_indexed_projects(query?)` → list indexed repo paths (filters by id prefix or path substring)

### Memory Tools:
- `search_memory(session_id, query, global_search=True)` → cross-project recall
- `list_memories(session_id, include_global=True)` → audit stored memories
- `add_memory(session_id, text)` → persist a durable note
"""

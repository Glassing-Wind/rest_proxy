# GraphRAG Tool Usage Guide

This is the plain Markdown replacement for the removed `get_graph_usage_guide` MCP tool.

Use it to onboard new agents to the current GraphRAG tool surface and normal workflow.

See also:

- [`/Users/michaelmarler/Projects/rest_proxy/docs/tool_product_audit.md`](/Users/michaelmarler/Projects/rest_proxy/docs/tool_product_audit.md)
  - product audit of which tools are actually first-class vs fallback/debug/admin surfaces

## Recommended Workflow

1. `index_workspace(workspace_id)`
   - Starts indexing and returns a `job_id`.
   - Normal path: wait for `DONE`, then the project should be graph-ready.
2. `get_index_status(job_id)`
   - Polls `RUNNING`, `DONE`, or `FAILED` and returns recent logs.
   - If the ID is wrong or stale, the response lists active jobs with status,
     elapsed time, and project path.
   - In strict multi-session mode, jobs from other sessions are hidden unless
     an admin intentionally uses `cancel_index_job(job_id, force=true)`.
3. `get_indexing_health(workspace_id, audit=true)`
   - Checks indexing freshness and parsing fidelity.
4. `get_project_overview(workspace_id)`
   - Best first graph tool for a new repo.
   - Summarizes health, architecture, important files, and Apple/Cargo context when present.
5. Use focused follow-up tools based on the repo shape:
   - `get_directory_snapshot(workspace_id, directory_path)`
   - `get_code_importance(workspace_id)`
   - `get_code_communities(workspace_id)`
   - `search_codebase([workspace_id], query, ...)`
   - `get_symbol_context(workspace_id, symbol_name)`

## Preferred First-Class Tools

If multiple tools could answer the question, prefer these first:

- `get_project_overview`
- `search_codebase`
- `get_symbol_context`
- `get_call_chain`
- `find_references`
- `describe_file`
- `get_indexing_health`
- `trace_graph_provenance`

These are the tools that should normally beat raw grep, raw Cypher, or manual graph inspection.

## Core Graph Tools

- `get_project_overview(workspace_id)`
  - Best onboarding tool for unfamiliar codebases.
- `get_directory_snapshot(workspace_id, directory_path, limit=5)`
  - Architectural landing page for a folder.
  - Includes local coupling and Apple/Cargo ownership context when available.
- `get_code_importance(workspace_id)`
  - PageRank-ranked files.
  - For Rust workspaces, output is grouped by Cargo crate.
- `get_code_communities(workspace_id)`
  - Architectural clusters.
  - Cargo-aware for Rust workspaces.
- `get_related_files(project_path, file_path)`
  - Structural neighbors and likely blast radius.
  - For Rust workspaces, prefers same-crate and directly connected crate files.

## Flow Tools

- `get_app_flow_summary(workspace_id, ...)`
  - UI -> API -> Service -> DB paths.
  - Best for full-stack JS/TS or mixed web repos where those layers should exist.
  - Empty results diagnose missing source/edge coverage and route to `get_flow_summary(mode='auto')`.
- `get_backend_flow_summary(workspace_id, ...)`
  - Returns connected API/service/database paths when attribution is concrete.
  - Falls back to route-handler inventory with an explicit partial-coverage
    warning instead of binding file-level service edges to individual routes.
  - Empty results include measured route/service/database graph coverage and a
    next action.
  - API -> Service -> DB paths.
  - Supports `crate_contains` for Rust workspaces.
  - For non-app Rust library repos, may return guidance instead of pretending a backend path exists.
- `get_apple_build_summary(workspace_id, ...)`
  - Apple source -> resource -> target -> scheme -> workspace paths.
- `get_flow_summary(workspace_id, mode='auto' | 'ui' | 'backend' | 'apple' | 'cli', ...)`
  - Convenience entrypoint that picks the most relevant flow view.

## Internal Fallbacks

Some fallback summaries still exist internally inside the graph layer, especially
behind `get_flow_summary(...)`, but they are not preferred standalone user-facing tools.

## Search and Symbol Tools

- `search_codebase([workspace_id], query, ...)`
  - Hybrid semantic + full-text search.
  - Supports Cargo crate filtering and metadata.
- `grep_codebase(workspace_id, pattern, file_glob='')`
  - Exact text search.
- `find_references(workspace_id, symbol_name)`
  - Cross-file references from graph + text.
- `get_symbol_context(workspace_id, symbol_name)`
  - Definition, callers, callees, and source preview.
- `get_call_chain(workspace_id, symbol_name, depth=3, direction='down' | 'up', ...)`
  - Multi-hop call tracing.
- `list_symbol_matches(project_path, query, limit=30, kinds='')`
  - Name/signature substring matches.
- `find_symbol_usages(file_path, symbol_name)`
  - Intra-file AST usages.
- `extract_function_body(file_path, symbol_name)`
  - Exact source extraction.
- `extract_class_interface(file_path, class_name)`
  - Public signature view.
- `swift_doc_lookup(file_path, symbol_name)`
  - Indexed Swift docs first, then SourceKit fallback.

## File Inspection Without Full Indexing

- `describe_file("", "/abs/path/to/file")`
  - Fast outline for one file.
- `describe_file(project_path, "relative/path")`
  - Symbols plus semantic preview.

## Documentation Tools

- `research_and_index(topic, query, max_urls=5)`
  - Search + crawl + index in one step.
- `research_documentation(topic, query)`
  - Find candidate documentation URLs.
- `download_documentation(urls, topic)`
  - Crawl and index chosen docs.
- `search_documentation(query, topic='')`
  - Search indexed docs.
- `list_documentation_sources(topic='', limit=20)`
  - List indexed documentation topics and domains.
- `delete_documentation(topic, url_patterns='', allow_all=False, dry_run=False)`
  - Remove indexed doc chunks.

## Memory Tools

- `search_memory(workspace_id, query, global_search=True)`
  - Pull durable context from prior sessions.
- `list_memories(workspace_id, include_global=True)`
  - Audit stored durable memories.
- `add_memory(workspace_id, text)`
  - Record a durable fact, decision, gotcha, or next step.

Use `add_memory(...)` proactively when you discover:

- a non-obvious config requirement
- a library or API gotcha
- an architectural decision and rationale
- a blocker worth preserving for future sessions

## Operational Notes

- Deterministic graph build now happens during `index_workspace`.
- Separate graph rebuild tools were intentionally removed.
- `IMPLICIT_IMPORTS_SYMBOL` is heuristic and experimental, not part of the default graph contract.
- Semantic chunk metadata is producer-owned by `tree-sitter-language-pack`.
  - `rest_proxy` validates the shared semantic chunk contract during indexing.
  - Missing required producer fields should fail indexing loudly instead of being backfilled in Python.
  - If producer metadata changes without changing chunk text, do a full `rebuild` for affected repos; incremental indexing can keep old semantic rows because chunk ids stay stable.
- For custom Codex desktop Streamable HTTP MCP config, a minimal hand-edited `~/.codex/config.toml` entry is more reliable than the current UI save flow when the UI hits null-serialization bugs.

## Debugging Bad Graph Edges

If a call edge or file-graph link looks wrong, debug the producer stages first.

Use optional one-off env vars when reindexing:

- `TS_PACK_DEBUG_PROVENANCE_SYMBOL`
- `TS_PACK_DEBUG_PROVENANCE_FILE`

These emit `[ts-pack-provenance] ...` lines from:

- parse-stage call extraction
- exact call resolution
- finalize-stage `CALLS_FILE` / `FILE_GRAPH_LINK` materialization

This is the preferred path for “where did this edge come from?” investigations. These flags are temporary debug inputs and should not be added to `.env` by default.

## Swift Notes

- Swift-heavy repos still need special validation at the tool layer, not only at indexing time.
- `get_call_chain(..., direction="up")` may need semantic caller fallback for SwiftUI/component composition because those relationships are not always emitted as CALLS edges.
- Swift protocol conformers should come from `IMPLEMENTS_TYPE` graph edges
  after rebuilding with the current pinned ts-pack fork. If `EventLoop`-style
  protocol conformers fall back to source snippets, first suspect stale
  producer/index data rather than adding a tool-layer rescue.
- `find_references` and `get_related_files` should prefer grouped cross-file Swift source hits and suppress low-signal markdown/session paths.
- After Swift semantic metadata changes in the producer, do a full `rebuild` on affected repos before judging tool quality.

## Repo-Specific Notes For `rest_proxy`

- `graphrag-brain` is exposed through FastMCP Streamable HTTP in:
  - `/_mcp.py`
  - `/brain_server.py`
- Supported shared-client mode today:
  - shared HTTP daemon at `http://127.0.0.1:8001/mcp`
  - manual watcher activation with `watch_project(path)` / `unwatch_project(path)`
  - explicit roots-capable client sync with no-argument `watch_project()`
  - no automatic workspace inference by default
- Streamable HTTP is now stateful so stale `Mcp-Session-Id` values fail with `404` after a restart.
- Normal debugging path for MCP availability:
  1. check `http://127.0.0.1:8001/health`
  2. confirm `~/.codex/config.toml`
  3. use a fresh Codex conversation if an older one is stuck on stale MCP state
- Restart verification:
  1. `curl -sS -D - http://127.0.0.1:8001/fingerprint`
  2. confirm `x-graphrag-boot-id` changed after restart
  3. confirm `x-graphrag-tool-fingerprint` matches the expected tool set
  4. if `x-graphrag-session-known: 0` or `/health` shows `"known_session": false`, reconnect the MCP client or start a fresh conversation
- Protocol smoke/regression scripts:
  1. `python scripts/check_mcp_protocol.py`
  2. `./scripts/check_mcp_stale_session_restart.sh`

# GraphRAG Tool Usage Guide

This is the plain Markdown replacement for the removed `get_graph_usage_guide` MCP tool.

Use it to onboard new agents to the current GraphRAG tool surface and normal workflow.

## Recommended Workflow

1. `index_workspace(workspace_id)`
   - Starts indexing and returns a `job_id`.
   - Normal path: wait for `DONE`, then the project should be graph-ready.
2. `get_index_status(job_id)`
   - Polls `RUNNING`, `DONE`, or `FAILED` and returns recent logs.
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
- `get_topology_summary(project_path)`
  - Compact topology overview.
  - Prefers non-test files first.
- `get_heuristic_flow_summary(project_path)`
  - Higher-level inferred paths.
  - For Rust library workspaces, falls back to Cargo crate dependency paths when needed.

## Flow Tools

- `get_app_flow_summary(workspace_id, ...)`
  - UI -> API -> Service -> DB paths.
  - Best for JS/TS or mixed app repos.
- `get_backend_flow_summary(workspace_id, ...)`
  - API -> Service -> DB paths.
  - Supports `crate_contains` for Rust workspaces.
  - For non-app Rust library repos, may return guidance instead of pretending a backend path exists.
- `get_apple_build_summary(workspace_id, ...)`
  - Apple source -> resource -> target -> scheme -> workspace paths.
- `get_flow_summary(workspace_id, mode='auto' | 'ui' | 'backend' | 'apple' | 'cli', ...)`
  - Convenience entrypoint that picks the most relevant flow view.

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
- For custom Codex desktop Streamable HTTP MCP config, a minimal hand-edited `~/.codex/config.toml` entry is more reliable than the current UI save flow when the UI hits null-serialization bugs.

## Repo-Specific Notes For `rest_proxy`

- `graphrag-brain` is exposed through FastMCP Streamable HTTP in:
  - `/_mcp.py`
  - `/brain_server.py`
- Supported shared-client mode today:
  - shared HTTP daemon at `http://127.0.0.1:8001/mcp`
  - manual watcher activation with `watch_project` / `unwatch_project`
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

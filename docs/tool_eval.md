# GraphRAG Brain Tool Evaluation

This document evaluates the tools provided by the `graphrag-brain` MCP server, reporting on their usefulness, required improvements, and other pertinent details.

## Evaluation Log: First 5 Tools

| Tool | Usefulness | Improvements Needed | Pertinent Details |
| :--- | :--- | :--- | :--- |
| `add_memory` | **High** | Add support for tags/categories and importance levels for better organization. | **Tested**: Successfully added a memory and verified with `list_memories`. Persistent and reliable. |
| `cancel_index_job` | **Medium** | None for the current admin workflow. | **Fixed**: Strict session-locking now explains the denial and supports `force=True` for an explicit admin override. |
| `delete_documentation` | **Medium** | Implement "delete by age" or "unused" filters. Link with usage metrics. | **Tested**: Safety check intercepted attempt with a hint. Excellent guardrail against accidental wipes. |
| `describe_file` | **Very High** | Include an LLM-generated natural language summary in the output. | **Tested**: Correctly identified 5 symbols in `mcp_server.py`. Fails if file/path is ambiguous or parser lags. |
| `download_documentation` | **High** | Improve status polling/reporting for background crawl jobs. | **Value**: Essential for expanding context beyond the repo. Idempotent updates are a plus. |
| `extract_class_interface` | **High** | Include decorators (e.g., `@classmethod`) and property markers. | **Tested**: Successfully extracted `WorkspaceRegistry` methods. Perfect for quick API surface review. |
| `extract_function_body` | **Very High** | None - very precise. | **Tested**: Extracted `get_project_id` cleanly using AST. Superior to manual line-range reading. |
| `find_code_duplication` | **High** | Add directory-level filtering to reduce output volume for large projects. | **Tested**: Found 6 files sharing `_execute_read`. Excellent for identifying refactoring targets. |
| `find_definitions` | **Medium-High** | Keep positioned as a fallback exact-name and cross-project disambiguation tool, not a peer to `get_symbol_context`. | **Tested**: Good exact-name lookup, but richer workflows should prefer `get_symbol_context`, `list_symbol_matches`, or `search_codebase`. |
| `find_references` | **Critical** | Group semantic hits by file to further reduce noise. | **Tested**: found 21 graph refs + dozens of semantic mentions. Best-in-class hybrid (graph + semantic) discovery. |
| `find_symbol_usages` | **High** | None - very useful for local scope. | **Tested**: Found 3 hits for `WorkspaceRegistry` in `_helpers.py`. Faster than cross-file search for local work. |
| `get_app_flow_summary` | **Niche/High** | Prefer returning route-qualified paths before broad fallbacks. | **Tested**: Useful once indexing completes; no separate rebuild step should be required. |
| `get_backend_flow_summary` | **Niche/High** | Keep backend symbol filtering tight to avoid frontend noise. | **Tested**: Useful for API/service mapping after a normal index. |
| `get_call_chain` | **Critical** | Include markers for dynamic/inferred calls if possible. | **Tested**: Traced `get_project_id` up 3 levels. Found vast impact across the codebase. Essential for risk assessment. |
| `get_changed_symbols` | **Very High** | Group non-code files (README, .env) separately from "no symbol found" code files. | **Tested**: Listed specific functions changed in `_helpers.py`. Much more actionable than a standard git diff. |
| `get_code_communities` | **High** | Continue comparing live output against real onboarding workflows before promotion. | **Fixed/Tested**: Now opens with a Community Summary, dominant concerns, first cluster recommendation, and omitted-tail accounting. |
| `get_code_importance` | **Critical** | None - PageRank implementation is solid. | **Tested**: Ranked `memory/store_core.py` as most important. Uses functional centrality, not just file size. |
| `get_directory_snapshot` | **Very High** | Add an AI-generated directory purpose summary. | **Tested**: Successfully mapped `proxy/` dependency coupling. Great for "landing" in a new part of the repo. |
| `get_flow_summary` | **High** | Annotate relationships with flow types (e.g., "Request Path" vs "Utility"). | **Tested**: Provided a clean file-to-file dependency map. Much more readable than a raw graph for architecture. |
| `get_index_status` | **Critical** | None for the current async job workflow. | **Fixed**: Missing-ID responses now list active job IDs with status, elapsed time, and project path. Essential for async job management. |
| `get_indexed_projects` | **High** | None - solid source of truth. | **Tested**: Found two registered views for `rest_proxy`. Correctly derived path-to-id mapping. |
| `get_indexing_health` | **Critical** | Explain the impact of 'isolated' files (e.g., "cannot trace call chains"). | **Tested**: Found 69 stale semantic files in `rest_proxy`. Indispensable for ensuring data integrity before reasoning. |
| `get_project_overview` | **Critical** | None - perfect first-call tool. | **Tested**: Summarized `rest_proxy` architecture (memory vs tools) and key files in one shot. Best for onboarding. |
| `get_related_files` | **Very High** | Annotate the *reason* for the relationship (e.g., "caller" vs "importer"). | **Tested**: linked `_helpers.py` to `proxy/config.py`. Essential for identifying blast radius of refactors. |
| `get_symbol_context` | **Critical** | Increase source preview window or add a 'full' flag to prevent truncation. | **Tested**: Single-call dive into `get_project_id`. Replaces 4-5 manual steps. Indispensable for deep-sea coding. |
| `get_symbol_exports_summary` | **Low-Medium** | Add a Python-specific heuristic (symbols not starting with '_') if graph edges are missing. | **Tested**: Returned no results. Highly dependent on granular export-indexing which may be language-specific. |
| `get_symbol_imports_overview` | **High** | Same as above. | **Tested**: Essential for granular refactoring (e.g., "what specifically do we use from this 5k-line module?"). |
| `get_test_coverage_for` | **Very High** | Add semantic similarity search for tests if name/import patterns fail. | **Tested**: Failed to find tests for `_helpers.py`. Vital for TDD workflows to ensure "no regression" before commits. |
| `git_summary` | **High** | None. | **Tested**: Summarized branch, untracked files, and 10+ recent commits. Perfect for checking recent project velocity. |
| `grep_codebase` | **Critical** | Group results by file (implemented). Recommend adding pre-filtering for binary files. | **Fixed**: Updated `_which` to resolve environment-specific `rg` paths. Now successfully searching across all files. |
| `index_workspace` | **Critical** | None. | **Tested**: Successfully identified an existing background process. Safely gates resource-intensive tasks. |
| `lint_project_subset` | **High** | Add a `fix=True` flag to auto-apply linter suggestions. | **Tested**: Ran `ruff` on `_helpers.py`. Great for ensuring high-quality edits before the user sees them. |
| `list_dir` | **Low-Medium** | Add directory/file type markers and sizes for visual parity with shell tools. | **Tested**: Listed `proxy/` contents. Useful but basic. |
| `list_documentation_sources` | **High** | None. | **Tested**: Identified 5 documentation topics including `neo4j-gds`. Vital for verifying what external context is available. |
| `list_memories` | **Critical** | None. | **Tested**: Successfully retrieved 6 architectural and task memories. Essential for cross-session continuity. |
| `list_symbol_matches` | **High** | Include abbreviated signatures in output to help disambiguate similarly named functions. | **Tested**: Found 7 symbols matching 'workspace'. Great for exploratory navigation. |
| `query_graph` | **High (Admin/Expert)** | Keep it hidden from the default user-facing surface; expose only for admin/debug workflows. | **Tested**: Powerful raw Cypher escape hatch, but not a normal product tool and no longer part of the default registered surface. |
| `research_and_index` | **Very High** | None. | **Tested**: Automates the external knowledge acquisition loop. Best for "learning" a new library. |
| `research_documentation` | **Critical** | None. | **Tested**: Found relevant URLs and detected `llms.txt` for Neo4j GDS. Outstanding for contextual research. |
| `search_codebase` | **Critical** | None. | **Tested**: Answered "How is project_id derived?" with high-fidelity chunks. The foundation of codebase understanding. |
| `search_documentation` | **Critical** | None. | **Tested**: Retrieved specific `maxIterations` parameter for Neo4j GDS. Outstanding for technical accuracy. |
| `search_memory` | **Critical** | None. | **Tested**: Recalled architectural decisions from previous sessions. Key to agent consistency. |
| `swift_doc_lookup` | **Niche** | Requires SourceKitten. Only works for Swift projects. | **Value**: Essential for Apple platform development but irrelevant for Python/JS repos. |
| `trace_symbol_cross_project` | **Very High** | None. | **Value**: Invaluable for gRPC and monorepo workflows to trace impact across boundaries. |
| `unwatch_project` | **Medium** | None. | **Tested**: Companion tool for resource management. |
| `visualize_subgraph` | **Critical** | None. | **Tested**: Generated a Mermaid diagram for `get_project_id`. Best tool for immediate mental mapping of a symbol's neighborhood. |
| `watch_project` | **High** | None. | **Tested**: Explicitly pins a repo for manual background watching. |

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
| `extract_class_interface` | **High** | None for known-file API inspection. | **Fixed/Tested**: Interfaces now include parser signatures, decorators such as `@classmethod`, and explicit property/member kinds. |
| `extract_function_body` | **Very High** | None - very precise. | **Tested**: Extracted `get_project_id` cleanly using AST. Superior to manual line-range reading. |
| `find_code_duplication` | **High** | None for focused duplicate triage. | **Fixed/Tested**: Supports an explicit recursive `directory_path` scope in addition to include/exclude globs. |
| `find_definitions` | **Medium-High** | Keep positioned as a fallback exact-name and cross-project disambiguation tool, not a peer to `get_symbol_context`. | **Tested**: Good exact-name lookup, but richer workflows should prefer `get_symbol_context`, `list_symbol_matches`, or `search_codebase`. |
| `find_references` | **Critical** | Continue validating grouped semantic output against large repos. | **Fixed/Tested**: Semantic mentions now group by file with line-level snippets retained, reducing flat-list noise while preserving graph + semantic discovery. |
| `find_symbol_usages` | **High** | None - very useful for local scope. | **Tested**: Found 3 hits for `WorkspaceRegistry` in `_helpers.py`. Faster than cross-file search for local work. |
| `get_app_flow_summary` | **Niche/High** | Prefer returning route-qualified paths before broad fallbacks. | **Tested**: Useful once indexing completes; no separate rebuild step should be required. |
| `get_backend_flow_summary` | **Niche/High** | Keep backend symbol filtering tight to avoid frontend noise. | **Tested**: Useful for API/service mapping after a normal index. |
| `get_call_chain` | **Critical** | Include markers for dynamic/inferred calls if possible. | **Tested**: Traced `get_project_id` up 3 levels. Found vast impact across the codebase. Essential for risk assessment. |
| `get_changed_symbols` | **Very High** | None for the current pre-commit review workflow. | **Fixed/Tested**: Changed symbols, source files without detected definitions, and non-code/support files are reported separately. Much more actionable than a standard git diff. |
| `get_code_communities` | **High** | Continue comparing live output against real onboarding workflows before promotion. | **Fixed/Tested**: Now opens with a Community Summary, dominant concerns, first cluster recommendation, and omitted-tail accounting. |
| `get_code_importance` | **Critical** | None - PageRank implementation is solid. | **Tested**: Ranked `memory/store_core.py` as most important. Uses functional centrality, not just file size. |
| `get_directory_snapshot` | **Very High** | Add an AI-generated directory purpose summary. | **Tested**: Successfully mapped `proxy/` dependency coupling. Great for "landing" in a new part of the repo. |
| `get_flow_summary` | **High** | Annotate relationships with flow types (e.g., "Request Path" vs "Utility"). | **Tested**: Provided a clean file-to-file dependency map. Much more readable than a raw graph for architecture. |
| `get_index_status` | **Critical** | None for the current async job workflow. | **Fixed**: Missing-ID responses now list active job IDs with status, elapsed time, and project path. Essential for async job management. |
| `get_indexed_projects` | **High** | None - solid source of truth. | **Tested**: Found two registered views for `rest_proxy`. Correctly derived path-to-id mapping. |
| `get_indexing_health` | **Critical** | Continue validating audit hints against real stale/isolated repo states. | **Fixed/Tested**: Audit mode now explains isolated-file impact on related-file, call-chain, and blast-radius completeness. Indispensable for ensuring data integrity before reasoning. |
| `get_project_overview` | **Critical** | None - perfect first-call tool. | **Tested**: Summarized `rest_proxy` architecture (memory vs tools) and key files in one shot. Best for onboarding. |
| `get_related_files` | **Very High** | Continue validating relationship labels across more repo shapes. | **Fixed/Tested**: Structural results now label caller/importer/sibling/import relationships explicitly, making blast-radius review easier without raw graph inspection. |
| `get_symbol_context` | **Critical** | None for bounded source inspection. | **Fixed/Tested**: Configurable line/character caps and `full_source_preview` can return the indexed symbol span without unbounded tool output. |
| `get_symbol_exports_summary` | **High** | Continue promoting only after more cross-language workflow evidence. | **Fixed/Tested**: Falls back to visibility metadata and Python non-underscore public naming when export edges are absent. |
| `get_symbol_imports_overview` | **High** | Report index-health guidance when symbol import edges are absent. | **Tested**: Essential for granular refactoring (e.g., "what specifically do we use from this 5k-line module?"). |
| `get_test_coverage_for` | **Very High** | None for current test-discovery workflows. | **Fixed/Tested**: Falls through name, graph import, exact text, route, and semantic test-chunk strategies with role-aware filtering. |
| `git_summary` | **High** | None. | **Tested**: Summarized branch, untracked files, and 10+ recent commits. Perfect for checking recent project velocity. |
| `grep_codebase` | **Critical** | Group results by file (implemented). Recommend adding pre-filtering for binary files. | **Fixed**: Updated `_which` to resolve environment-specific `rg` paths. Now successfully searching across all files. |
| `index_workspace` | **Critical** | None. | **Tested**: Successfully identified an existing background process. Safely gates resource-intensive tasks. |
| `lint_project_subset` | **High** | None for focused lint workflows. | **Fixed/Tested**: `fix=True` applies supported Ruff/SwiftLint fixes and reports remaining issues. |
| `list_documentation_sources` | **High** | None. | **Tested**: Identified 5 documentation topics including `neo4j-gds`. Vital for verifying what external context is available. |
| `list_memories` | **Critical** | None. | **Tested**: Successfully retrieved 6 architectural and task memories. Essential for cross-session continuity. |
| `list_symbol_matches` | **High** | None for the current exploratory lookup workflow. | **Fixed/Tested**: Results include normalized signatures, with long signatures abbreviated to keep similarly named symbols easy to scan and disambiguate. |
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

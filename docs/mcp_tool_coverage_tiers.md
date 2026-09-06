# MCP Tool Coverage Tiers

This document tracks which MCP tools matter most for day-to-day trust and what
kind of coverage each tool has today.

For the current trust goal, current repo position, and the remaining
workflow-driven hardening work, see
[Tool trust status](tool_trust_status.md).

Coverage types:

- `unit/script`: targeted implementation tests or script tests
- `live`: exercised by the live graph regression harness on real indexed repos
- `mcp parity`: exercised through the real Streamable HTTP MCP daemon and
  compared against direct in-process invocation
- `tool-choice`: covered indirectly by tool-selection evals

Standard gate policy:

- The default retrieval-quality gate should cover Tier 1 tools, promoted Tier 2
  tools, Apple/build trust cases, and workflow cases that protect real
  investigation behavior.
- Avoid adding one-off historical cases to the default gate unless they protect
  a current trust boundary.
- Prefer workflow coverage for secondary tools when the question is usefulness,
  not just correctness.

## Tier 1: Core User-Facing Investigation Tools

These are the tools we expect to reach for first during normal codebase work.

| Tool | Current coverage |
| --- | --- |
| `get_indexing_health` | unit/script, live, mcp parity |
| `resolve_graph_project` | unit/script, live, mcp parity |
| `get_project_overview` | unit/script, live, mcp parity |
| `search_codebase` | unit/script, live, mcp parity, tool-choice |
| `get_symbol_context` | unit/script, live, mcp parity, tool-choice |
| `find_references` | unit/script, live, mcp parity, tool-choice |
| `get_call_chain` | unit/script, live, mcp parity, tool-choice |
| `get_directory_snapshot` | unit/script, live, mcp parity |
| `find_definitions` | unit/script, mcp parity, tool-choice |
| `list_symbol_matches` | unit/script, mcp parity |
| `grep_codebase` | unit/script, mcp parity |
| `describe_file` | unit/script, mcp parity, tool-choice |
| `trace_graph_provenance` | unit/script, live, mcp parity, tool-choice |
| `get_mcp_tool_catalog` | unit/script, mcp parity |

Notes:

- `get_mcp_tool_catalog` is the product-positioning surface for tool choice.
  It is intentionally lightweight and contract-tested so newly registered MCP
  tools must state when they are worth reaching for.

## Tier 2: Secondary Architectural / Triage Tools

Important, but not as central as Tier 1 for most code investigation paths.
Promoted Tier 2 tools are included in the standard gate because they have proven
useful in real workflows; unpromoted tools stay on unit/script coverage until a
repo or investigation path justifies stronger coverage.

| Tool | Current coverage |
| --- | --- |
| `get_related_files` | unit/script, live, mcp parity, workflow |
| `get_code_importance` | unit/script, live, mcp parity, workflow |
| `get_code_communities` | unit/script, live, mcp parity, workflow |
| `get_repo_dependency_summary` | unit/script, live, mcp parity |
| `get_flow_summary` | unit/script, live, mcp parity |
| `get_app_flow_summary` | unit/script, live, mcp parity |
| `get_backend_flow_summary` | unit/script, live, mcp parity |
| `get_apple_build_summary` | unit/script, live, mcp parity, workflow |
| `get_symbol_exports_summary` | unit/script, live, mcp parity, workflow |
| `query_graph` | unit/script, live |
| `get_symbol_imports_overview` | unit/script, live, mcp parity, workflow |
| `visualize_subgraph` | unit/script, live, mcp parity, workflow |
| `trace_symbol_cross_project` | unit/script, live, mcp parity, tool-choice, workflow |

Current promoted Tier 2 trust set:

- `get_related_files`
- `get_code_importance`
- `get_code_communities`
- `get_repo_dependency_summary`
- `get_flow_summary`
- `get_app_flow_summary`
- `get_backend_flow_summary`
- `get_apple_build_summary`
- `get_symbol_exports_summary`
- `get_symbol_imports_overview`
- `visualize_subgraph`
- `trace_symbol_cross_project`

`trace_symbol_cross_project` is protected by the
`indexed_dependency_boundary_stack` MCP-only workflow, which traces
`detect_language` from `tree-sitter-language-pack` into `rest_proxy` and checks
that implementation-side consumer evidence is surfaced before support/test
noise.

Still intentionally unpromoted:

- `query_graph`, because it is admin-gated in the normal daemon and is not a
  standard end-user MCP parity target.

## Tier 3: Indexing / Admin / Dev Utilities

These matter operationally, but they are not the main “do I trust the code
investigation tools?” surface.

Examples:

- `index_workspace`
- `get_index_status`
- `cancel_index_job`
- `cleanup_stale_shadow_graph`
- `watch_project`
- `unwatch_project`
- `get_indexed_projects`
- `suggest_indexignore`
- `git_summary` (mcp parity)
- `lint_project_subset`
- `get_test_coverage_for`
- `get_changed_symbols`
- `extract_function_body`
- `extract_class_interface`
- `find_symbol_usages`
- `restart_brain_server`
- `rerank_retrieval_results`
- `analyze_duplicate_results`
- `trace_code_ranking`
- `swift_doc_lookup`

Read-only documentation and memory tools are operational/context surfaces, but
the standard parity smoke now includes representative checks for
`list_documentation_sources`, `search_documentation`, `list_memories`, and
`search_memory` because they are common companion tools once the catalog routes
users toward docs or remembered project context.

Retrieval-QA tools are still support/debug surfaces, but their compact default
contracts are now MCP-parity protected: `rerank_retrieval_results`,
`analyze_duplicate_results`, and `trace_code_ranking` must expose decision-sized
outputs over caller-supplied candidates without requiring live index state.

These should keep strong unit/script coverage. Add live or MCP parity coverage
only if a tool becomes operationally flaky or user-critical.

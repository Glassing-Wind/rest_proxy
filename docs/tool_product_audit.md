# Tool Product Audit

This document is the current product-level audit of the GraphRAG tool surface.

The goal is not to list every available tool. The goal is to say which tools are
actually first-class and should be preferred in real usage, and which tools are
still fallback/debug/admin surfaces.

## First-Class Criteria

A tool is first-class when all of the following are true:

- it is a tool we would choose over raw grep/Cypher/manual DB inspection
- it returns stable, readable output without requiring repo-specific decoding
- it has representative live coverage, not just unit tests
- it works well on more than one repo shape
- it has a clear job and does not duplicate another better tool

If a tool fails one of those, it may still be useful, but it is not first-class.

## First-Class Tools

These are the tools that should be preferred by default.

- `get_project_overview`
  - Best onboarding entrypoint for an unfamiliar repo.
- `search_codebase`
  - Primary code search surface.
  - Now covers definition, usage, entrypoint, and noisy-repo retrieval much better than raw search.
- `get_symbol_context`
  - Primary single-symbol inspection tool.
- `get_call_chain`
  - Primary multi-hop execution-path tool.
- `find_references`
  - Primary cross-file reference lookup.
- `describe_file`
  - Fast file inspection without needing full graph spelunking.
- `get_indexing_health`
  - Primary operational truth for graph freshness and alignment.
- `index_workspace`
  - Primary indexing entrypoint.
- `search_documentation`
  - Primary docs retrieval tool once docs are indexed.
- `trace_graph_provenance`
  - Primary “why does this result/edge exist?” tool for graph/file-link debugging.

## Strong But Narrow Tools

These are good tools, but their domain is narrower or their output is more specialized.

- `get_directory_snapshot`
- `get_repo_dependency_summary`
- `get_app_flow_summary`
  - Preferred for concrete UI-to-route/API tracing in full-stack web repos.
  - Deeper service/database hops are shown only when the graph can attribute
    them without misleading route-level precision.
- `get_backend_flow_summary`
  - Preferred for backend route-handler and downstream-flow inspection.
  - Route-only fallback remains useful without overstating route-specific
    service/database attribution.
  - Declared Spring mappings are now available even when no frontend/client
    call references them.
- `get_flow_summary`
- `get_symbol_exports_summary`
- `get_symbol_imports_overview`
- `get_related_files`
- `trace_symbol_cross_project`
- `find_code_duplication`

These should be preferred when their exact problem shape matches.

## Useful But Not Yet First-Class

These are still worth improving because they do not yet feel like obvious default choices.

- `get_code_communities`
  - Useful secondary orientation tool. It now opens with a compact community
    summary, dominant concerns, and omitted-tail accounting, but remains behind
    `get_project_overview` for normal first-stop onboarding.
- `get_code_importance`
  - Useful secondary ranking signal with role-aware recommendations, but remains
    behind `get_project_overview` for normal onboarding.
- `find_definitions`
  - Good fallback, but for real work `get_symbol_context`, `list_symbol_matches`, and `search_codebase` are usually better.
- `query_graph`
  - Necessary escape hatch, not a first-class product tool.
  - Admin/debug only and no longer part of the default registered tool surface.

## Admin / Debug / Escape-Hatch Tools

These are not product-quality primary tools and should stay that way.

- `query_graph`
- `list_documentation_sources`
- `delete_documentation`
- `get_index_status`
- `get_indexed_projects`
- `find_symbol_usages`
- `extract_function_body`
- `extract_class_interface`
- `swift_doc_lookup`
- developer/admin indexing tools

These are important operationally, but they should not drive the main user workflow.

## Current Product Gaps

The biggest remaining gaps are not raw capability gaps. They are product-shape gaps.

### 1. Too many overlapping discovery surfaces

We still have several tools that partially overlap:

- `get_project_overview`
- `get_directory_snapshot`
- `get_code_importance`
- `get_code_communities`

The first two are the best user-facing surfaces. `get_code_communities` and
`get_code_importance` are decision-oriented secondary tools for clustered repos
and blast-radius triage, respectively, rather than default onboarding surfaces.

One concrete drift we found: `get_topology_summary` had been discussed in docs
like a user-facing tool, but it is currently an internal helper path rather than
a registered MCP surface.

We are now treating `get_heuristic_flow_summary` the same way: useful as an
internal fallback behind `get_flow_summary`, but not a peer standalone product tool.

### 2. Some tools still feel analysis-oriented rather than decision-oriented

A first-class tool should answer the user’s question directly.

The weaker tools often still provide:

- rankings without a clear recommendation
- topology data without a next action
- architecture blobs without enough prioritization

The retrieval-QA tranche now follows this rule: `rerank_retrieval_results`,
`analyze_duplicate_results`, and `trace_code_ranking` return compact decisions
by default and reserve raw contracts for `include_debug=true`. Ranking traces
show only factors that actually changed a score, and empty traces state the
candidate metadata needed for a useful diagnosis.

### 3. Some tool names still expose implementation shape instead of product intent

Examples:

- `trace_code_ranking`
- `find_definitions`

The names are technically accurate, but they do not describe the best user-facing workflow.

## Preferred Workflow

For normal repo work, prefer this stack:

1. `get_indexing_health`
2. `get_project_overview`
3. `search_codebase`
4. `get_symbol_context`
5. `get_call_chain`
6. `find_references`
7. `trace_graph_provenance` when a graph/search result looks suspect

Everything else should justify itself against that baseline.

## Next Product Tranche

If we want the tool surface to feel genuinely first-class, the next work should be:

1. tighten or demote overlapping discovery tools
2. make weak tools more decision-oriented
3. add live evaluations that compare tool choice against realistic user intents
4. avoid shipping raw capability surfaces as if they were equal to preferred tools

The overlapping discovery tranche now has explicit tool-choice protection:
generic repo onboarding treats `get_code_importance` and `get_code_communities`
as bad first tools, while the catalog still routes targeted "blast radius" and
"clustered architecture" intents to those secondary surfaces with
`get_project_overview` as the preferred prior step.

The support-tool naming issue is also partially mitigated at the catalog layer:
natural wording such as "jump to definition", "why did search rank this result",
"duplicate search results", and "review changed code before commit" routes to
the exact support/dev surfaces even when the tool names remain implementation
shaped.

Common companion workflows are now covered by the same tool-choice contract:
indexed documentation lookup starts with `search_documentation`, documentation
inventory starts with `list_documentation_sources`, memory capture/review starts
with `add_memory`/`list_memories`, changed-code review starts with
`git_summary` and `get_changed_symbols`, and retrieval-ranking debug starts with
`trace_code_ranking` before lower-level duplicate/rerank helpers.

Flow-tool routing is similarly explicit: full-stack UI/API/service/DB wording
routes to the app-flow summary, while backend request-to-database wording routes
to the backend flow summary. A positive `rental` workflow now protects concrete
UI-to-route/API output through both live regression and MCP parity; ambiguous
file-level service/database attribution remains suppressed.

Route/controller discovery is now also protected at the product layer: natural
prompts such as "where is this route handled" or "gin route handler" route to
`search_codebase`, where the lower-level request-handler/controller ranking
signals already apply.

The bar is simple:

- if we would not personally choose the tool first, it should not be treated as a peer first-class surface

## Tool Choice Goldens

Intent-to-tool expectations now live in:

- [`/Users/michaelmarler/Projects/rest_proxy/benchmarks/tool_choice_goldens.json`](/Users/michaelmarler/Projects/rest_proxy/benchmarks/tool_choice_goldens.json)

This is intentionally small. It is not a giant benchmark harness yet. It is a
repo-owned statement of which tools should be preferred for common intents.

Canonical preferred workflows are also checked in CI via:

- [`/Users/michaelmarler/Projects/rest_proxy/scripts/run_tool_choice_eval_suite.py`](/Users/michaelmarler/Projects/rest_proxy/scripts/run_tool_choice_eval_suite.py)

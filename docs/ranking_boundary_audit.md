# Ranking Boundary Audit

This audit tracks where ranking, fallback, and rescue policy currently belongs
across the Rust producer, Python ingest, memory retrieval core, and MCP wrapper
layers.

## Boundary Rules

- `ts-pack` owns durable source facts: symbols, spans, file roles, route facts,
  inherited/implemented types, generated/test/support classification, and
  parser-owned resource facts.
- Indexing/ingest owns persistence, contract versions, stale detection,
  promotion of lower-level metadata onto graph nodes and embedding rows, and
  compatibility fallbacks while editable installs catch up.
- `memory/*` owns shared retrieval and ranking policy: query interpretation,
  candidate loading, exact/path/member rescues, role-aware ranking, duplicate
  handling, cross-project trace ranking, and compact ranking contracts.
- MCP tools own registration, argument shape, error wrapping, and final
  user-facing presentation. They should not invent durable search facts or carry
  reusable ranking policy.

## Current Findings

### Moved In This Pass

- `trace_symbol_cross_project` no longer owns its definition ranking,
  semantic-consumer ranking, SQL/graph orchestration, or source-preview
  selection in the MCP wrapper.
- New core module: `memory/cross_project_trace.py`.
- `tools/brain/search/cross_project.py` is now a thin MCP wrapper that delegates
  to `memory.cross_project_trace.trace_symbol_cross_project_core`.
- `memory.store.trace_symbol_cross_project_core` is exposed as the reusable
  non-MCP entry point.

### Correctly Owned Today

- `search_codebase` delegates retrieval and ranking to
  `memory.store.search_codebase_core`; the wrapper handles embeddings, result
  formatting, telemetry emission, and fallback display.
- Documentation search delegates query expansion, topic-family filtering, and
  ranking to `memory.docs_retrieval`.
- Duplicate-aware reranking and implementation ranking traces live under
  `memory/retrieval_*` and are exposed through compact MCP support tools.
- Swift protocol conformer correctness is producer/index owned after the
  attributed-extension ts-pack fix and Swift workspace rebuild.
- `get_directory_snapshot` still owns graph-orientation product ranking, but
  now applies deterministic equal-count graph ordering and augments Swift
  directory dependencies with symbol-overlap evidence when direct import/call
  edges miss central core-module files.

### Still Tool-Specific By Design

- `get_project_overview`, `get_directory_snapshot`, `get_code_importance`, and
  `get_code_communities` contain product ranking for graph orientation rather
  than general code retrieval. Their heuristics should move lower only when
  another frontend needs the same policy or a live miss shows they are
  compensating for missing producer metadata.
- `get_symbol_context`, `get_call_chain`, and `find_references` contain
  symbol-inspection fallbacks that are tool-specific presentation behavior.
  When those fallbacks reveal durable source facts are missing, fix `ts-pack` or
  ingest first.
- `tool_catalog` ranking is product routing, not retrieval ranking. It belongs
  in the MCP/product layer unless another client consumes the same catalog
  contract directly.

## Remaining Watchpoints

- Any new cross-project tracing behavior should enter
  `memory/cross_project_trace.py`, not the MCP wrapper.
- Any new `search_codebase` ranking behavior should enter
  `memory/code_retrieval.py` or a focused `memory/retrieval_*` module.
- Any repeated Swift, Java, route, or file-role fallback should first be
  checked against the current `ts-pack` metadata before adding tool-layer
  rescue logic.
- Any graph-orientation heuristic that becomes useful outside one MCP tool
  should be extracted to a memory or graph-core module with tests before
  broadening its use.
- If a graph-orientation fallback repeatedly recovers the same source fact,
  treat it as producer or ingest drift and fix the durable graph edge rather
  than expanding presentation-only rescue logic.

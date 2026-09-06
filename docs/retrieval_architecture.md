# Retrieval Architecture

## Product rule

If two frontends should receive the same answer, the behavior belongs in the
shared retrieval layer rather than an MCP wrapper. Wrappers own transport,
parameter validation, bounded presentation, and diagnostics.

## Current ownership

- `memory/docs_retrieval.py`: documentation expansion, filtering, and ranking.
- `memory/code_retrieval.py`: semantic, exact, fallback, and final code retrieval.
- `memory/retrieval_*.py`: intent, policy, metadata, scoring, enrichment,
  suppression, telemetry, and contracts.
- `memory/cross_project_trace.py`: cross-project definition and consumer tracing.
- `graphrag_core/` and the pinned ts-pack fork: structural facts and indexing
  metadata that should not be inferred independently by each consumer.
- `tools/brain/`: thin MCP-facing orchestration and result presentation.

## Change standard

1. Reproduce a real workflow weakness.
2. Decide whether it is ingest metadata, retrieval policy, or presentation.
3. Fix it at the lowest shared layer that owns the invariant.
4. Add the smallest golden or live regression that protects the behavior.
5. Avoid compensating for missing graph facts with permanent wrapper heuristics.

The original multi-phase plan is retained as a historical record in
[retrieval_architecture_plan.md](retrieval_architecture_plan.md). Current
readiness and open risks live in [tool_trust_status.md](tool_trust_status.md).

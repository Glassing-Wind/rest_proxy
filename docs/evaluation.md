# Evaluation

The project separates deterministic correctness checks, live integration checks,
retrieval trend evaluation, and comparative agent-product evaluation.

## Local gate

```bash
./scripts/run_ci_checks.sh
```

This runs broad production linting, GraphRAG regressions, MCP lifecycle checks,
and CI-safe service tests. It may start and stop a local MCP daemon.

Focused graph checks:

```bash
./scripts/check_graph_pipeline.sh
```

## Retrieval trust gate

```bash
./scripts/run_retrieval_quality_gate.sh
```

Use this to check tool choice, product positioning, MCP transport parity,
stale-session behavior, and live graph regressions against configured benchmark
repositories.

For one MCP-only investigation workflow:

```bash
python scripts/run_mcp_investigation_pass.py \
  --workflow-id rest_proxy_preferred_investigation_stack
```

## Enterprise trend evaluation

```bash
python scripts/run_enterprise_eval.py --skip-graph
```

This writes historical JSON artifacts under `.runtime/enterprise_eval/` and is
intended for ranking and duplicate-collapse trend analysis rather than a simple
pass/fail trust decision.

## Native-vs-MCP agent benchmark

The comparative benchmark answers whether indexed MCP tools are actually faster
and better than native exact search and file reading.

1. Use the cases in `benchmarks/agent_tooling_cases.json` for both conditions.
2. In the `native` condition, allow only ordinary file listing, exact search,
   file reads, and shell inspection.
3. In the `mcp` condition, allow the GraphRAG MCP tools; native fallback remains
   allowed but must be counted.
4. Record each run using the schema described by
   `benchmarks/agent_tooling_result.schema.json`.
5. Compare the two result files:

```bash
python scripts/compare_agent_tooling.py \
  --native native-results.json \
  --mcp mcp-results.json
```

Report warm indexed time separately from indexing/setup time. Run at least one
fresh, stale, and partially indexed condition. Primary metrics are correctness,
unsupported claims, elapsed time, tool calls, files opened, and input/output
tokens. The comparison script fails when case sets differ or result records do
not satisfy the schema.

## Recorded agent-tooling pilot

The September 6, 2026 pilot lives in `benchmarks/reports/2026-09-06/`.
It records `revision`, `token_measurement`, `elapsed_measurement`, and
`measurement_notes`, with per-case `native_fallback_calls` for MCP. Older schema-v1
records remain readable; missing measurement metadata is reported as unspecified.
The comparator rejects differing revisions and suppresses timing deltas when elapsed measurement methods differ.

Per-case tool latency excludes reasoning, and estimated text tokens exclude repeated
context, tool schemas, and internal model usage. Neither establishes end-to-end speed
or model-token savings. Evidence coverage is substring overlap, not source validation.
CI publishes the saved pilot as an artifact without enforcing performance thresholds.
See `docs/next-session-roadmap.md` for the confirmatory-run protocol and open blockers.

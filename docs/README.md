# Documentation

Start with the root [README](../README.md) for installation and the shortest
working path.

## Operate the system

- [Configuration](configuration.md): feature modes, dependencies, and safe examples.
- [MCP and proxy operations](operations.md): daemon lifecycle and client setup.
- [LM Studio embeddings](lm-studio.md): local model lifecycle and tuning.
- [Large repository indexing](large_repo_indexing.md): safety gates and sizing.
- [Memory](MEMORY.md): persistence, prompt augmentation, and data layout.
- [Dependency security](security.md): blocking audit policy and existing baseline.

## Use and evaluate the tools

- [GraphRAG tool usage](graphrag_tool_usage.md): preferred investigation workflow.
- [Evaluation](evaluation.md): local CI, live checks, and comparative benchmarking.
- [MCP conformance](mcp_conformance_checklist.md): protocol behavior and known gaps.
- [Tool coverage tiers](mcp_tool_coverage_tiers.md): product positioning by tool family.
- [Tool trust status](tool_trust_status.md): dated readiness snapshot and backlog.

## Design records

- [Retrieval architecture](retrieval_architecture.md): current ownership boundaries.
- [Historical retrieval plan](retrieval_architecture_plan.md): archived implementation plan.
- [Duplicate-aware reranking](duplicate_aware_retrieval_reranker.md).
- [Retrieval suppression policy](retrieval_suppression_policy.md).
- [Ranking boundary audit](ranking_boundary_audit.md).
- [ts-pack fork policy](ts_pack_fork_policy.md) and
  [supported languages](ts-pack-languages.md).

Documents containing an “As of” date are snapshots. Prefer current operating
guides and executable checks when a snapshot disagrees with the implementation.

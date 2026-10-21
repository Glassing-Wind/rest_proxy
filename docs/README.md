# Documentation

Start with the root [README](../README.md) for installation and the shortest
working path.

**Current architecture:** use the [canonical architecture guide](current-architecture.md)
for application/deployment boundaries, storage ownership, trust boundaries and current
acceptance limits. Configuration and executable source/checks govern operating details.

**Historical snapshots:** dated studies, status milestones and benchmark reports are
revision-bound evidence. Their old next steps and readiness statements are superseded
by the current architecture and [active roadmap](next-session-roadmap.md) where they
disagree. Keep original evidence intact; a passing historical receipt does not certify
the present working tree or deployment.

## Operate the system

- [Configuration](configuration.md): feature modes, dependencies, and safe examples.
- [MCP and proxy operations](operations.md): daemon lifecycle and client setup.
- [Health and readiness](health.md): process liveness, explicit dependencies and bounded checks.
- [LM Studio embeddings](lm-studio.md): local model lifecycle and tuning.
- [Large repository indexing](large_repo_indexing.md): safety gates and sizing.
- [Native ARM validation](arm-validation.md): runtime/artifact fingerprints,
  Ladybug thread control, disposable workload measurements and ARM CI limits.
- [Memory](MEMORY.md): persistence, prompt augmentation, and data layout.
- [Dependency security](security.md): blocking audit policy and existing baseline.

## Use and evaluate the tools

- [Local evidence workspace](evidence-workspace.md): opt-in source inspection,
  relationship review, private recovery archives and synthetic acceptance.
- [GraphRAG tool usage](graphrag_tool_usage.md): preferred investigation workflow.
- [Evaluation](evaluation.md): local CI, live checks, and comparative benchmarking.
- [MCP conformance](mcp_conformance_checklist.md): protocol behavior and known gaps.
- [Tool coverage tiers](mcp_tool_coverage_tiers.md): product positioning by tool family.
- [Tool trust status](tool_trust_status.md): dated readiness snapshot and backlog.

## Design records

- [Current architecture](current-architecture.md): canonical components, deployment
  profiles, publication protocol, local security boundary and measured limits.
- [Retrieval architecture](retrieval_architecture.md): current ownership boundaries.
- [Historical retrieval plan](retrieval_architecture_plan.md): archived implementation plan.
- [Duplicate-aware reranking](duplicate_aware_retrieval_reranker.md).
- [Retrieval suppression policy](retrieval_suppression_policy.md).
- [Ranking boundary audit](ranking_boundary_audit.md).
- [ts-pack fork policy](ts_pack_fork_policy.md) and
  [supported languages](ts-pack-languages.md).

Documents containing an “As of” date remain snapshots under the policy above.

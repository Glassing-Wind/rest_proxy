# Native vs MCP Agent Tooling

Repository: `rest_proxy` · Cases: 15

| Metric | Native | MCP |
|---|---:|---:|
| Recorded correct (see grading notes) | 100.0% | 100.0% |
| Evidence coverage | 90.0% | 93.3% |
| Mean recorded seconds (NOT comparable) | 20.79 | 1.33 |
| Mean tool calls | 2.67 | 3.80 |
| Mean files opened | 2.53 | 1.87 |
| Recorded tokens (see measurement method) | 58577 | 60784 |
| Unsupported claims | 0 | 0 |
| Setup/index seconds | 0.00 | 7.23 |

MCP index state: `warm`. Setup/index time is excluded from per-case elapsed time.

Token measurement: native `estimated`, MCP `estimated`. Estimated or unspecified values do not establish model token savings.
MCP native fallback calls: 18.
Elapsed measurement: native `end_to_end`, MCP `tool_latency`; tool latency excludes agent reasoning. Differing methods suppress timing deltas.

Native measurement notes: Native-only source inspection pilot, 15 sequential cases in one agent session; shared context and filesystem caches are not reset. elapsed_seconds is actual Date.now wall time from first case search to recorded answer, including reasoning and orchestration. Setup protocol reads were not timed and setup_seconds=0 means no indexing/setup was performed, not zero protocol overhead. Per-case token estimates are ceil((captured tool output characters + command characters)/4) input and ceil(answer characters/4) output; they are NOT total model/billed tokens and exclude inherited context, repeated context, reasoning and orchestration. No per-case model telemetry API was available. Tool calls count executed native inspection shell invocations including failed searches. files_opened counts unique existing source files whose content appeared in command output, including exact search excerpts; failed paths and protocol files excluded. native_fallback_calls=0 because native is the assigned baseline, not a fallback. Correctness is source-based self-assessment, not independent grading. See native-transcript.json for full command/output and timestamps.
MCP measurement notes: Fresh condition-level subagent context, no other condition answers/results read. Cases interleaved/batched within one context using expected_evidence file hints. elapsed_seconds is summed instrumented tool duration, not end-to-end per-case task latency; do not compare as speed advantage. Token estimates ceil((prompt + tool arguments + canonical tool text chars)/4) and ceil(answer chars/4), excluding system/history/reasoning/caching/wrapper duplication. Not actual telemetry. Setup excludes initial skill/tool discovery and AGENTS/case/schema read, timed from post-schema setup marker through health; no indexing performed. See mcp-measurement-notes.md and mcp-evidence-log.json. Post-run audit found automatic structural reindexing and failed semantic updates; warm is the initial state only, not a stable condition. See post-run-index-health.md. This invalidates controlled-index attribution.

Evidence coverage is keyword overlap, not independent verification of correctness.

| Case | Native correct | MCP correct | Native evidence | MCP evidence | Seconds delta (MCP − native) | Calls delta | Tokens delta |
|---|---|---|---:|---:|---:|---:|---:|
| http_mcp_startup | True | True | 100% | 100% | N/A | +2 | -86 |
| proxy_chat_flow | True | True | 100% | 100% | N/A | +1 | +423 |
| memory_mode_gating | True | True | 50% | 50% | N/A | +0 | +390 |
| index_job_recovery | True | True | 100% | 100% | N/A | +2 | +191 |
| semantic_search_ranking | True | True | 100% | 100% | N/A | +2 | +144 |
| duplicate_suppression | True | True | 100% | 100% | N/A | +3 | +88 |
| documentation_pipeline | True | True | 100% | 100% | N/A | +2 | +93 |
| symbol_context | True | True | 100% | 100% | N/A | +1 | +25 |
| test_coverage_lookup | True | True | 100% | 100% | N/A | -1 | -149 |
| watcher_lifecycle | True | True | 100% | 100% | N/A | +2 | +450 |
| stale_mcp_session | True | True | 50% | 100% | N/A | +1 | +409 |
| tool_fingerprint | True | True | 100% | 100% | N/A | +1 | +857 |
| cross_project_trace | True | True | 100% | 100% | N/A | +1 | -148 |
| optional_dependency_failure | True | True | 50% | 50% | N/A | +1 | +787 |
| tool_choice_catalog | True | True | 100% | 100% | N/A | -1 | -1267 |

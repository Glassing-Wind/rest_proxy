# Enterprise tooling follow-up — September 30, 2026

The two identified reliability bugs are fixed and the local CI and live retrieval-quality gates passed. A valid native-versus-GraphRAG agent comparison remains **unproven**.

## Product fixes and verification

- Explicit dependency manifests now affect the MCP runtime fingerprint; recursive directory discovery remains Python-only. Four fingerprint tests passed.
- Cross-project graph traces now restrict the target symbol to the source project. Fifteen offline tests passed. A live Neo4j rollback-only test confirmed correct direct/inferred callers and excluded same-name targets in unrelated, local and shadow projects, including an export alias. Removing the filter reproduced false positives; fixture rollback left no test nodes.
- Removed one confirmed inactive shadow namespace: 4,576 nodes and 379 relationships; zero remained afterward. No active indexing jobs existed at cleanup time.
- Full local CI passed. The complete retrieval-quality gate passed, including MCP lifecycle, investigation workflows and tool parity. The newly added pilot measurement tests passed separately (six tests after availability hardening).
- AGENTS.md now distinguishes the core agent-tooling goal from the optional inference proxy and requires measured evidence for superiority claims.

## Agent pilot outcome

Three questions, two fresh sessions each, requested the same `gpt-6-astra` model and low effort, with alternating condition order. Source and complete index-health hashes stayed stable; the initial index covered 310 files and was healthy/aligned. The CLI did not emit an observed model ID, so the requested model is recorded but not independently verified.

All six answers passed independent source-based review with condition labels and metrics withheld. However, **all six used native shell tools only**. Separate diagnostic prompts reported that GraphRAG MCP tools were unavailable, despite explicit server configuration and required startup. Therefore the configured MCP arm is excluded and no time/token advantage is attributable to GraphRAG. Do not interpret the original runner's `valid: true` as experimental validity: `pilot-results.json` preserves it as `original_runner_valid` and records the corrected exclusion.

The runner now explicitly requires a successful MCP `get_indexing_health` call in every MCP condition before answering. It rejects missing/failed availability evidence. A successful transport-level probe by the parent process does not establish tool exposure to a child agent.

One prior attempt was interrupted by a CLI usage-limit rejection. A subsequent desktop allowance check showed available usage; the complete retry is kept separate. Two earlier setup attempts stopped before inference (ignored user config misclassified as project config, then stale runner source). Failed attempts were not blended into the six-answer results.

## Evidence

- `pilot-results.json`: actual CLI usage, elapsed time, tool counts and corrected experiment validity.
- `baseline.json`: source hashes and stable warm-index identity.
- `independent-review.json` / `.md`, `answers/`, `grading-key.json`: answer review and mapping, retained after grading.
- `mcp-availability.jsonl`: explicit diagnostic finding tools unavailable.
- Full local raw run artifacts: `/tmp/rest-proxy-agent-pilot-20260930d`; interrupted attempt: `/tmp/rest-proxy-agent-pilot-20260930c`.
- CI log: `.runtime/enterprise-ci-2026-09-29.log`; retrieval gate: `.runtime/enterprise-trust-gate-2026-09-30.log`.

Command transcripts were audited: source reads and searches only, with no observed network/service access, edits, tests or benchmark-answer reads. Some documentation search output can expose incidental project context; this is not a perfectly blinded repository. The reviewer found no substantive answer errors, but grading cannot repair a missing treatment condition.

## Remaining work

Resolve MCP tool exposure in isolated fresh CLI sessions and pass the explicit health-call check before rerunning the paired pilot. Then repeat on unfamiliar repositories, degraded indexes and actual code-change tasks. Enterprise reliability also requires ongoing operational/security validation; passing local checks and a small answer-quality review are not an enterprise certification or proof of coding productivity.

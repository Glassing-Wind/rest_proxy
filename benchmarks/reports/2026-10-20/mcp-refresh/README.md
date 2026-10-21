# Shared MCP refresh and VS Code task acceptance

October 20, 2026. Operational follow-up to the
[editor setup](../vscode-development/README.md). Implementation revisions are
`8ba5961` and `e6d873f`. Preexisting unrelated working-tree changes were preserved;
the daemon loads that working tree. This is not a clean release certification.

## Service and index

The listener is owned by launchd's `com.graphrag.brain-server` KeepAlive job.
Refreshes used that owner, avoiding a competing standalone process. The final
daemon fingerprint `f2103291e9d3` matches a fresh current-source import. Readiness
and protocol checks passed; actual live tool names equal current registration:
60 tools under the existing service-backed profile.

**Correction to the original editor report:** embedded, repository-learning, FIRE
and context tools have explicit profile/state gates. Their absence did not prove
an old catalog. A separately measured fingerprint difference established loaded
source drift. No optional profile or tool group was enabled to inflate coverage.

Two scoped incremental jobs are retained: initial refresh `65bceb7f`, then
`5a5009a3` after the watcher failure-handling follow-up. Both structural and semantic
workers exited zero with aligned runs and completed maintenance. Final health
reports **610/610 structural and semantic files**, 421/421 source-eligible files
parsed, and no reported stale/missing files. Health is the service's bounded
freshness report; no deep fidelity audit or retrieval-quality benchmark was run.

The configured local Jina encoder supplied new embeddings. Model, provider,
storage configuration and the pinned-watch file were unchanged. Only these two
expected indexing jobs were created; unrelated pinned projects were not indexed.
Machine-local `.codex/**` settings are now excluded through `.indexignore`.

## Watcher changes and checks

Previously, restored watches started with empty modification maps and immediately
queued indexes for all existing files. Restored subscriptions now baseline on
their first successful scan; new manual watches retain initial indexing. Later
edits, additions and deletions remain detectable. Changes made while the service
was stopped require an explicit incremental index.

An independent review caught that lenient nested traversal/stat failures could
look like deletions. Watcher scans now request the opt-in strict manifest path,
retain their previous/unprimed baseline on failure, log the failure and continue
other roots. Normal index callers retain the existing best-effort manifest policy.
The reviewer confirmed that follow-up resolved the finding.

**25 offline tests passed:** 12 watcher, 2 embedded watcher and 11 manifest tests,
run in separate processes. Changed Python Ruff and diff whitespace checks passed.
Live restart checks created zero startup jobs; only the two explicitly requested
index jobs appeared. Full daemon-changing CI was not run.

## One real task through VS Code

The installed Codex sidebar ran the bounded `RP_VSCODE_MCP_20261020_A` probe and
saved it as **Identify MCP and inference APIs**. UI and rollout metadata identify
`gpt-6.1-sol`, Ultra reasoning, client `codex_vscode`, runtime
`0.162.0-alpha.17.2`. The selected model was inspected and retained. Automatic IDE
context was omitted for this probe so the source came from the named tool reads.

The task used four read-only MCP calls: one health check and three bounded source
reads for `AGENTS.md`, `brain_server.py` and `proxy/app.py`. Each request was allowed
once under the existing approval controls; no persistent allow rule was added.
Observed execution used no terminal commands, edits, native file fallback, web
search or other account/service tools.

The final answer is valid JSON. All **six exact source-line quotations and full
file hashes** matched the current files. The application-boundary answer was
reviewed against source. The completed answer remained visible after leaving and
reopening its chat. This verifies chat persistence, not task-registry integration.
See [normalized answer](vscode-answer.json) and [machine receipt](acceptance.json).
Original response bytes, private logs and session identifiers remain outside Git.

The probe named base `8ba5961` and correctly qualified its evidence as current
local source rather than a verified checkout of that commit. The subsequent
strict-watcher revision changed neither cited application file nor `AGENTS.md`;
their hashes still match. Final service/index checks cover the follow-up revision.
No second model run was needed for those unchanged source citations.

Reported VS Code task usage is cumulative across model turns: 224,983 input tokens,
including 204,928 cached input tokens; 2,167 output tokens, with 483 reasoning output
tokens reported. This is not unique context size, billed dollars or total project
usage. Coordinator/subagent usage, correction time, quota impact and a paired
baseline were not measured. No cost, latency or productivity advantage is claimed.

Custom-role discovery/tool enforcement, a worker SDK adapter, the optional local
provider's PR acceptance, and explicit human release review remain separate gates.
Codex role frontmatter is still not an authorization boundary. No pending lesson
was accepted and no private investigation account data was acquired.

The product-facing stack and next backend/API slice are explained in
[product stack](../../../../docs/product-stack.md): clients and a proposed Cockpit
use thin APIs over shared evidence/task logic and the selected storage profile.

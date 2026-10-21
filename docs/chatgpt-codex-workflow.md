# ChatGPT and Codex workflow

Use ChatGPT to explore architecture, compare tradeoffs and define acceptance
criteria. Use Codex in the local `rest_proxy` project to verify source, implement
changes, run meaningful tests and manage the authorized Git work.

## Start in the project

Open `/Users/michaelmarler/Projects/rest_proxy` as the Codex project. Read
[AGENTS.md](../AGENTS.md), inspect the current branch, revision and working tree,
and use the existing `.venv/bin/python` environment. Keep unrelated changes intact.

Project settings in `.codex/config.toml` use `workspace-write` with network access
for dependency/service checks and Git operations. These settings leave the global
model and Ultra reasoning choice untouched. Neither network access nor a reasoning
setting connects ChatGPT chats or authorizes external actions by itself.

For an unfamiliar service-backed indexed area, call `get_indexing_health`, then
`get_project_overview`; use `search_codebase`, symbol context and callers as needed.
For a known file, read it directly or use `describe_file` with `include_source=True`.
Check important claims against current source and include revision/line evidence
in the handoff. If the index is stale or partial, report the gap and continue with
source reads where possible; explain any proposed indexing or configuration change
before making it. See [GraphRAG tool usage](graphrag_tool_usage.md).

For embedded state, use `list_embedded_projects`, `get_embedded_overview` and
`get_embedded_indexing_attempt`, followed by `search_embedded_repository` and
`describe_embedded_file`. Set search `mode="text"` when no encoder is intended.
Common `get_indexing_health` and `search_codebase(mode="text")` also have bounded
embedded routes, with JSON provenance and explicit coverage limits. Health hashes
only the published manifest; deeper fidelity auditing remains unsupported. Indexing
still requires the explicit manifest tool. Use the
[configuration profiles](configuration.md) and embedded manifest contract.
The embedded profile needs no Neo4j/Postgres/Redis servers for these tools. Its
current real-vector operations still use a local embedding service.

## Architecture starting points

| Concern | Current source | Design rule |
| --- | --- | --- |
| HTTP proxy and provider routing | [proxy/app.py](../proxy/app.py), [proxy/config.py](../proxy/config.py), [proxy/handlers.py](../proxy/handlers.py) | Preserve request/response compatibility and optional integrations. |
| MCP transports | [brain_server.py](../brain_server.py), [mcp_server.py](../mcp_server.py) | Shared HTTP daemon and STDIO/CLI entry points. |
| Indexing and structural facts | [scripts/index_workspace.py](../scripts/index_workspace.py), [graphrag_core/indexing](../graphrag_core/indexing), [graphrag_core/ts_pack_facts.py](../graphrag_core/ts_pack_facts.py) | Fix ingest facts at their owning layer. |
| Retrieval policy | [memory/code_retrieval.py](../memory/code_retrieval.py), [memory/docs_retrieval.py](../memory/docs_retrieval.py) | Put behavior shared by frontends in retrieval, not wrappers. |
| MCP tools and presentation | [tools/brain](../tools/brain), [tools/hands](../tools/hands) | Keep orchestration, validation and bounded presentation here. |
| Durable evidence and tasks | [memory/fire_store.py](../memory/fire_store.py), [memory/task_registry.py](../memory/task_registry.py), [memory/task_dispatch.py](../memory/task_dispatch.py) | Reuse scoped evidence and task contracts when continuity is required. |

Consult [retrieval architecture](retrieval_architecture.md) for ownership and the
[current roadmap](next-session-roadmap.md) for active scope. Historical proposals
are context, not an implementation queue. The optional inference proxy does not
need a provider migration to support this workflow.

## Route the work through the existing tools

Keep one accountable implementation owner for each bounded task. For the current
audit/documentation delivery, Codex owns current-source verification, changes and
checks. ChatGPT can prepare a design brief when a decision needs discussion.

| Participant | Useful assignment | Handoff boundary |
| --- | --- | --- |
| FIRE shared MCP brain | Repository evidence, relationships and bounded current-source reads. | Tool service; connecting a client does not assign workers or synchronize chats. |
| Codex | Lead an investigation/change and verify the final evidence and diff. | One explicit brief and completion report; retain human decisions and authorized Git scope. |
| Antigravity | Alternative IDE/agent interface, or one isolated implementation/review assignment. | Connect to the same MCP service; avoid concurrent writes in the lead's checkout. |
| Claude Code | Optional second review or a separately scoped task after authentication is verified. | Return source references and concrete objections; the lead checks them. Installation alone is not task-registry integration. |
| Siri / FIRE Capture Task | Voice intake for a local read-only request. | Queues a task; inspect the transcript before dispatch. It does not invoke an agent. |
| FIRE Review Evidence | Advisory critique of a fresh, previewed task evidence bundle. | Capture against the exact task revision/submission; advice is untrusted input, not approval. Do not rerun the historical fixed bundle. |
| Linear | Delivery queue with one outcome, owner, acceptance and evidence links per issue. | No automatic synchronization with the local task registry is established. |
| Cloud workspace | Isolated Linux validation or an independent reproducible investigation. | Verify attached runtime, revision, setup and permitted network/data access before execution; return logs/artifacts to the lead. |

Antigravity's observed configuration points `graphrag-brain` at
`http://localhost:8001/mcp`. This explains a concrete purpose for its setup: make
the same repository intelligence available in that client. It does not establish
the user's original choice of IDE or an automatic FIRE supervisor. The default
lmproxy configuration is now `~/.config/lmproxy`; `.gemini/antigravity` paths in
[app_state.py](../graphrag_core/app_state.py) are compatibility fallbacks. The
[MCP supervisor](../scripts/graphrag_mcp_supervisor.py) supervises a server process,
not a team of agents. See [operations](operations.md) for shared HTTP and STDIO setup.

The [October 11 inspection](../benchmarks/reports/2026-10-11/work-routing.md)
separates installed/configured/callable integrations from unfinished adapters.
It records a callable current-source reader in Codex, existing Linear delivery
issues, installed Claude, both FIRE shortcuts and the unverified cloud attachment.
Follow-up inspection confirmed a saved private Codex Cloud environment named
`rest_proxy` for `Glassing-Wind/rest_proxy`. Its setup includes Python 3.14.7,
CI dependencies and the pinned parser. Earlier onboarding reports 801 passing tests
and 11 skips; no new cloud run or current instance readiness was verified here.
The saved setup excludes live inference/databases and optional embedded workflows.
That is a limit of this cloud setup, not a requirement for external database servers
in the embedded product path. Cloud acceptance of native embedded engines needs
their dependencies and disposable state, with an encoder only for real-vector work.

### Three different handoffs

1. **Evidence:** client -> MCP brain -> source/index result -> current-source
   verification by the lead. Use an existing healthy index for discovery and direct
   reads for known files. Do not rebuild the index solely to change clients.
2. **Worker:** task capture -> local registry -> explicit one-shot `task_run` ->
   loopback `LocalTaskProvider` -> `review_pending` -> separate review decision.
   Recorded Qwen trials demonstrate this manual handoff, with correction/timeouts;
   they do not establish an automatic Codex/Claude supervisor or a completed team.
3. **Advisory review:** `task_review_export` -> fresh preview -> Apple Cloud shortcut
   -> `task_review_capture` -> retained assessment. The historical fixed prompt is
   stale, and generic shortcut input/output wiring remains an open acceptance.
   The capture helper labels the reviewer `apple-shortcut`; do not use it unchanged
   to record a Claude review. See [review handoff](shortcuts/fire-review-handoff.md).

### Reduce coordination and repeated setup

Start from one question and a pinned revision. Give parallel reviewers different
questions or use separate worktrees for independent changes, then have the lead
integrate and verify once. Do not have Codex, Claude and Antigravity edit the same
files concurrently. Reuse the working local environment; select focused checks
for changed behavior, and broaden only for a failure or a relevant unresolved risk.
Worktree isolation follows the [official Codex execution-context guidance](https://developers.openai.com/blog/mastering-codex-remote-for-engineering).

On the selected cloud executor, first verify instance/status and repository SHA.
An environment configured elsewhere does not attach this local chat automatically,
and cloud loopback cannot reach the Mac's MCP daemon or LM Studio. Start with public
source, disposable fixtures and the repository's supported setup/checks. Keep Mac
Shortcuts and local model checks on the Mac unless an authenticated remote route
has actually been tested. See [cloud environment documentation](https://learn.chatgpt.com/docs/environments/cloud-environment)
for Codex cloud setup; the selected Cloud Environment plugin's runtime skill governs
its own attachment, credentials, proxy/CA and network-policy checks.
Cloud tasks already have isolated checkouts; use worktrees for local concurrent
changes, rather than adding nested cloud worktrees without a specific need/request.
See [capability priorities](capability-priorities.md) for when cloud, Spark and Pillow
add value and which existing FIRE capability to demonstrate next.

Before processing customer data, agree permitted paths, processing location,
external providers and retention/deletion. Export only the required bundle, exclude
credentials and unrelated records, and use disposable state for writes. Do not
expose the loopback MCP/embedded endpoints publicly as a shortcut to cloud access;
remote access needs tested authentication and project isolation. A stored hash is
evidence of snapshot consistency, not freshness, correctness or reviewer identity.

These choices aim to reduce duplicated work. Measure elapsed time, usage, setup,
review and correction overhead on equivalent questions before claiming an advantage.
The [sample audit](samples/fire-repository-audit.md) matched nine selected indexed
and native source windows; it measured no investigation speed or token savings.

Keep the current [roadmap](next-session-roadmap.md) outcome-driven: the rental
walkthrough/pilot and the separate [repository audit](fire-repository-audit-offer.md)
are concrete deliverables. Another broker, model migration or framework is not a
prerequisite unless a demonstrated delivery blocker calls for it.

## Architecture handoff to Codex

Ask ChatGPT to produce a bounded implementation brief using current source evidence:

```text
Help me reason about this rest_proxy change and prepare a Codex handoff.
Repository and inspected revision: <path/revision>
Goal and observed behavior: <problem and desired result>
Evidence: <files/lines or source-bound GraphRAG results>
Constraints: <compatibility, scope, performance and data requirements>
Decision: <chosen design, tradeoffs and open questions>
Acceptance criteria: <observable behavior and failure cases>
Validation: <reproduction and relevant checks>
Git actions authorized by me: <commit/push/PR scope, if any>
Identify missing evidence and assumptions. Do not claim tests were run.
```

Then give the brief to Codex with a direct implementation request:

```text
Implement the attached brief in /Users/michaelmarler/Projects/rest_proxy.
Verify its assumptions against current source and Git state first. Preserve
unrelated changes, make the smallest useful change, run relevant checks and review
the final diff. Complete authorized reversible work autonomously. Explain proposed
configuration, indexing or service changes before making them. Carry out the Git
actions I authorized. Return the completion report below, including any deviations
from the proposed architecture and the evidence that justified them.
```

## Tests and Git completion

Use the existing environment and select tests that protect the changed behavior.
For the local evidence/handoff surfaces, these checks use disposable fixtures and
require no inference service or live task mutation:

```sh
.venv/bin/python test_file_source_excerpt.py
.venv/bin/python test_search_codebase_tool.py
.venv/bin/python test_task_handoff.py
```

Use `.venv/bin/python -m ruff check <changed-python-paths>` for Python changes.
The live `scripts/check_mcp_protocol.py` checks transport/health and manages its own
client sessions. The broad `scripts/run_ci_checks.sh` also starts/stops the daemon
and tests restart behavior; explain those service effects before running it.
See [evaluation](evaluation.md) for wider checks.

Inspect the diff, stage only intended files and follow the user's direct Git
instructions for this task. Use normal non-force pushes, inspect hosted checks
when pushing and attach any created PR to the Codex chat. A planning brief from
ChatGPT does not expand the user's authorization.

## Completion handoff to ChatGPT

Codex returns a concise report that ChatGPT can use to assess the architecture:

```text
Goal and resulting behavior:
Repository, branch, starting revision and resulting revision:
Changed files and source evidence:
Design decisions and justified deviations:
Checks run and exact outcomes; checks not run and why:
Git state: remaining diff, commit/push/PR and hosted checks where applicable:
Acceptance criteria met, remaining limitations and next action:
```

Carry the brief/report through an explicit shared conversation reference, copied
text or a saved document. ChatGPT and Codex chats do not synchronize automatically.
The existing [shared task system](shared-task-system.md) provides durable scoped
contracts when needed; this workflow does not create a background supervisor or
introduce another orchestration framework. Keep credentials and unrelated private
data out of handoffs.

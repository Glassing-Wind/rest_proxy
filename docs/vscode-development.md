# VS Code development and agent collaboration

Validated on October 20, 2026 with VS Code 1.142.0 and the existing native arm64
Python 3.14.8 environment. Use VS Code for implementation, debugging, focused tests
and diff review; use the desktop app for planning and coordination when convenient.
Changing interfaces alone does not establish lower usage or better outcomes.

## Open and verify the project

Open the repository root, read [AGENTS.md](../AGENTS.md), and inspect the branch,
revision and existing changes before assigning work. The checked-in editor files
use `${workspaceFolder}` and contain no credentials or personal interpreter paths.
The former workspace settings selected a Python 3.11 Conda environment; a private
backup was retained outside Git before replacing those settings.

Run **Python: Select Interpreter** and select `./.venv/bin/python`. Updating
`python.defaultInterpreterPath` alone does not necessarily replace a cached
selection; the active selection was verified in this workspace's interpreter UI.
Open a new terminal if an existing terminal still has the old Conda environment.
Tasks and debug profiles explicitly use `.venv/bin/python` regardless of shell
activation. Reuse this environment; do not reinstall working dependencies.

| Entry point | What it does |
| --- | --- |
| **Tasks: Run Task**, `rest_proxy: Ruff current Python file` | Lint the active Python source using the installed Ruff CLI. |
| `rest_proxy: offline core checks` | Run source citations, retrieval and task handoff scripts sequentially in separate processes. |
| `rest_proxy: offline lessons acceptance` | Run the 16 synthetic lessons modules selected by the current CI workflow. |
| `rest_proxy: offline Cockpit acceptance` | Check the one-task REST reader and owned disposable launcher; no model calls or MCP restart. |
| `rest_proxy: check running MCP protocol` | Check the existing HTTP daemon using only the check's own sessions. |
| **Testing** view, manual refresh | Discover `test_lessons_*.py`; save-time automatic discovery is disabled. |
| **Run and Debug** | Debug the current Python file or the bounded offline scripture tests. |

Core scripts need separate processes: combining these modules in one unittest
process exposed existing mock/import interference and attempted real storage reads.
The final aggregate task preserves the documented standalone script entry points.
Lessons acceptance currently reports 230 passes and four skips because `pypdf` is
optional in this environment. The task does not install it. No full-CI task runs
automatically: full CI includes daemon start/stop checks. No service launch is bound
to folder open, build, test discovery or debug startup.

The extension recommendations are Codex (`openai.chatgpt`), Python, Pylance,
Python Debugger and Ruff. Recommendations do not install extensions. Python and
debugger extensions were already present; Ruff 2026.88.0 was confirmed in the later
October 20 registry review. CLI linting still works. See the
[extension review and C# warning diagnosis](vscode-extensions.md).
Other installed agents need separate authentication and configuration;
their presence does not establish task-registry integration.

## Two MCP clients, two configuration formats

| Client | Configuration | Verified endpoint |
| --- | --- | --- |
| Native VS Code Local/Copilot | Repository-root [`.mcp.json`](../.mcp.json), `mcpServers.graphragBrain` | `http://127.0.0.1:8001/mcp` |
| Codex extension, CLI and desktop | Existing private `~/.codex/config.toml`, `[mcp_servers.graphrag-brain]`; trusted project overrides may apply | Same endpoint; existing configuration was retained. |

Microsoft's current portable format uses `mcpServers`. The older
`.vscode/mcp.json` format uses `servers`; do not duplicate this server in both
locations. Copilot reads the portable format directly. See the
[Microsoft MCP reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
and [Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

In VS Code run **MCP: List Servers**, select **graphragBrain**, then inspect status
and output. For this HTTP entry, **Start Server** connects the client to the
already-running daemon; it does not start a second Python service. Review effective
tools in the selected harness before an agent task. Configured, discovered,
connected and exercised by an actual model are different acceptance states.
Server discovery and interpreter selection were verified in the editor; the
[acceptance receipt](../benchmarks/reports/2026-10-20/vscode-development/README.md)
records the exact connection checks and remaining UI limits.

Port 8000 is the normal inference proxy, 8001 is MCP, and the optional OpenAI
[canary example](openai.md) now uses 8002. Loopback addresses resolve on the machine
executing the client: a cloud workspace or remote container cannot reach the Mac's
daemon using its own `127.0.0.1`. See [operations](operations.md) for daemon ownership.

The daemon answered initialization, listed 60 tools and served a bounded current
source excerpt. Its index covered 484 of 607 files, with 59 stale files and 123
missing files. These are a sampled health report, not a fresh atomic filesystem
audit. Embedded, repository-learning, FIRE and context groups are conditional on
explicit profile/state flags. Their absence is expected in the current
service-backed configuration; a fresh source import also registers 60 tools.
Compare actual tool names and the runtime fingerprint to detect drift. A successful
connection alone does not establish source freshness. Use bounded current-source
reads for affected questions and report fallback calls. The initial editor setup
did not restart or reindex the service; subsequent checks are recorded separately.

## Collaborate with explicit ownership

The [Evidence](../.github/agents/rest-proxy-evidence.agent.md) and
[Review](../.github/agents/rest-proxy-review.agent.md) roles request native reading
and search plus five explicit GraphRAG tools. They require citations, freshness
limits and a fallback count. Neither role selects a model or dispatches workers.
The Local tool names are the baseline; verify effective names under Copilot.
Codex can load the role instructions but **does not enforce `tools` frontmatter**.
Its own permissions and MCP configuration still govern access. See Microsoft's
[harness-specific customization guide](https://code.visualstudio.com/docs/agents/guides/customize-copilot-guide).
The shared daemon exposes mutation/admin tools as well as evidence tools; a role
description is not an authorization boundary.

Keep one implementation owner. Give reviewers different questions and return their
findings to that owner. Concurrent writers should use separate Git worktrees and
bounded file ownership, followed by one integration review. Worktrees isolate file
changes, not credentials or every process permission. VS Code supports parallel
[delegation into worktrees](https://code.visualstudio.com/docs/agents/guides/delegate-two-tasks).
Choose the harness and inspect its permissions for each session. Do not assume
ChatGPT, Codex, Copilot and Antigravity chats synchronize automatically.

A handoff should contain the question, workspace/revision, allowed files, acceptance
checks, evidence, unresolved questions and explicit Git scope. Existing project
task/evidence/review contracts remain the product's continuity mechanism. A VS Code
session, MCP service connection or installed extension does not register a worker.

## When an SDK or API earns its place

| Option | Fit for this project |
| --- | --- |
| Codex in VS Code | Immediate coding, navigation, debugging and review beside the diff. Keep the existing sign-in and model preferences. |
| Desktop app | Planning and coordination; continue bounded implementation there when useful. |
| [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk) | First automation candidate: one local coding or review worker invoked by an existing registry task. Python and TypeScript SDKs support continuing threads. |
| [Codex app-server](https://learn.chatgpt.com/docs/app-server) | Build a richer Cockpit client requiring streamed events, approvals, authentication and history. The Codex VS Code extension already uses this protocol. |
| [Agents SDK / Responses](https://developers.openai.com/api/docs/guides/agents/sdk) | Use when our product owns orchestration, tools and result contracts. This adds operational ownership. |
| [Agents API](https://developers.openai.com/api/docs/guides/agents-api/overview) | Evaluate a managed harness/environment for durable hosted tasks when a concrete product requirement calls for it. |

Eligible ChatGPT sign-in access and API-key billing are different choices. API-key
usage follows standard API billing; do not infer a saving from removing the UI.
See [Codex authentication](https://learn.chatgpt.com/docs/auth) and
[pricing](https://learn.chatgpt.com/docs/pricing?codex-pricing-plans=individual).
No paid provider call, API-key setup or inference-provider migration was needed
for this configuration.

Before implementing a worker adapter, define one disposable task, pinned revision,
allowed operations, timeout/spend budget, exact result/citation contract, usage
availability, reopen/recovery and separate human review. Compare equivalent tasks
including correction/review overhead. Editor readiness and offline contracts do
not establish a current-model result or a reduction in latency, tokens or cost.

# VS Code development acceptance

As of October 20, 2026. Configuration work on base revision `e841eab`, with
preexisting unrelated working-tree changes preserved. This is editor/transport
acceptance, not a current-head real-model or performance result.

**Follow-up correction, October 20:** absence of embedded, repository-learning,
FIRE and context tools is expected under the selected profile/state flags. A fresh
source import also registers 60 tools. The original missing-group observation
below does not establish a catalog discrepancy. A separately observed runtime
fingerprint difference establishes source/runtime drift; follow-up refresh results
belong in a separate operational receipt.

## Delivered

- Portable workspace Python, manual lesson-test discovery, focused process tasks,
  debugger profiles and extension recommendations.
- Root `.mcp.json` pointing native VS Code at the existing shared HTTP daemon.
  Existing private Codex TOML settings retained.
- Evidence and review role files, with explicit harness enforcement limits.
- [Development guide](../../../../docs/vscode-development.md), operations link and
  correction of the optional inference canary's port from 8001 to 8002.

The old ignored editor settings were copied to a private backup outside Git before
replacement. Only four reviewed editor files are allowlisted for version control.
No credentials, global configuration contents or MCP session identifiers are
included in this report. Selected source hashes are in [acceptance.json](acceptance.json).

## Observed checks

| Check | Result |
| --- | --- |
| Runtime | Existing native arm64 Python 3.14.8; VS Code 1.142.0. |
| JSON / agent headers | Five JSON files and two YAML frontmatter headers parsed; referenced task paths resolve. |
| Editor settings / links | All nine Python setting keys exist in installed extension schemas; local Markdown links resolve. |
| Core task scripts | Source excerpts 3, search 15, handoff 2: 20 passing, separate processes. |
| Lessons task | Exact configured 16-module command: 234 cases, 230 passed, 4 optional `pypdf` skips. |
| Manual unittest discovery | 234 lesson cases discovered without discovery errors. |
| Ruff task command | Existing `test_lessons_scripture.py` passed using the installed CLI. No Python source changed. |
| Diff whitespace | `git diff --check` passed for the working tree. |
| VS Code interpreter UI | Changed cached Python 3.11 Conda selection; picker now shows `./.venv/bin/python`, Python 3.14.8. |
| Native MCP UI | `graphragBrain` discovered from `rest_proxy/.mcp.json`; after client connect, status **Running**. |
| Independent Streamable HTTP probe | Initialize 200, notification 202, tools list 200, 60 tools, protocol `2025-11-25`. |
| Bounded tool read | `describe_file(AGENTS.md, include_source=True, start_line=1, max_lines=8)` returned source and hash without a tool/JSON-RPC error. |
| Probe cleanup | DELETE closed only that probe's session, HTTP 200. |
| Independent change review | No actionable defects identified; model/role execution limits retained. |

The first combined core unittest process failed with six failures and four errors
because existing per-module mocks interfered after another module imported
`memory.store`; it attempted real storage reads. Standalone scripts passed. The
final aggregate task runs them sequentially in separate processes, matching the
repository's documented entry points. No production or test implementation was
changed to hide the failure.

## Service limits and remaining acceptance

The sampled index reported 607 disk files, 484 structural/semantic files, 59 stale
files and 123 missing files; structural and semantic publication runs aligned.
Health and overview were separate read calls, not an atomic filesystem audit.
The live catalog exposes 60 tools, including mutation/admin operations, and lacks
embedded, repository-learning, FIRE and context groups conditionally registered in
current source. As corrected above, that absence reflects configuration gates,
not proof of a server/source discrepancy or current application cutover.

The editor's **Start Server** action connected its HTTP client to the existing
daemon. No daemon restart, reindex, environment rebuild, provider call, sampling
grant, global preference change or paid API setup was performed. Other installed
extensions may activate when a workspace opens; this report does not certify all
installed extensions or disable their normal startup behavior.

Custom-role picker discovery, effective role tool restrictions in each harness,
debugger UI execution and a model-driven tool call were not demonstrated. A final
Codex sidebar readiness inspection was blocked when the Mac locked, after the
interpreter and native MCP checks above had completed. Codex
can load role instructions but does not enforce their `tools` frontmatter. Native
client discovery/connection and the independent bounded tool probe are distinct
checks. No latency, usage, cost or outcome advantage was measured.

Next operational acceptance should refresh the shared daemon and selected index
under their normal ownership procedure, then exercise one disposable current-model
task with exact runtime/model identity, citations and usage/reopen/review evidence.
That does not require an inference-provider migration or new agent framework.

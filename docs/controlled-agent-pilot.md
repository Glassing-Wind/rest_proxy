# Controlled fresh-agent investigation pilot

This runner compares native tools with GraphRAG MCP plus native fallback on identical questions. It is an investigation pilot, not evidence of coding productivity or enterprise readiness on its own.

The runner uses a new `codex --no-daemon exec --ephemeral --ignore-user-config --sandbox read-only --json` process per answer, the same explicit model and effort, and alternates native/MCP order across shuffled cases. Existing saved CLI authentication is reused without copying credentials or modifying user config. It detects the current or older desktop CLI layout, then falls back to PATH. Each MCP answer must first make an observed successful health call; configuration and parent-side connectivity alone do not prove agent tool availability.

Official documentation: [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) documents ephemeral sessions, ignoring user config, required MCP startup, JSON usage events, and saved authentication.

## Run

Dry run (no inference or health calls):

```sh
python scripts/run_controlled_agent_pilot.py --output /tmp/graphrag-pilot-unique
```

After pausing watchers, completing edits, stabilizing indexes, and verifying account usage allowance, add `--execute`. Defaults are four repository questions, `gpt-6-astra`, effort `low`, and the local HTTP MCP server. Override `--case-id` repeatedly for a bounded 3–5-case pilot. Use a new external output directory; artifacts must not enter the repository being investigated.

Every execution first runs a separate fresh-agent MCP availability probe and aborts before benchmark questions if it fails. Use `--execute --preflight-only` for just that check. Its time and usage are preserved separately from paired measurements. Prefer a persistent external directory rather than `/tmp`, which can disappear after a restart.

On CLI 0.159.2, a verified successful probe required catalog readiness, not merely a connected server: the runner sets `mcp_servers.graphrag.startup_readiness="catalog"` and `mcp_optional_startup_grace_ms=0`. These settings were verified against the installed CLI; strict configuration parsing rejects unsupported versions. Each allowlisted read-only tool has `approval_mode="approve"` so unattended calls do not fail under `approval_policy="never"`. No write/admin tools are allowed. See the [official MCP configuration documentation](https://learn.chatgpt.com/docs/extend/mcp) for per-tool approval configuration. The catalog-readiness setting was verified locally and is not currently described there.

No expected-evidence hints enter prompts. A source manifest hashes tracked and nonignored untracked files (including dirty changes), excluding `.runtime` and prior benchmark reports. Every run hashes the entire raw index health response before and after. Any observed drift aborts the batch. Fresh/warm runs require an initially healthy and aligned index. The other state labels describe externally prepared conditions; the runner does not create isolated degraded indexes.

Elapsed time includes CLI launch, MCP initialization, model inference, tool work, and shutdown equally for both conditions. Health probes and hashing are outside the timed interval. Token counts come from completed-turn JSON usage; unavailable usage stays null and invalidates a run. Cached input tokens are preserved separately; do not subtract or add them to input without understanding billing semantics. No monetary cost is inferred from account allowance or token counts.

Each run preserves its prompt, final answer, JSON events, stderr, identity snapshots, and metrics. Failed/timed-out runs are retained and abort the pilot. Correctness remains null. A shuffled `blind/` packet contains only questions and answers; `grading-key.json` is separate. A fresh independent grader must verify claims and citations against the identical source snapshot before correctness comparisons. Answer prose can reveal tool provenance, so blinding is partial.

## Limits to report

Read-only sandboxing protects files; MCP allowlisting prevents declared write tools. Native shell access to network/services is prohibited by the prompt, not a complete isolation boundary. Event checks reject MCP use in native runs, unexpected MCP tools, file changes, and web searches. Inspect command transcripts for indirect service access and benchmark-artifact leakage before admitting results. System skills, AGENTS.md, managed configuration, and execpolicy rules may still be inherited. Ancestor/project `.codex/config.toml` causes an abort pending review.

A single small repository pilot has order/cache effects, shared infrastructure, model nondeterminism, and limited generalizability. Counterbalancing reduces but does not eliminate those effects. Follow up with repeated cases, a genuinely unfamiliar repository, degraded-index conditions, and real code-change tasks. Do not claim statistically established gains from this pilot.

The September 30 attempt found MCP tools unavailable inside fresh CLI sessions despite required server configuration. Its answers passed independent review, but its comparison is excluded. See [the evidence report](../benchmarks/reports/2026-09-30/README.md). Resolve availability before spending allowance on another full run. The ignored user config is exempt from ancestor-config rejection; other project/ancestor configs still cause an abort.


October 3 follow-up: CLI 0.160.0 passed an observed MCP health call when the probe
explicitly allowed tool search/discovery. A more restrictive prompt
reported the tool unavailable in a separate session despite parent-side catalog
availability. The underlying cause is unverified; no discovery call was recorded.
Both the preflight and MCP investigation prompts now allow discovery before health;
success still requires the observed health call. Retain CLI version for preflight
attempts as well as completed batches. Model tool choice remains variable; never
infer exposure from configuration alone.

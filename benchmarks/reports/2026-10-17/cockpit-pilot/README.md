# Cockpit compatibility and extraction pilot

October 17, 2026. **Keep helpers experimental.** This pilot implemented and tested
the evaluation path, but did not establish a reliable automatic helper or token
savings. It follows the [review and frozen plan](../cockpit-model-review/README.md).
The [receipt](receipt.json) and per-condition summaries retain every attempted
case. Exact request/response bodies remain in an owner-only folder outside Git;
the summaries include their hashes and sanitized provider usage.

## Live results

Each declared condition used the same six original synthetic sources, exact typed
fact/citation criteria, temperature zero, and a 2,048-token output limit. Sources,
truth values and acceptance thresholds were not changed after viewing results.
The initial schema used a multi-type `type` array. The separately declared follow-up
used equivalent `anyOf` alternatives, and the native API condition used prompt-only
JSON. These are compatibility probes, not a matched model ranking.

| Condition | Accepted | Observed result |
| --- | ---: | --- |
| Phi-4-reasoning-plus, MLX 4-bit, original constrained schema | 3/6 | All six returned schema-valid JSON. Conflicts/reference cases added unsupported fields; qualifications were sometimes converted to booleans. |
| Qwen3.6-35B-A3B Splash 4-bit, original constrained schema/default thinking | 0/6 | All six reached the output limit. Provider reported 12,262 reasoning tokens out of 12,288 generated tokens; no complete accepted final result. |
| Phi-4-mini-instruct, community MLX 4-bit, original constrained schema | Unassessed | Six HTTP 400 responses: runtime rejected the schema's multi-type `type` field before returning findings. |
| Phi-mini, `anyOf` follow-up | Unassessed | First request exceeded 90 seconds; the owned instance was unloaded. Remaining errors include cancellation/unloaded-instance failures and are not answer-quality measurements. |
| Qwen, native LM Studio API with reasoning off, prompt-only JSON | 0/6 | Zero reasoning tokens reported. JSON included schema definitions as output or lacked required findings. Strict shape checks refused all six. |
| Native Gemini 3.8 Flash Low | Not run | CLI startup still advertised 60 tools despite an empty-tool custom agent; controller stopped before sending the synthetic payload. |

There were **30 local HTTP attempts**, including compatibility errors and the
interrupted follow-up, and **zero Gemini generation payloads**. No retries were
hidden inside a condition. No paid API key or generic OAuth proxy was added.
The native route's cached generation authentication and quota remain unverified.

Phi-plus reported 3,850 input and 983 output tokens for its six calls. Native
Qwen reported 2,641 input and 3,773 output tokens. These are different tokenizers
and different decoding conditions; they do not establish a cost advantage. Lead
usage, human correction time, electricity, billed dollars and subscription quota
were not measured. The direct lead was not a blinded baseline because it had
already seen the evaluator's truth values.

## Changes delivered

The standalone runner builds bounded source-only requests, verifies typed facts
and exact original-line quotations, records errors/usage, and packages explicit
native handoffs. The native LM Studio diagnostic uses its documented
[reasoning control](https://lmstudio.ai/docs/developer/rest/chat), empty integrations
and `store: false`. It does not add a production provider or alter task dispatch.

The tests found and fixed a failed-row summary crash. Strict JSON now also refuses
overflowing numeric literals and isolated Unicode surrogates. After the live MLX
stall, the runner was changed to stop a batch on timeout and list unattempted cases:
a client timeout does not prove server-side cancellation. The retained live run
predates that safeguard. See [usage and limitations](../../../../docs/cockpit-evaluation.md).

The repaired user `agy` wrapper launches the installed IDE. The distinct native
Antigravity CLI 1.4.0 was installed privately, with vendor archive SHA-512 and Google
LLC Apple signature verified. PATH, profiles, global permissions, MCP configuration
and application presets were not changed. A workspace agent requested no tools or
inherited customizations, but startup reported otherwise. The
[headless contract](https://www.antigravity.google/docs/cli/headless/) defines that
list as available tools, so the receipt treats it as an unresolved runtime/reporting
boundary. See [native preflight](native-preflight.json) and
[installation receipt](native-install.json).

Phi-mini was downloaded using the Hugging Face CLI skill from the public
[community conversion](https://huggingface.co/mlx-community/Phi-4-mini-instruct-4bit/tree/ac1c269cb4222a4e136a3d09edad301056c1f36a),
pinned to revision `ac1c269cb4222a4e136a3d09edad301056c1f36a`.
The 2,158,100,796-byte weight file matched SHA-256
`9dcfcdc0a579494283f2d2317ad686123782ce461d1111690ede7c904ff3f87b`.
Only weights and data/config/tokenizer files were downloaded; repository Python
files were excluded. The artifact remains downloaded for later compatibility work.

## Runtime and limits

The Mac uses Apple M3 Max/128 GB unified memory, native arm64 Python and LM Studio
with Metal-capable MLX/Splash runtimes. Temporary Qwen and Phi-mini instances used
8,192-token contexts and parallelism one; the preexisting Phi-plus instance used
its existing 32,768-token context/parallelism four. This verifies native ARM serving
paths, not GPU utilization or peak performance. The two temporary instances were
unloaded. The original Phi-plus and Jina embedding model identifiers, paths,
quantizations and contexts match the [baseline after cleanup](runtime-state.json).
No reindexing occurred; the pending lesson ledger's hash still matches the prior
snapshot, and no lesson was approved or released.

Latency is descriptive only: runs were not randomized, loading/warmth differed,
and native Qwen requests overlapped the stalled MLX follow-up. The Unicode case
tests exact text and unit labeling, not numerical offset calculations. These tiny
fixtures do not assess repository coding, long-context reasoning, investigation
outcomes, adversarial robustness generally, or factual knowledge.

Next, resolve the serving/schema and native tool-surface mismatches, then freeze
one common request format and run a new held-out paired evaluation before enabling
routing. Prompt tuning on these six known examples alone is not a promotion gate.

Validation: **40 offline tests pass**, changed-file Ruff passes, and the new Python
files compile. Full daemon-changing CI was not run for this isolated runner.

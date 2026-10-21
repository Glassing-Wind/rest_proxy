# Cockpit model routing review

Reviewed October 17, 2026. This is a source-backed recommendation and read-only
inventory, not a model benchmark or an activated routing policy. The
[inspection receipt](../benchmarks/reports/2026-10-17/cockpit-model-review/README.md)
records local observations and scope. No model was downloaded, loaded or queried,
and no prompt was sent to Antigravity during this review.

Follow-up: the [live cockpit pilot](../benchmarks/reports/2026-10-17/cockpit-pilot/README.md)
implemented the [evaluation runner](cockpit-evaluation.md), repaired the IDE launcher,
and tested local serving paths. Compatibility and quality failures kept automatic
routing disabled; native Gemini stopped at tool-surface preflight before generation.
The inventory below remains the original review snapshot.

## Recommendation

Keep one accountable lead. Let deterministic tools retrieve and validate evidence,
small local models handle bounded routine language tasks, and Gemini or a stronger
reviewer handle selected difficult work. A model should earn a role by reducing
the total cost of an accepted result, including verification and corrections.

| Work | First candidate | Escalation |
| --- | --- | --- |
| Exact IDs, AST relationships, filtering, citation/span checks | Existing code and GraphRAG; no generation needed | Investigate missing coverage before adding a model. |
| Intent labels, query suggestions, literal extraction, short grounded drafts | Phi-4-mini-instruct; compare with Ministral 3 8B or Gemma 4 E4B | Existing Qwen or a selected cloud worker when validation fails. |
| Repository coding and multi-file explanations | Current loaded Qwen as baseline; downloaded Qwen3-Coder-Next as coding challenger | Gemini through a documented native workflow, followed by lead verification. |
| Independent public-code review and documentation critique | Existing Gemini in Antigravity, with a compact scoped brief | Lead resolves disagreements against source and tests. |
| Selected source/lesson research | Explicit Free Man read-only corpus interface and reviewed tasks | Preserve dissent and pending human decisions. No automatic operational lesson transfer. |

The existing [helper direction](helper-agent-direction.md) already distinguishes
routine local work from supervision. The [cockpit direction](clarity-cockpit-direction.md)
is still a product direction; opening multiple model apps does not implement an
automatic background supervisor or shared chat history.

## What is available on this Mac

The inspected MacBook Pro has an Apple M3 Max and 128 GB unified memory. Its native
arm64 LM Studio installation has Metal-capable MLX, llama.cpp and Splash runtimes.
This is a viable Apple Silicon deployment path; runtime selection metadata is not
a measurement of GPU utilization, generation speed or memory pressure.

| Local discovery | Artifact metadata | Observed state |
| --- | --- | --- |
| `microsoft/phi-4-reasoning-plus` | MLX, 4-bit, 8.26 GB file size; advertised 32,768-token context | Downloaded, not loaded |
| `qwen3.6-35b-a3b-splash` | Splash, 4-bit, 20.95 GB file size; loaded context capacity 262,144 | Loaded |
| Qwen3-Coder-Next | MLX, 4-bit, 44.86 GB file size | Downloaded, not loaded |
| Jina embeddings v2 base code | GGUF F16, approximately 323 MB; loaded context capacity 8,192 | Loaded |

File sizes are not resident memory use. Total weights, quantization metadata,
context/KV cache and runtime buffers matter. MoE active-parameter counts do not
eliminate the need to keep the full model's weights available. Preserve the accepted
embedding identity while testing generative models; changing embeddings would be
a separate indexing decision.

Antigravity Desktop is open on `rest_proxy`, showing **Gemini 3.8 Flash High** and
the **Turbo** permission preset. Its model menu also shows an unavailable Gemini 4
Argon entry and an account quota eligibility notice. Those observations are not
an API model ID, remaining-token allowance or paid-plan determination. No setting
was changed. Its configured GraphRAG endpoint is `http://localhost:8001/mcp`;
Antigravity-to-MCP execution was not tested by sending a prompt.

There are two distinct installed applications: Antigravity Desktop 2.24.0 and
Antigravity IDE 2.5.5. The user `agy` wrapper fails because it targets an obsolete
binary path inside the Desktop app. A working IDE CLI exists at
`/Applications/Antigravity IDE.app/Contents/Resources/app/bin/antigravity-ide`.
Its `chat --help` launches UI conversations with ask/edit/agent modes. This is not
proof that the installed tooling implements the current documented headless
`agy -p` response/usage protocol. Repairing the old launcher and installing or
verifying the distinct native headless CLI are separate decisions.

## Gemini and Antigravity integration choices

| Route | Suitable use | Boundary |
| --- | --- | --- |
| Existing App/IDE | A user-directed bounded review using shared MCP evidence | Works as a separate conversation; no automatic task-registry adapter or synchronized history is established. |
| Documented Antigravity CLI | Native headless tasks with JSON/schema output and usage metadata | Verify installed binary, authentication, model availability and scoped tool policy first. Inspect terminal status and artifacts, not exit code alone. |
| Gemini Developer API | Explicit metered cloud generation adapter | Separate authentication/billing/data policy; never infer API credits from an IDE subscription. |
| Antigravity Python SDK | Its agent harness, MCP, policies and lifecycle when those features are needed | Extra orchestration is optional; not a prerequisite for a simple one-shot model worker. |
| SDK with a local OpenAI-compatible server | Optional local harness over existing LM Studio | Documented SDK support does not imply arbitrary local-model support in the installed IDE model menu. |

The [official headless CLI](https://www.antigravity.google/docs/cli/headless/)
documents JSON and streaming output, structured results and usage fields. It also
documents soft-denied tools and workspace write behavior: an automated pilot needs
an explicit restricted policy and disposable checkout, rather than inheriting the
currently visible Turbo preset. These are documented capabilities, not accepted
local CLI integration results.

Google's [local SDK documentation](https://www.antigravity.google/docs/sdk/local-models/)
supports `LocalOpenAIAgentConfig` for LM Studio/Ollama/vLLM and LiteRT on Apple
Silicon Metal. Start with the existing local task adapter unless the new harness
closes a demonstrated gap. The [SDK quickstart](https://www.antigravity.google/docs/sdk/overview/)
uses explicit Gemini API credentials or Google Cloud configuration; do not assume
those calls consume the open app's included usage.

The current [local task provider](../memory/task_provider.py) deliberately accepts
only numeric-loopback HTTP, an 8 KiB prompt, at most 4,096 output tokens, a
20-second request timeout and an exact returned model identity. Tool calls and
truncated responses are refused. A Gemini integration needs its own explicit
authentication/data/budget contract, rather than widening that local boundary.

Use documented native authentication rather than extracting Antigravity OAuth
credentials into a generic cockpit provider. Google's
[terms](https://www.antigravity.google/terms), section 6, specifically restrict
third-party service access using Antigravity OAuth. A supported native workflow
does not authorize a subscription inference proxy or resale arrangement.

## Microsoft Phi review

These are open-weight Microsoft models under MIT licenses. That is a more precise
description than assuming all training data and the full training pipeline are
open and reproducible. Context figures below are advertised model capacities,
not a recommendation to submit that many tokens on every task.

| Official model | Parameters / context | Cockpit assessment |
| --- | --- | --- |
| [Phi-4-mini-instruct](https://huggingface.co/microsoft/Phi-4-mini-instruct) | 3.8B / 128K | First Phi candidate for bounded extraction, routing and short cited drafts. Its card includes a function-call format and warns of limited factual capacity. RAG helps supply evidence; exact output still needs validation. |
| [Phi-4](https://huggingface.co/microsoft/phi-4) | 14B / 16K | Broader local text-analysis comparator. Do not assume its generic tool template behaves like mini-instruct's format. |
| [Phi-4-reasoning](https://huggingface.co/microsoft/Phi-4-reasoning) | 14B / 32K | Reasoning specialist; intended-use text emphasizes math. Citation-sensitive investigation needs its own evaluation. |
| [Phi-4-reasoning-plus](https://huggingface.co/microsoft/Phi-4-reasoning-plus) | 14B / 32K | Already downloaded, useful as an experimental comparator. Microsoft reports approximately 50% more generated tokens than reasoning and higher latency; a poor default assumption for cheap literal extraction. |
| [Phi-4-mini-reasoning](https://huggingface.co/microsoft/Phi-4-mini-reasoning) | 3.8B / 128K | Math-focused specialist, lower priority for routine cockpit work. |
| [Phi-4-mini-flash-reasoning](https://huggingface.co/microsoft/Phi-4-mini-flash-reasoning) | 3.8B / 64K | Different SambaY hybrid architecture. Published vLLM throughput claims do not establish performance or support in this Mac runtime. |
| [Phi-4-multimodal-instruct](https://huggingface.co/microsoft/Phi-4-multimodal-instruct) | 5.6B / 128K | Later text/image/audio intake candidate; verify each modality and serving template separately. |
| [Phi-4-reasoning-vision-15B](https://huggingface.co/microsoft/Phi-4-reasoning-vision-15B) | 15B / 16,384 | March 2026 vision/reasoning release; possible document/chart worker. No Mac acceptance result here. |
| [Phi-Ground](https://huggingface.co/microsoft/Phi-Ground) | 4B class | Specialized GUI grounding, not a general supervisor or a grant of action permissions. |

Prefer the individual model cards for architecture details: the current cookbook's
family table contains a conflicting Phi-4-reasoning parameter count. No downloaded
mini-instruct artifact or validated quantization was found in the local inventory.

Apple's [MLX-LM](https://github.com/ml-explore/mlx-lm) has a Phi architecture path;
[llama.cpp](https://github.com/ggml-org/llama.cpp) supports Apple Metal/ARM and Phi
conversion/runtime paths. Architecture support is not acceptance of every vision,
audio or hybrid checkpoint in the installed runtime. Pin the actual artifact,
quantization, chat template, engine and context when evaluating it.

## Other open-weight candidates

| Candidate | Role worth testing | Important limit |
| --- | --- | --- |
| [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) | Keep the loaded model as the baseline. 35B total / 3B active; Apache-2.0. | Installed Splash artifact is a separate serving/quantization variant; official benchmark results are not its local acceptance. |
| [Qwen3-Coder-Next](https://huggingface.co/Qwen/Qwen3-Coder-Next) | First already-downloaded coding specialist. 80B total / 3B active, non-thinking, Apache-2.0. | Its approximately 44.86 GB local artifact is larger on disk; resident memory remains unmeasured. No measured coding advantage here yet. |
| [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) | New generalist challenger: dense 27B, vision, thinking controls, Apache-2.0. | Newer is not automatically cheaper. Thinking defaults and preservation, retries, dense compute and quantization change task cost. |
| [Ministral 3 8B Instruct](https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512) | Independent-family competitor for extraction and short tool-oriented tasks; Apache-2.0. | Marketed 8B includes an 8.4B language model + 0.4B vision encoder. An official GGUF is available; validate constrained JSON and actual citations. |
| [Gemma 4 E4B / 12B / 26B A4B](https://ai.google.dev/gemma/docs/core) | Small helper, multimodal worker and larger generalist comparisons, respectively. | Effective/active parameters are not total weight memory. Google's memory table excludes context/KV cache. Use checkpoint-specific licensing and runtime support. |
| [Mistral Small 4](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603) | Later large local generalist comparator, Apache-2.0. | Despite its name, 119B total / 6.5B active. Four-bit weight arithmetic alone is about 59.5 GB, before metadata/cache/runtime; not the first always-loaded helper. |

The [Gemma 4 12B card](https://huggingface.co/google/gemma-4-12B-it) uses Apache-2.0.
Google supplies [QAT/GGUF deployment paths](https://ai.google.dev/gemma/docs/core)
for LM Studio/llama.cpp. Avoid treating older Gemma licensing assumptions or mobile
effective-parameter labels as the current checkpoint's terms or actual footprint.

## Cost accounting and next experiment

Delegating can reduce this assistant's usage while consuming another service's
quota, electricity and review time. [OpenAI Docs](https://learn.chatgpt.com/docs/pricing)
distinguish subscription/credit usage from API token pricing; do not use API prices
to estimate included Codex tasks. Antigravity has its own
[quotas and credit overages](https://www.antigravity.google/docs/plans). The actual
account's quota/overage setting was not changed or fully audited here.

For comparison, Google's current standard paid API text rates per million tokens:

| Model | Input USD | Output USD, including thinking |
| --- | ---: | ---: |
| Gemini 3.8 Flash | 0.75 | 3.75 |
| Gemini 3.5 Flash-Lite | 0.30 | 2.50 |
| Gemini 3.1 Flash-Lite | 0.25 | 1.50 |

These are [API prices](https://ai.google.dev/gemini-api/docs/pricing), not app-plan
quotas. The 3.8 Flash rates are introductory through December 31, 2026 and double
January 1, 2027. Supported [batch processing and caching](https://ai.google.dev/gemini-api/docs/optimization)
can help repeatable workloads, with cache storage and tool charges accounted for.

Keep private financial/case material out of an unreviewed cloud experiment.
[Gemini API terms](https://ai.google.dev/gemini-api/terms) distinguish unpaid
processing from standard paid data treatment; optional contribution tiers can
differ too. Local processing does not authorize sending its summaries to a cloud
reviewer. Free Man's two pending lessons remain pending and unreleased.

Start with the six synthetic cases in the receipt's evaluation plan. Compare direct
lead handling, current Qwen, installed Phi, a small instruct challenger and native
Gemini handoff under the same evidence/output contract. Score correctness, exact
citations, abstention, schema failures, truncation, latency, all prompt/output/
thinking/cache usage, retries and human correction time. Mark unavailable usage
as unavailable; different tokenizers and subscriptions are not one common meter.

Use compact task/evidence bundles instead of copying this entire conversation to
every worker. Cache stable allowed prefixes when supported, constrain output size,
avoid redundant agent reviews and escalate on observable failure criteria. Do not
turn down reasoning blindly: reduced effort can increase retries and total cost.

The next implementation should validate the documented native CLI and one bounded
task handoff through existing task/evidence/review contracts, then run the paired
local-model trial. It does not require a database rewrite, a new always-running
framework, replacing embeddings or changing the inference proxy's provider.

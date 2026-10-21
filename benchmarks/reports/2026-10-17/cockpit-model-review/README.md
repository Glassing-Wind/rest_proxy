# Cockpit, Gemini and open-weight model review

October 17, 2026. The user requested ways to use the already-open Gemini/
Antigravity setup to reduce usage and a review of open models, especially Phi.
The [full recommendation](../../../../docs/cockpit-model-routing.md) distinguishes
observed local configuration, current official documentation and unmeasured
deployment hypotheses.

## Findings

1. **Reuse what is present.** The Mac has an M3 Max and 128 GB unified memory.
   LM Studio already has Phi-4-reasoning-plus and Qwen3-Coder-Next downloaded.
   Qwen3.6-35B-A3B Splash and Jina code embeddings were loaded at inspection time.
   No model lifecycle or generation request was performed.
2. **Use a small instruct model for simple work.** Phi-4-mini-instruct is the first
   Phi candidate for bounded extraction/query/drafting tasks. Reasoning-plus is
   already available, but its long-output design does not make it a default
   token-saving helper. Compare against current Qwen and an independent family.
3. **Separate Antigravity surfaces.** The open Desktop app shows Gemini 3.8 Flash
   High. Its configured GraphRAG endpoint gives it a possible shared evidence
   route, not automatic cockpit dispatch. The user `agy` wrapper points to an
   obsolete Desktop binary and fails. The installed IDE CLI works as a UI chat
   launcher; current documented native headless support remains locally unverified.
4. **Prefer a supported native interface.** Google's current CLI documents JSON,
   schemas and usage output; its SDK documents local OpenAI-compatible servers.
   Neither an open app nor cached OAuth grants a generic cockpit Gemini endpoint.
   Subscription quota and API billing remain separate.
5. **Measure accepted results.** No new model speed, quality, token saving or
   total-cost improvement is established. Include verification, reasoning tokens,
   cache charges, failures, rework and human correction in the comparison.

The model review covers nine Phi variants and selected Qwen, Gemma and Mistral
alternatives. Microsoft individual cards take precedence over a conflicting
cookbook parameter-count table. Advertised model context and downloaded byte size
are distinct from configured context, resident memory and observed throughput.
The review calls MIT/Apache-licensed weights open-weight without assuming that all
training data and the complete training pipeline are reproducibly open.

## Evidence and scope

[inventory.json](inventory.json) contains sanitized observations from hardware
metadata, Antigravity accessibility state, selected application/CLI metadata and
LM Studio's bounded local model-catalog GET. It includes installed versions,
selected runtime records and the broken-launcher finding. It contains no account
credentials, private financial/case data, conversation bodies or prompt results.
The source endpoint configured in Antigravity was read; no Antigravity prompt was
sent to prove a current tool call. Actual account quota/overage settings were not
fully inspected or changed.

Current source inspection found an explicit loopback-only structured worker in
`memory/task_provider.py`, exact evidence checks in `memory/task_worker.py` and
the earlier bounded-helper direction. A direct Gemini task adapter or automatic
cross-client coordinator was not found in the inspected source. This was a bounded
inspection, not a claim that every file or external installation was audited.

Primary Google, Microsoft, Qwen, Mistral, Apple/llama.cpp and official OpenAI Docs
references are linked beside their claims in the full recommendation. Current
public documentation was opened during the review; no whole vendor pages or model
weights are copied into the repository. Documentation support is distinct from
acceptance of the exact installed model/template/runtime.

## Next bounded experiment

[evaluation-plan.json](evaluation-plan.json) contains six synthetic cases: literal
fields, missing facts, conflicting revisions, embedded instructions, Unicode
citations and an unreviewed reference counterpart. Inputs have deterministic
UTF-8 hashes and explicit expected behavior. This is a **planned comparison**, not
an executed benchmark or a runnable existing harness configuration.

Start with the current loaded Qwen baseline and existing Phi artifact after an
explicit lifecycle choice. Add Phi-mini-instruct or another small instruct model
only after selecting its exact quantization/template. Establish a supported native
Gemini CLI and restricted disposable task policy before automating cloud handoff.
The current Turbo preset was observed, not changed or accepted as the pilot policy.

Use the same task and source/output contract for direct lead handling and delegated
handling. Keep unavailable usage unavailable; provider token counts, subscription
quota units and human review time are different measurements. The first run should
retain one attempt and any failure before changing settings or retrying.

No configuration repair, installation, paid generation, model download, service/
index restart, cloud data submission or training occurred. The two Free Man lesson
proposals remain pending and unreleased. This documentation-only change needed no
implementation test run. JSON syntax, fixture hashes, local links and Git whitespace
were checked; [fingerprints.json](fingerprints.json) records artifact consistency.

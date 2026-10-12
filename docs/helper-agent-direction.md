# Helper delegation direction — October 7, 2026

The user clarified that Qwen is intended for simple helper work to potentially reduce
paid-model usage. It was not intended as supervisor. Previous Qwen reviewer trials
remain historical observations; they do not establish suitability for extraction,
classification or drafting. Stop treating reviewer calibration as the model-selection
gate for the helper role.

## Roles and boundaries

| Role | Implementation today | Intended responsibility |
| --- | --- | --- |
| Owner | User | Goals and consequential approvals |
| Supervisor | Codex in this chat; automatic adapter unfinished | Choose bounded work, verify outputs, integrate results |
| Helper | Explicit local Qwen one-shot provider | Extract fields, suggest terms, draft compact summaries |
| Executor | Deterministic code/tools | Source access, schema checks, tests and permissions |
| Interface | Siri/Shortcuts intake | Requests and presentation; status UI unfinished |
| Memory | FIRE task/checkpoint/evidence stores | Retain originals, provenance, correction and recovery |

A role is a task contract, not a requirement to run a separate persistent model.
No automatic delegation, model switch or paid API invocation is introduced by this
plan. Framework adapters must use existing revision/claim/evidence boundaries.
Start one supervisor plus one helper. Framework evaluation remains optional;
PydanticAI is a candidate for typed delegation, LangGraph for branching workflows.
Neither is installed by this milestone. Hooks may assist continuity after trust review;
they must not silently become worker launchers or approval authorities.

## First bounded helper trial

Use a synthetic numbered document with five explicit fields: project name, maximum
raw-byte count, maximum decoded-character count, requested action and whether an
executor is shown. Helper returns a compact JSON list of field/value/source-line/
exact-quote records and unresolved fields. Exclude interpretation of task type,
permissions or behavior absent from the document. No tools, side effects or review
approval. Output schema compliance is distinct from factual correctness.

Deterministic checks validate field allowlist, unique keys, byte size, line ranges
and exact quoted substring in original evidence. Supervisor checks whether each
value follows from its quote and whether missing facts remain unresolved. A matching
quote alone does not establish the inferred value. Preserve failed outputs privately
or sanitized evidence as appropriate; do not store secrets in benchmark artifacts.

Compare direct supervisor extraction with helper extraction plus supervisor checking
on identical inputs, acceptance criteria and source access. Include verification,
corrections, retries, prompt overhead and native fallbacks. Record provider-reported
usage when available; do not substitute estimates for measured paid token usage.
Report unmeasured costs explicitly. No savings claim until complete paired results.
Use multiple fixtures including missing fields and misleading embedded instructions.

Repeated 20-second local provider timeouts remain a known implementation constraint.
Do not consume the live Siri task's remaining attempts to test unrelated extraction.
Use isolated fixtures. Resolve timeout/budget handling separately without changing
service/model lifecycle. Gateway journal durability work remains a separate milestone.

# Bounded Qwen worker trial and supervisor review

Executed October 5, 2026 on project Python 3.14.4, from base commit `1228a9b`.
Task: investigate missing bearer authentication in the LM Studio embedding client,
return cited findings and propose a patch. Codex reviewed the proposal, implemented
accepted changes and ran validation. This is one guided known-file functional trial,
not a controlled comparison or evidence that Qwen/cloud supervision saves tokens.

## What completed

- Real HTTP generation and multi-turn function calls with the locally loaded
  `qwen3.6-35b-a3b-splash` model through LM Studio port 1234.
- Bounded worker source reads with path allowlisting, line/character/read/turn limits
  and an overall deadline; no worker shell or write access.
- A cited investigation identifying the shared `httpx.AsyncClient` authentication gap.
- A patch proposal reviewed against original source, with defects recorded below.
- Supervisor implementation of optional `LMSTUDIO_API_KEY` for model discovery,
  load/unload and embeddings. Unset/blank values omit the header; `OPENAI_API_KEY`
  is not reused. Credentials are fixed per provider instance, outside config and
  encoder identity. Exact token occurrences in returned HTTP error text are redacted.
- Offline tests of actual HTTP request headers and encoder identity; runner guard
  checks refuse unloaded models, outside-allowlist source and unsupported tools.

The trial used **direct LM Studio HTTP**, not the rest_proxy inference route or MCP.
Its six successful source reads in the accepted attempt are native fallbacks.
No repository index, embedding model/configuration or shared authentication setting
was changed. The Jina embedding model's reported loaded-instance record matched
before and after. This does not attest resident weights or runtime bytes.

## Attempts and observed usage

| Attempt | Outcome | Turns / native reads | Wall time | Reported input / output tokens | Reasoning tokens |
| --- | --- | --- | --- | --- | --- |
| 1 | Incomplete: final output hit `length`. Rejected. | 5 / 4 | 35.107 s | 18,106 / 3,810 | 3,319 |
| 2 | Complete proposal, accepted with supervisor corrections. | 7 / 6 | 29.380 s | 33,463 / 2,411 | 0 |

Usage is summed across requests, including repeated prompt history. Attempt 2
requested `reasoning_effort=none`, increased the turn/output ceilings and forced
`tool_choice=none` for its final turn. Several variables changed, so these rows
are not an isolated reasoning-setting experiment or a performance comparison.
Local tokens still consume compute. Supervisor usage is unavailable; no cloud-token
savings percentage is supported. The worker itself made no cloud inference calls.

The model was already loaded when this trial began; no lifecycle load/unload was
needed. Its reported context length was 262,144, not independently verified.
No streaming trial, unfamiliar-repository discovery, automatic routing, background
supervisor, audio, personal capture or CLARITY publication was demonstrated.

## Supervisor review

The central diagnosis and header-placement approach were correct. The proposed
config-independent credential storage preserves existing constructors and avoids
adding a secret to strict encoder fingerprints. Supervisor corrections were needed:

1. Its test plan expected `_FakeClient.calls` to capture headers; that fake records
   only method/path/body. Tests now use actual httpx requests with MockTransport.
2. It suggested `config.__dict__` despite a slots dataclass; tests use `asdict`.
3. The asserted config field count was wrong and some endpoint line citations were
   imprecise. The source-client location was correct; endpoint facts were checked
   independently against the frozen source reads.
4. It did not read the strict encoder or account for token echoes in HTTP error
   bodies. The supervisor reviewed the encoder and added identity and error-redaction
   regressions. Authentication does not change reported model/provenance identity.
5. Its proposal supplied a test plan rather than executable tests. No model-produced
   patch was automatically applied, and the worker correctly did not claim tests ran.

Conclusion: useful draft work, still requiring source review and executable checks.
Protocol completion alone must not authorize merging or consequential actions.

## Validation and artifacts

Provider suite: **11 passed**. Strict encoder suite: **5 passed**. Bounded-runner
guard suite: **3 passed**. Full local CI status and artifact hashes are recorded in
[the machine-readable receipt](qwen-worker-trial.json). Hosted CI is not implied by
local checks. The runner guard checks are included in gated CI.

Raw requests/results, frozen numbered source excerpts, usage, model-list snapshots
and test/CI logs remain private under `.runtime/qwen-worker-trial/` (directory mode
700, worker receipts mode 600). The public receipt includes hashes and measured
summary values, not raw reasoning or credentials.

Re-run a new trial with an already loaded local model:

```bash
.venv/bin/python scripts/run_qwen_worker_trial.py \
  --model qwen3.6-35b-a3b-splash \
  --output .runtime/qwen-worker-trial/new-attempt
```

The runner checks loaded native model metadata before sending generation. It does
not download or load a model. The task is intentionally fixed to this authentication
investigation; after the patch, new runs inspect the repaired code and do not
reproduce the original missing-auth condition. Reconstruct that original source
from the recorded base commit/frozen source hashes in an isolated checkout when
comparing models. The source character ceiling is not a complete-request tokenizer
budget and does not complete Priority 4.

## Next steps and priority accounting

The bounded investigation/proposal/supervisor-review milestone is complete. Optional
embedding bearer headers are implemented and tested offline; enforcement against an
authentication-required deployed LM Studio server remains an operational check.
Next evaluate the worker through an isolated rest_proxy route, then compare fixed
source tasks under local-only and supervised conditions with the same grading and
actual usage accounting. Build an automatic router only after those results justify
its policy. No inference provider was migrated.

See [current five-priority status](../../../docs/five-priority-status.md): this trial
adds functional evidence to Priority 5 and closes an optional adapter gap; it does
not complete full embedded integration, FIRE recovery, context budgeting or release
acceptance. The personal/public-interest direction remains separate.

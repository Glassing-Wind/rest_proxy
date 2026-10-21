# Bounded cockpit evaluations

The [October 17 pilot](../benchmarks/reports/2026-10-17/cockpit-pilot/README.md)
records live compatibility and quality failures. No helper has been promoted to
an automatic role. The earlier [model review](cockpit-model-routing.md) remains
a dated inventory and recommendation.

`scripts.evaluate_cockpit_models` evaluates six frozen synthetic sources. It
imports no task registry, corpus ledger, operational configuration or model
lifecycle code. The runner never loads a model or performs a tool call. Select
and load an instance explicitly before running it, and preserve existing embedding
instances. The HTTP adapter accepts only numeric loopback origins with an explicit
port, ignores ambient proxy configuration, and refuses redirects.

From the repository root, use an existing, absolute, canonical private parent
directory. Each output directory must be new; existing paths and symlink paths
are refused. Output directories are mode 0700 and files are mode 0600.

```sh
PILOT_PARENT=/absolute/private/parent
.venv/bin/python -m scripts.evaluate_cockpit_models run \
  --base-url http://127.0.0.1:1234/v1 \
  --model EXACT_LOADED_INSTANCE_ID \
  --max-output-tokens 2048 --timeout-seconds 90 \
  --output "$PILOT_PARENT/new-local-trial"
```

The default adapter uses OpenAI-compatible chat completions with JSON Schema
decoding, temperature zero, and the runtime's default reasoning behavior. The
current schema uses `anyOf` for typed values: the MLX runtime rejected the original
multi-type `type` array. Schema support still needs a live compatibility check.

For a **separate diagnostic condition**, LM Studio's
[native chat API](https://lmstudio.ai/docs/developer/rest/chat) exposes a documented
reasoning-off control:

```sh
.venv/bin/python -m scripts.evaluate_cockpit_models run \
  --adapter loopback_lmstudio_reasoning_off \
  --base-url http://127.0.0.1:1234 \
  --model EXACT_LOADED_INSTANCE_ID \
  --max-output-tokens 2048 --timeout-seconds 90 \
  --output "$PILOT_PARENT/new-reasoning-off-trial"
```

This sends the same source and extraction instructions, with empty integrations,
`store: false`, and prompt-only JSON. It does not claim constrained decoding parity.
The native response supplies no finish reason: the evaluator conservatively
refuses output at the token ceiling, missing output counts, missing/nonzero
reasoning counts, reasoning text and tool calls. Unsupported reasoning controls
remain explicit provider errors.

The evaluator checks exact typed values, missing and extra facts, unresolved keys,
and full original-line citations. Duplicate JSON keys, nonfinite numbers, invalid
Unicode, code fences, changed model identifiers, tool calls and OpenAI responses
without `finish_reason: stop` are refused. The Unicode fixture checks exact wording
and the unit label; numerical byte-versus-character offsets are not evaluated.

Exact request and response bodies remain in the private output folder, including
failed responses. Summaries retain sanitized usage and reasoning character counts,
never reasoning text. HTTP response bodies are capped at 1 MiB, final text at
32,768 characters, and input JSON at 256 KiB. Timeouts apply to HTTP phases with
elapsed-time checks; they are not a server-side cancellation guarantee. Stop an
unresponsive owned instance before allowing further queued work. The runner now
stops the batch after a timeout and explicitly lists unattempted cases. This was
added after the live MLX stall; the retained pilot predates that safeguard.

## Explicit native handoff

```sh
.venv/bin/python -m scripts.evaluate_cockpit_models package \
  --output "$PILOT_PARENT/new-native-package"
.venv/bin/python -m scripts.evaluate_cockpit_models evaluate \
  --input /absolute/private/native-findings.json \
  --model OPERATOR_LABEL \
  --output "$PILOT_PARENT/new-native-score"
```

Packaging performs no generation. Supply the package only through an explicitly
selected native workflow. Evaluation accepts the package's six-result findings
contract, not a vendor CLI envelope. An operator's model label is unverified;
native authentication, usage, tool activity and terminal status need their own
receipt. The evaluator does not launch arbitrary commands or reuse provider OAuth.

The repaired `~/bin/agy` starts the installed IDE. The separate native headless
Antigravity CLI is pinned under `~/.local/share/rest_proxy/cockpit-tools/`; it is
not added to PATH. In the pilot, its stream initialization advertised 60 tools
despite a workspace-only agent requesting none. The controller stopped before
submitting the prompt. Treat that route as blocked until the effective tool surface
can be verified; a successful process exit or a permission preset is insufficient.
See the [headless contract](https://www.antigravity.google/docs/cli/headless/) and
[customization changelog](https://www.antigravity.google/docs/changelog?tab=cli).

## Verification and interpretation

```sh
.venv/bin/python test_cockpit_eval.py
.venv/bin/python -m ruff check graphrag_core/cockpit_eval.py \
  scripts/evaluate_cockpit_models.py test_cockpit_eval.py
```

The offline tests use synthetic fixtures and mock HTTP only. Live generations
require the intended loaded model and fresh output folders. A zero CLI exit means
evaluation completed; inspect `accepted_cases`, per-case scores and error stages
to determine acceptance.

Keep all failed attempts and declare changed schema/API/prompt conditions before
rerunning. Tokenizers, model contexts, thinking defaults and decoding paths differ.
Six tiny cases are a smoke test, not evidence of general model superiority. Measure
lead usage, correction/review time, quota and billed costs before claiming savings.
These trials never approve a lesson, train a model, or authorize source actions.

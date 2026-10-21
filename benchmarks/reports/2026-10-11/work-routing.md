# FIRE work routing inspection — October 11, 2026

Observed local checkout: `codex/task-provider-adapter`, HEAD
`8bc9bdf2bcd147accc4af1661afce5b258c0a9f6`. The working tree contained existing
AGENTS/README/documentation changes and untracked workflow/configuration/gateway
files. This receipt describes a local inspection, not a clean release or a cloud run.

## Result and recommended route

The shared repository service is connected, while worker/reviewer handoffs remain
explicit. Keep Codex responsible for this delivery's source verification, changes
and acceptance. Use Antigravity or Claude for a bounded separate assignment when
useful, Siri for intake/advisory review, Linear for the delivery queue and a verified
cloud workspace for isolated validation. No new orchestration framework is needed
for the existing demonstration.

## Fresh observations

| Surface | Method / observed result | Limit |
| --- | --- | --- |
| Repository | Git identity/status/history inspected at the HEAD above. | Existing local edits are separate from that committed revision. |
| Antigravity | Safe bundle metadata, filtered process inventory and allowlisted MCP configuration fields inspected. Antigravity 2.19.1 and Antigravity IDE 2.5.5 are installed; the IDE is running. Both client configuration files point `graphrag-brain` to `http://localhost:8001/mcp` and separately configure XcodeBuildMCP. | Configuration and a running process do not prove an Antigravity tool call, model route or supervisor integration. |
| Shared MCP | Loopback port 8001 has a Python listener. This Codex chat called `describe_file` with `include_source=True` for `graphrag_core/app_state.py`, lines 1–30. Origin was current local file; SHA256 `1f36079e23414d9edb17e35eea965c7416f765033658d912805bea111c16da52`. | Verifies the current-source reader here; indexed/embedding health, Antigravity's own client callability and cloud access were not tested. |
| Claude terminal | Executable found; version/help and sanitized `claude auth status --json` inspected. Version 2.1.296; `loggedIn=false`, `authMethod=none` in this shell. | No inference executed. This does not establish another shell, Desktop session or provider's login state. No task adapter was found in the reviewed implementation. |
| Siri shortcuts | Local Shortcuts list contains `FIRE Capture Task` and `FIRE Review Evidence`. | Neither was run; current action contents were not inspected. Existing review documentation identifies its fixed historical bundle as stale. |
| Linear | Connected workspace/issues queried read-only. MIC-5 and MIC-6 are Todo; both have concrete outcome/acceptance descriptions. | No issue, project or task state changed. No Linear-to-registry synchronization was established. |
| Cloud Environment | User reports a configured environment. Installed runtime skill read and enabled tool inventory/plugin discovery inspected. This chat's executor is local macOS; no environment-status capability was exposed for verifying an attached instance. | Cloud configuration, attachment, credentials, networking, dependency readiness and execution remain unverified here. This is not evidence that the user's environment is missing. |

Antigravity client configuration hashes matched at inspection:
`78d026aab1d51f41ddca34a8f41a9798a34ab6d3926c680b5346346483b9cb7b`.
Only server names, executable/arguments and credential-free URL fields were
reported. Credential values, private conversation bodies and raw process arguments
were excluded from the receipt.

Existing delivery queue:

- [MIC-5: Prepare the five-minute rental investigation walkthrough](https://linear.app/michael-marler/issue/MIC-5/prepare-the-five-minute-rental-investigation-walkthrough).
- [MIC-6: Select one bounded workflow investigation pilot and confirm fit](https://linear.app/michael-marler/issue/MIC-6/select-one-bounded-workflow-investigation-pilot-and-confirm-fit).

These are rental/workflow tasks. The new repository audit is a separate sample;
neither issue was silently marked complete or repurposed.

## Source and historical evidence

The following references were inspected at the observed HEAD; line numbers are
starting locations, not claims that the new documentation is committed there.

| Evidence | Verified boundary |
| --- | --- |
| [app_state.py](../../../graphrag_core/app_state.py), lines 9, 20–46 | Antigravity directory is a legacy fallback; active default lmproxy configuration uses `~/.config/lmproxy`. |
| [graphrag_mcp_supervisor.py](../../../scripts/graphrag_mcp_supervisor.py), lines 3–11, 41–46, 157–167 | Client-labelled server-process supervisor; launches MCP server, not an agent team. |
| [operations](../../../docs/operations.md), lines 3–26, 36–62 | Shared HTTP daemon and generic client connection; STDIO fallback is separate. |
| [task_capture.py](../../../scripts/task_capture.py), lines 9–25 | Bounded stdin intake creates a `rest_proxy` read-source task; no agent invocation. |
| [task_run.py](../../../scripts/task_run.py), lines 14–41 | Explicit one-shot execution with task/revision/source/endpoint/model and `--execute`. |
| [task_provider.py](../../../memory/task_provider.py), lines 46–84, 105–151 | Numeric loopback HTTP generation, bounded structured output, redirects/proxy trust disabled and limited provider metadata. |
| [task_worker.py](../../../memory/task_worker.py), lines 11–62, 63–110 | Bounded source handoff and exact citations; returned finding requires separate review. |
| [task_review_export.py](../../../scripts/task_review_export.py), lines 9–48 | At most 8 KiB linked historical bundle; omits bearer/workspace metadata and does not transmit. Source/goal can still be private. |
| [task_review_capture.py](../../../scripts/task_review_capture.py), lines 12–38 | Checks exact task/submission/revision linkage; imports advice with `apple-shortcut` label, not approval. |
| [Apple review handoff](../../../docs/shortcuts/fire-review-handoff.md), lines 1–35 | Fixed historical bundle stale; generic preview/input/capture wiring remains unfinished. |

Historical October 7 receipts establish one real Qwen worker attempt and manual
Codex correction, plus correction/recovery timeouts. The Apple Cloud review repeated
an incorrect claim rather than providing the requested assessment. Underlying
Apple model identity and usage are unverified. Those receipts do not prove current
live task state, calibrated review, completed autonomous work or savings:

- [Siri intake](../2026-10-07/siri-task-intake.json).
- [Siri worker and manual review](../2026-10-07/siri-worker-review.json).
- [Correction timeout](../2026-10-07/siri-correction-timeout.json).
- [Recovery timeout](../2026-10-07/siri-expired-recovery-timeout.json).
- [Apple live review](../2026-10-07/apple-live-review.json).

## Revision-bound demonstration

The [public sample audit](../../../docs/samples/fire-repository-audit.md) and
[evidence archive](FIRE-Audit-Evidence.zip) were copied unchanged from the completed
October 11 deliverables. They audit public-main revision
`d2ae74c4adc6233981b16d38b26ae290e023a47b`, not this working branch.
The archive contains its manifest, methods, validation logs and comparison records.
All publication/storage source and native tests in that audit's evidence inventory
were unchanged between those revisions on inspection. Task provider/worker/registry
and dispatch have changed; the older audit does not certify their current behavior.

That audit recorded 56 passing focused tests and nine selected indexed/native source
windows with identical contents. It used an eight-file disposable index and synthetic
vectors for text/source access. No blinded investigation, elapsed-time/token comparison,
real embedding quality or customer outcome was measured. Do not turn source-window
parity into a claim that FIRE or an extra agent made the investigation faster.

## Documentation refinement and remaining gates

Updated the workflow with participant roles, explicit evidence/worker/advisory
handoffs, isolation, cloud boundaries and data controls. Removed blanket search-speed
claims, clarified current-source versus historical-snapshot reads, corrected the
outreach status and kept the $750 workflow offer separate from the proposed $900
repository audit. The latter remains proposed pricing, not accepted terms or demand.

This inspection made no production-code, service, index, provider, shortcut, task
registry, authentication or external issue changes. No model inference was executed.
Documentation links/format and preservation of unrelated work are checked separately
from the historical audit acceptance. Nine Markdown documents had 101 local link
targets checked with no missing files; tracked-document whitespace checks passed.
AGENTS content is byte-identical to the pre-edit baseline. Both imported artifacts
match the original SHA256 values, and the ZIP integrity check passed. Two parallel
read-only reviewers found no blocking source/scope accuracy concern. No new
regression-suite or hosted CI result is claimed.

Imported artifact identities:

| Artifact | SHA256 |
| --- | --- |
| Sample Markdown | `56aeeb4561e4106c2a0d7f73140dcd4d6812dcfdd675a4ef024603531be4a78f` |
| Evidence ZIP | `416a7f823ad83a058b182296c1043b40b9ab7b3717084ec31552ca748d4bc600` |

Open gates: fresh cloud attachment/run; authenticated bounded Claude review;
Antigravity-specific callability; fresh generic Siri review input/capture; automated
supervisor integration; current indexed/embedding health; equivalent end-to-end
performance measurement; and actual buyer acceptance. None is a prerequisite to
reviewing the already completed source-backed sample.

## Follow-up: saved cloud configuration found

After the user clarified that setup may have been in Codex, account Settings ->
Codex Cloud -> Environments showed a private `rest_proxy` environment associated
with `Glassing-Wind/rest_proxy`. Its configuration preview includes Python 3.14.7,
Rust 1.99.0, CI-profile requirements and the pinned parser wheel; package-manager
internet access, no additional allowed domains, no proxy secrets or environment
variables, and use restricted to the owner. These are saved settings, not fresh
runtime/network/credential observations.

The earlier `Set up rest_proxy` cloud chat was read. Its retained completion reports
801 tests passed, 11 skipped, MCP/proxy health and tool-choice checks, with repository
files unchanged. These are historical onboarding records, not rerun acceptance for
the current branch. Live inference/databases and optional LadybugDB/LanceDB workflows
are excluded from the saved offline setup. A separate earlier architecture chat
also contains a successful `cloud_environment.environment_status` tool call; that
capability is not exposed to this current local executor.

Opening the settings Edit action generated an unused configuration draft chat.
No settings were edited or published; the generated draft was archived. The original
saved environment remains listed. No cloud validation was launched and the sample
audit's provenance is unchanged.

See [capability priorities](../../../docs/capability-priorities.md) for Spark/Pillow
integration status and a next demonstration using existing change-impact tools.
Follow-up documentation validation resolved 111 local file links across ten Markdown
documents, with no missing files or new-document whitespace errors. Original audit
artifact hashes and unrelated AGENTS content remained unchanged. No cloud job or
regression suite was executed during this follow-up.

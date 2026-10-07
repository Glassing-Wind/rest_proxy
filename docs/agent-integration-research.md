# Agent interfaces and repository reuse — October 7, 2026

## Siri task -> Qwen -> explicit Codex review — October 7

An explicit one-task CLI (`python -m scripts.task_run`) now requires --execute,
state/project/task/revision, source range and loopback endpoint/model. It calls the
existing worker once, without polling, retries, lifecycle calls or automatic review.
Missing --execute rejects before state creation; one CLI test plus five worker and
twelve provider tests pass, focused Ruff/diff checks pass.

User-authorized Siri task 1214c5b2e644470197b691e7e8e849aa was supplied only
scripts/task_capture.py lines 1–33. Already-loaded Qwen produced a source-validated
finding, review_pending revision 4, reporting 866 prompt/2414 completion tokens.
Manual Codex review requested correction: project scope was called a type, broader
coordination question lacked source coverage, and limits were keywords. Registry
reopen confirms queued revision 5 with original finding and review retained.
[Evidence](../benchmarks/reports/2026-10-07/siri-worker-review.json).
This is one real supervised handoff, not a persistent background team or automatic
Codex adapter. No automatic retry occurs. Prior cancelled Siri task stays cancelled.

Queueing uses TaskRegistry.create and private SQLite; run_worker claims a revision,
reads bounded source, checkpoints, generates and submits for review. Full team needs
multi-file evidence selection, user request preview/correction, scoped task access,
a supported supervisor adapter, bounded dispatch scheduling and failure/cancellation
handling. Next implement multi-source worker evidence without loosening citation
checks; persistent scheduling stays opt-in and requires explicit lifecycle approval.
No quality/token-savings claim, and five overall priority gates remain open.


## Siri intake accepted — October 7

User screenshots establish Siri invocation, request dictation and queued confirmation.
Reopening task 852f3af340d8442f8b04930bcfccc75d confirms queued revision 1, read_source
only, zero attempts and no claim; saved goal matches the visible transcript.
[Receipt](../benchmarks/reports/2026-10-07/siri-task-intake.json).
GUI and Siri local intake are now evidenced. Dictation misrecognized Qwen as Quin
and Codex as Kodak Kodak (also changed the opening request). Accurate intent capture
is not established. Next add request preview/confirmation with edit or cancellation
before dispatch; retain original transcript separately from user-confirmed corrections.
No automatic agent invocation occurred. Authenticated/scoped dispatch and semantic
review remain open; do not silently rewrite voice requests from guessed intent.


## Shortcut GUI intake accepted — October 7

User screenshot confirms queued task 37f060b4b4d542798757f48cbf72f7da. Reopening
private local registry confirms rest_proxy scope, queued revision 1, read_source
only, zero attempts and no claim. No agent invoked. Request text is excluded from
published evidence. [Receipt](../benchmarks/reports/2026-10-07/shortcut-task-intake.json).
GUI task intake and durable reopen are now verified; Siri voice invocation remains
untested. Next scoped worker/supervisor dispatch with explicit review. This does
not establish a persistent team or authenticated remote task interface.


## Verified state

PR4 revision c6509dd passed all eight hosted checks on inspection October 7.
Earlier “unverified” described pending results, not a failure. New changes need
new hosted checks. Qwen has one synthetic source-validated finding in review_pending;
no persistent multi-agent team, authenticated task endpoints or automatic supervisor.

## Supported integration paths

| Participant | Supported path and evidence | Our next acceptance |
| --- | --- | --- |
| Siri / Mac | [Apple Shortcuts](https://support.apple.com/en-euro/guide/shortcuts-mac/apdf22b0444c/mac): Siri can run a named shortcut. Local shell action can submit stdin to our task capture helper. | FIRE Capture Task configured in Shortcuts; user enabled scripting. Ask for Input -> stdin shell action -> Show Content. GUI test waits at runner input not exposed to automation; end-to-end/voice acceptance pending. |
| iPhone / glasses | Separate app/Shortcut transport to an authenticated service; Mac shell action is Mac-specific. | No remotely reachable task intake. Do not use phone loopback to address Mac. |
| Claude Code Desktop | [Current Desktop docs](https://code.claude.com/docs/en/desktop) describe MCP configuration through project/user settings and desktop connectors. Installing/running Desktop does not connect it to our task registry. | Add narrowly scoped task MCP interface and test claim/read/submit against disposable state, with review retained. No Claude settings or conversations changed. Standalone claude executable not on current shell PATH. |
| Claude programmatic worker | [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) exposes Claude Code loop in Python/TypeScript, MCP, permissions, sessions and hooks. Docs describe authentication/usage terms; desktop subscription is not evidence of permission to redistribute its login/limits in our product. | Explicit adapter permissions, credentials and budget; no SDK installed or paid inference invoked. |
| Codex supervisor | [Official harness interfaces](https://developers.openai.com/blog/codex-as-a-platform): codex exec for bounded jobs, SDK for programmatic workflows, app-server for persistent sessions/events/approval handling. Local codex executable exists. | Read-only structured review fixture through exec first; SDK later if resume/stream needed. This chat is not an automatically invoked endpoint. |
| Muse | Product identity unspecified. | User asked which app/website; API, authentication, export and webhook capabilities unverified. Manual structured handoff is possible in principle, not connected. |

Recommendation: keep the local task registry and evidence contracts authoritative.
Adapters translate participant requests/results into that contract. Start with manual
review; no automatic deployment, merge, tool expansion or message forwarding.

## Framework choice

[Pydantic AI](https://pydantic.dev/docs/ai/guides/multi-agent-applications/)
provides delegation patterns and typed outputs. Its
[durable execution integrations](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)
require an execution backend; selecting the library does not make our SQLite registry
an orchestration engine. First candidate for a small optional Python coordinator.
[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
provides checkpoints/interrupt workflows; consider when branching, durable resumes
and parallel scheduling exceed our simple dispatch needs. Avoid two conflicting task
state authorities; define mapping and idempotency before adopting either.
[Magentic-One](https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/magentic-one.html)
is a broad orchestrator/team reference. [AutoGen](https://github.com/microsoft/autogen)
is now maintenance-only and recommends Microsoft Agent Framework for new projects.
Do not add AutoGen as a new foundational dependency here. No framework substitutes
for Siri/app interfaces, authentication, evidence validation or review correctness.
These are researched options, not installed/evaluated integrations.

## Learning and document structure

Citation validation proves bytes/path/range match evidence. It does not prove a
claim is entailed, complete, current, or free of omitted counterexamples. Example:
citing `return sum(values)` supports summation; it does not support “validates all
inputs.” Review should retain claim, supporting passage, counterevidence, verdict,
correction, source hash/date and reviewer identity. Accepted/corrected cases can feed
retrieval memory and held-out evaluations. Model fine-tuning is a separate deliberate
step; correction persistence alone is not weight training. Reviewer IDs are currently
local labels, not authenticated identities.

A document syntax tree can deterministically represent headings, sections,
paragraphs, lists, links, code blocks and source spans. For free prose there is no
single programming-language AST. Semantic claim/entity/relation extraction is a
second, fallible layer, with provenance and explicit uncertain/corrected states.
Recommended slice: Markdown/TXT structure fixture -> stable source spans/hash ->
claim/evidence/review records -> retrieval, then held-out accuracy tests. Keep AST
structure and model inference distinct in schema and reporting.

## Local repository findings (source review, not runtime certification)

Both repositories have pre-existing modified/deleted/untracked files. Neither was
changed, installed or started. No license file was found by the bounded filename
search; verify ownership/dependency licenses before copying code into distribution.

**RepoAnalyzer is the stronger immediate reuse candidate.**
`context_adapters/graphrag_brain.py` explicitly separates normalized indexed context
from runtime MCP invocation. `learning_core/service.py` accepts interchangeable
context adapters. `learning_core/models.py` provides EvidenceItem, ConventionSignal,
LearnedPattern, PatternRecommendation and framework/adoption models. These concepts
fit repository learning and suggest an adapter between FIRE evidence and learning
outputs, with hashes/ranges/review labels added where needed.
`parsers/custom_parsers/custom_plaintext_parser.py` creates document/heading/paragraph/
list-item nodes with locations. Its initialization pulls in caches, global pattern
processors and AI resources, so importing the whole subsystem is not a minimal
embedded parser dependency. Its relevance/similarity/documentation/code-quality
functions return constants (0.8/0.7/0.9/0.85); those are placeholders, not measured
learning quality. `learning_core/framework_profiles.py` confidence calculations
also include file-count heuristics. `custom_asciidoc_parser.py` initialization calls
initialize_caches without a visible local import in the reviewed imports; check
runtime coverage before reuse. These observations do not establish all paths fail.

**GithubAnalyzer supplies lower-level parser ideas.**
`src/GithubAnalyzer/models/core/ast.py` retains Tree-sitter byte/point ranges,
error/missing nodes and recursive node dictionaries. Useful for source-position
normalization and parse-error reporting. Recursive node_to_dict repeats text at each
node, risking large duplicate payloads; bounded/lazy snapshots fit FIRE better.
README architecture depends on PostgreSQL/pgvector and Neo4j/APOC/GDS, so do not
adopt its storage stack wholesale. Preserve LadybugDB/LanceDB and modified ts-pack.
Source review suggests borrow interfaces and fixtures first, not a repository merge.

## Mac Shortcut completion

Draft name: **FIRE Capture Task**. Ask for Input prompt is configured. Run Shell
Script is present but macOS says scripting actions are disabled. User must decide
whether to enable scripts in Shortcuts settings; this enables a broader scripting
capability, not just FIRE. We did not change the protection.

After that decision, paste [the shell action](shortcuts/fire-capture-task.sh) into
Run Shell Script, select input from Ask for Input, pass input **to stdin**, then add
Show Result. Script uses an absolute project Python path and never interpolates the
request into shell code. It creates a queued read-only rest_proxy task in
`~/Library/Application Support/FIRE/tasks`, prints only a confirmation and task ID,
and invokes no inference, tools, supervisor or background worker. State is private
local SQLite; request text is retained. Mac-specific paths require updating after
moving/reinstalling the project. Siri invocation has not been tested.

Two disposable helper tests verify reopen, literal shell-like text, queued/read-only
state and rejected empty/oversized input without state creation. Offline tests do
not establish voice dictation or GUI action wiring works.

Next milestones: finish Shortcut action wiring/run after the setting decision;
expose authenticated/scoped task MCP operations for Claude/Codex; bounded supervisor
review acceptance; separate document structure adapter with held-out fixtures.
Primary priority 3, with priority 4 evidence bounds and priority 5 outcome evaluation.

## Shortcut wiring checkpoint — later October 7

User enabled scripting; enabled checkbox verified. FIRE Capture Task now contains
Ask for Input (existing question), the absolute-path shell command with Provided
Input passed to stdin, and Show Content bound to Shell Script Result. Administrator
execution remains off. Run started, but runner input dialog is not exposed by the
available app automation surface. User asked to enter a synthetic test phrase.
No task state directory existed at inspection; do not claim a queued task or successful
GUI/Siri acceptance yet. Prior revision 4479d4c passed all eight hosted checks.
Earlier disabled-script sections above describe the initial state.

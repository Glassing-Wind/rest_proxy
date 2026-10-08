# Durable knowledge gateway direction — October 7, 2026

Basis: full user-provided [architecture study](research/rest-proxy-architecture-study.md),
read October 7 and reconciled with local HEAD 52ffb86 and open draft PR #4.
The study inspected main d2ae74c; the active branch adds task recovery, advisory
retention and failure inspection. Study source links are preserved as supplied;
upstream protocol/license claims have not been independently reverified in this
implementation slice. No new dependency or license decision relies on them.

REST proxy may expand into a configured knowledge gateway: forwarding → durable
journal → asynchronous normalization → evidence stores/indexes → agent tools.
Existing inference routes and their optional provider behavior must stay compatible.
This direction is proposed, not implemented or runtime-validated.

## Decisions for the first proof

Use one explicit allowlisted HTTP upstream and one read-only JSON endpoint with a
local fake upstream in tests. No generic CONNECT proxy, browser interception, ambient
traffic capture, payment/message replay or deployment. Forwarding must not depend on
embeddings or model availability. Keep capture disabled by default.

Use a separate private SQLite event journal for the native prototype, rather than
repurposing mutable FIRE checkpoints as a traffic log. Reuse private-directory,
bounded JSON and scope conventions where appropriate. Explicitly choose SQLite
transaction/durability settings and document host/filesystem limits; a commit alone
must not be advertised as proof against every power-loss scenario.

Required capture must commit an intent before forwarding and commit a bounded result
before acknowledging successful capture. Storage failure before dispatch prevents
forwarding; failure after upstream response must expose incomplete capture rather
than imply the upstream was never contacted. Best-effort capture may forward despite
capture failure but must report incompleteness. Decide exact client status semantics
before implementing either policy. Upstream timeouts remain indeterminate outcomes.

Record event ID, scope, schema version, upstream identity, timestamps, request/result
hashes, redacted bounded payload and completeness state. Persist no authorization
headers by default. Redact before journaling; hashing is not anonymization. Define
retention/deletion and backup behavior. No private life/health data in this first proof.

Replay means rebuilding a local evidence projection from stored events. It performs
no HTTP forwarding, model invocation or external action. Apply each scoped event ID
idempotently in the same transaction as projection acknowledgement. Crash after a
journal commit but before projection must permit safe catch-up without duplicate
projection. Missing/partial response remains explicit evidence, never reconstructed
as a successful response. Independent new model runs need explicit authorization.

First retrieval can be a bounded scoped event lookup exposed to an agent-facing
contract. Do not promise coherent graph/vector publication until that path has its
own staging/publication evidence. LadybugDB/LanceDB remain the embedded graph/vector
choices; LM Studio stays optional. PostgreSQL, NATS/Kafka, Spark and orchestration
frameworks remain candidates only if measured requirements justify their complexity.
No new dependencies or licensing changes are part of this decision slice.

## Existing foundations and gaps

memory/fire_store.py holds scoped snapshots; memory/task_registry.py holds mutable
revision-checked tasks. Worker recovery, failure records and sanitized inspection are
implemented on the active branch. They can consume journal evidence later, but do
not provide traffic capture durability or exactly-once upstream execution.

proxy/handlers_streaming.py uses background state saving; proxy/openai_provider.py
uses best-effort asynchronous conversation persistence. Those paths are not evidence
of crash-safe traffic journaling. Preserve their existing behavior in the prototype.

Task/evidence contracts should remain independent of a team framework. Current local
Siri intake, Qwen dispatch and advisory reviews are explicit/manual. Persistent
cross-session agent orchestration is unfinished. MCP remains the existing tool
surface; A2A, LangGraph and Temporal are future evaluations, not installed integrations.

## Acceptance and sequence

1. Build offline journal append and scoped retrieval with bounded redaction, required
   storage-failure behavior, and subprocess crash/reopen evidence.
2. Add local projection replay: duplicate event delivery and interruption leave one
   projection per scoped event, with no network/model/action calls.
3. Route the single controlled fake-upstream API; test crash boundaries, timeout,
   oversized/partial response, disk-write failure and cross-scope denial.
4. Add bounded agent retrieval and backup/restore drill; then consider graph/vector
   publication. Streaming capture needs separate semantics and tests before adoption.

These are acceptance targets, not completion claims. First proof deliberately excludes
streaming and arbitrary upstreams. Existing five priorities remain active: journal
work spans reliable recovery (1), storage (2), continuity (3), context (4) and outcome
validation (5); it does not replace unfinished repository-investigation evaluation.

Wearable health/Oura, automotive manuals and fire investigation remain separate
exploratory applications. No diagnostic, forensic-quality or commercial-validation
claim follows from this gateway design. Keep current license unchanged; ownership,
contribution and dependency audit precedes any future license proposal.

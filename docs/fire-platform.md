# FIRE — Find, Integrate, Retrieve, Explain

Accepted product direction, October 3, 2026. FIRE names the persistent context
platform being developed in rest_proxy. The repository/package name and existing
interfaces remain unchanged. This document specifies intended behavior; it does
not claim that the full platform is implemented.

## The cycle

| Stage | Responsibility | Output |
| --- | --- | --- |
| Find | Discover repository facts, user decisions and investigation evidence. | Scoped observations with original source references. |
| Integrate | Connect evidence and retain decisions, unresolved questions and task state. | Durable relationships and versioned checkpoints. |
| Retrieve | Select fresh, relevant evidence for the current task and model budget. | A bounded context bundle with citations and explicit omissions. |
| Explain | Ground conclusions in evidence and record what the investigation changed. | Supported conclusions, uncertainty and the next checkpoint. |

MCP exposes investigation and context tools. The optional REST proxy can assemble
context before inference. LadybugDB, LanceDB and our ts-pack fork are the intended
native storage/parsing foundation; backend integration remains in progress.

## Proposed supporting method: WET

October 4 discussion proposed **WET — Working Evidence Trail** as a method
supporting FIRE. This is a working acronym, not an adopted product name or an
existing software dependency. FIRE describes the investigation cycle; WET
describes the evidence retained behind it.

A working evidence trail links original sources and observations to processing
steps, interpretations, conclusions and later corrections. Preserve source
identity/version, scope and timestamps; distinguish observed, inferred and
user-confirmed claims. Retrieval and compaction must retain those links and
disclose unavailable originals. These requirements extend the provenance direction
below; they do not establish that durable trails are already implemented.

The separate wearable discussion also proposed
[WATER — Watch, Annotate, Trace, Evaluate, Retrieve](smart-glasses-project-direction.md#proposed-workflow-name-water).
It remains a candidate workflow name for that project.

## Proposed evidence router

Extend the current intent catalog and allowlisted read dispatcher into an optional
router that chooses and explains a bounded evidence plan. Known-file questions
should go directly to source; discovery, caller tracing and provenance questions
need different routes. Scope, freshness and request budget inform selection.
Use deterministic rules for clear cases and evaluate model-assisted planning only
where ambiguity warrants it. Keep direct MCP tool access available.

Own FIRE's routing policy and evidence contracts. LangGraph is an optional workflow
runtime candidate; LightRAG and Fast GraphRAG are document-retrieval benchmark
candidates. None replaces parser-derived code evidence by default. Compare actual
outcomes with current agent-selected tools before adoption. See
[framework findings, compatibility limits and evaluation order](evidence-routing-framework-research.md).
This is proposed work, not an implemented autonomous router.

## Compaction is the continuity boundary

Continuity must survive compression. A summary provides navigation; every material
claim needs a path back to its supporting evidence. Persist the active goal,
explicit user constraints, decisions, open questions and next actions before
discarding their only usable representation. Retention permissions still apply.

Evidence references must include project/task scope, source identity and version
or content hash where available. Distinguish original observations, user decisions
and generated summaries. Superseded decisions retain their history but must not
be restored as current instructions. Edited or deleted source must be revalidated
before it supports a current-code claim.

Recovery must disclose missing originals, truncated tool output, unknown freshness
and failed persistence. The current memory records already allow truncated raw
tool output, and retrieval returns compact strings; these are concrete gaps in
recoverability and provenance, not proof of durable evidence preservation.

An external client's compaction may occur without notifying FIRE. Support explicit
checkpoint/resume operations and periodic checkpointing; do not assume interception
of Codex or another client's internal context management.

## First implementation slice

Develop this against existing backend interfaces while embedded integration
continues. Reuse memory/types.py, memory/summary.py and memory/retrieval.py.

1. Define versioned evidence references and scoped checkpoint contracts. Include
   goal, accepted constraints, decisions with origins, unresolved questions, next
   actions and evidence IDs. Represent unavailable originals explicitly.
2. Preserve retrieval provenance through ranking and assembly instead of reducing
   every hit to an untraceable string. Maintain existing compatibility outputs.
3. Define a context bundle and complete-request budgeting contract. Select and
   deduplicate before rendering, preserving citations and recording omissions.
4. Add opt-in checkpoint/resume tools through the existing tool catalog. Restore
   state only within the requested project/task scope; revalidate source evidence.
5. Run a controlled compaction/resumption evaluation before advertising continuity.

No inference-provider migration or automatic database migration is implied.
Optional persistence failures remain best-effort and must not break requests.

## Acceptance scenarios

Initial implementation: memory/types.py now defines EvidenceReference,
RetrievedEvidence and TaskCheckpoint contracts. Memory assembly returns structured
retrieved evidence alongside its existing compact strings, retaining returned
source IDs and scope through ranking/selection. Native graph memory search returns
scope fields. Unknown freshness and original availability remain explicit; these
contracts do not yet persist FIRE checkpoints, fetch originals or implement resume
tools. test_fire_context.py checks scope matching, ranked provenance, shared-prefix
evidence, bounded deduplication and storage-outage compatibility.

- Resume with the original conversation removed: recover the goal, constraints,
  current decision and unresolved question, then retrieve supporting originals.
- Correct a mistaken summary: retrieve the original, record the correction and
  prevent the obsolete conclusion from becoming current task state.
- Change or delete a cited file: flag historical evidence and retrieve current
  source before asserting present behavior.
- Resume a different project/task: prevent unrelated state and evidence leakage.
- Exhaust the token budget or lose storage: return bounded context and explicit
  omissions/failure status without silently inventing recovered state.

Measure task-state preservation, evidence recovery, citation correctness, stale
claims, actual request tokens, fallback use and end-to-end latency against the same
tasks without FIRE. Passing contract tests alone does not prove better outcomes.

See [context contracts](context-platform-direction.md),
[integrated implementation plan](integrated-platform-plan.md) and
[session roadmap](next-session-roadmap.md).

## October 6 durable continuity implementation

The [explicit checkpoint/original store](fire-durable-continuity.md) implements scoped
MCP save/resume, hashed paged originals, revision-checked corrections, retention and
deletion. A real STDIO-to-fresh-HTTP drill recovers fixture task state and originals
after removing the conversation/source. Earlier contract-only descriptions remain
historical; client compaction integration and automatic current-source validation
are not implemented.

# October 3 enterprise tooling evidence

The compact tool catalog, truthful current-file outline, and portable development
appliance are implemented and checked. Embedded storage remains experimental.
The three-task paired pilot does not establish a speed or token advantage for MCP.

## Implemented and validated

- Primary profile: 12 tools including an allowlisted secondary read dispatcher,
  Context forwarding, structured results, and on-demand secondary schemas.
  Full catalog: 60 tools, 16,414 `o200k_base` tokens / 73,655 UTF-8 bytes;
  primary: 3,687 tokens / 16,567 bytes, a 77.54% schema-token reduction.
  These are serialized catalog counts, not billed tokens or per-turn savings.
- A successfully parsed current file replaces the complete indexed outline,
  including deleted symbols. Content alignment remains explicitly unknown;
  graph relationships and semantic previews remain indexed evidence.
- Compose uses supplied credentials, a configurable read-only workspace, internal
  database ports, loopback MCP publication, bounded startup and mandatory bootstrap.
  A disposable real stack passed indexing, source/symbol/search queries, full restart
  persistence, and source reads during a Neo4j outage. A container-only telemetry
  path error found during validation was fixed and the image rebuilt.
  Synthetic embeddings were used; the 5.34 GiB image and these narrow lifecycle
  checks do not establish production readiness or recovery of every query path.
- Full local CI and the live retrieval-quality gate passed. Relevant Ruff checks
  and whitespace checks passed. Final logs remain in `.runtime/`.
- Disposable embedded checks passed vector upsert, lexical retrieval and project
  scoping, but failed actual graph-query compatibility, transaction rollback and
  independent session connections. No backend migration is warranted.

## Controlled pilot

Six fresh CLI sessions completed with stable source and index identities. MCP
readiness was verified separately by an observed health call, and every MCP arm
also invoked health. All six records pass measurement/protocol validity;
correctness is evaluated separately. Requested model: `gpt-6-astra`, low effort;
CLI 0.160.0 did not report an observed model ID, so model identity is unverified.

| Case | Arm | Seconds | Input tokens | Cached input | Output tokens | Native commands | MCP calls |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Symbol context | Native | 73.479 | 161,477 | 125,952 | 1,364 | 5 | 0 |
| Symbol context | MCP | 71.500 | 177,435 | 101,248 | 1,260 | 3 | 2 |
| HTTP MCP startup | Native | 75.944 | 142,983 | 112,512 | 1,535 | 6 | 0 |
| HTTP MCP startup | MCP | 88.857 | 299,970 | 256,768 | 1,683 | 5 | 3 |
| Search ranking | Native | 120.761 | 237,289 | 175,232 | 2,596 | 9 | 0 |
| Search ranking | MCP | 113.642 | 452,262 | 351,616 | 2,028 | 8 | 2 |
| **Total** | **Native** | **270.184** | **541,749** | **413,696** | **5,495** | **20** | **0** |
| **Total** | **MCP** | **274.000** | **929,667** | **709,632** | **4,971** | **16** | **7** |

MCP aggregate elapsed time was 1.41% greater and input tokens 71.60% greater.
Individual elapsed times were mixed. Native fallbacks are included. Usage is
reported CLI telemetry; cached tokens are included in input, and no billing-cost
inference is made. The comparison used the full catalog with a 15-tool read
allowlist, so it does not evaluate the compact profile's investigation outcomes.

The pilot uses one shared warm index, three code-understanding tasks in this
repository, one counterbalanced order, and no code changes. Reports and grading
case files were excluded from retrieval; native commands were manually audited
for prohibited reads and actions. The shuffled grading packet hides arm labels
and measurements, but wording can reveal tool provenance. Prompt restrictions
are not hard process isolation. A confirmatory study needs isolated indexes,
unfamiliar repositories, repeated randomized pairs, partial/stale indexes and
code-change outcomes.

## Failed attempts and retained evidence

The September 30 comparison remains excluded because MCP was unavailable.
October 3 includes a preflight failed during server restart and a restrictive
preflight reporting unavailable MCP. Allowing discovery before the mandatory
health call passed; the causal mechanism is unverified because no discovery event
was observed. Preflight usage is separate from paired totals.

Two failed indexing attempts encountered Neo4j authentication rate limiting;
a sequential fresh CLI refresh succeeded. Only confirmed inactive namespaces
from those failed jobs were cleaned. The underlying authentication cause remains
unproven. The first appliance harness missed a text-form tool error; that attempt
is retained as failed, and the corrected harness passed after the telemetry fix.
The first grading session hit its CLI usage limit; its partial transcript is
retained separately from the retry.

Machine-readable artifacts: [catalog](tool-profiles.json),
[appliance](appliance-validation.json), [embedded](embedded-feasibility.json),
[index recovery](index-recovery.json), [raw pilot rows](pilot-results.json),
[source/index identities](pilot-baseline.json), [blind answers](blind/instructions.md),
[raw evidence manifest](pilot-raw-manifest.json).
Raw paired transcripts are retained outside the indexed repository at
`/Users/michaelmarler/Projects/graphrag-evidence-20261003-final`.

## Independent grading

The fresh source-only grader completed successfully. All six answers passed
completeness and citation validity. Native answers passed correctness; MCP answers
received partial correctness because runtime availability/count assertions cannot
be established from source alone. The ranking MCP answer additionally described
0.4 as a fixed concentration threshold rather than the configurable
`fallback_ratio` default. No discovered substantive source error is hidden by the
measurement-validity flag.

Paired transcripts support successful MCP invocation and the reported healthy,
aligned 312-file indexing counts. They do not show a distinct discovery event;
claims about a discovery mechanism remain unverified. These transcript observations
are separate from the unchanged source-only grader judgments. See
[independent grading](independent-grading.json), [grader telemetry](independent-grading-record.json),
and [rows with reviews](pilot-reviewed-results.json). The grader's partial failed
attempt and successful retry remain in their external evidence directories.

Next: test whether a shorter catalog improves paired outcomes, with repetition
and unfamiliar tasks; capture source hashes during native indexing before claiming
content alignment; select a maintained graph candidate and establish query/transaction
parity before expanding embedded integration. Enterprise deployment acceptance
remains the broader work listed in the implementation plan.

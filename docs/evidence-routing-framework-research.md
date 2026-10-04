# Evidence routing and framework research — October 4, 2026

## Recommendation and scope

Extend FIRE's existing tool catalog and read dispatcher with a small, optional
evidence router. Evaluate external frameworks behind explicit interfaces rather
than replacing repository indexing or adopting all four frameworks together.
Personal memory and authorized incident investigation remain separate projects.
These are researched design recommendations, not installed dependencies or
verified Python 3.14/backend integrations.

## Existing foundation and proposed router

`tools/brain/tool_catalog.py` already matches intent words, ranks tool guidance and
returns exact argument schemas. `tools/brain/primary.py` exposes 12 primary tools
and an allowlisted secondary read dispatcher. The dispatcher executes a named
tool; it does not autonomously choose an evidence plan. Discovery filtering is
not an authorization boundary.

The proposed router selects an operation or bounded sequence using question,
project/task scope, known file/symbol, available capabilities, freshness and
remaining budget. Keep clear cases deterministic; consider a schema-validated
model-assisted plan for ambiguous questions. Return the selected tools, arguments,
rationale and limits. Clients retain direct access to tools and their own workflow.

| Question | Evidence route |
| --- | --- |
| What does this known file do? | Bounded current source through `describe_file(include_source=True)` or direct read |
| Where is this behavior implemented? | Semantic discovery, symbol context, current source verification |
| What calls this function? | References/call chain and relevant source |
| Can I trust this indexed result? | Provenance, run identity and freshness checks |
| What was true on a particular date? | Proposed temporal retrieval with original observations; not an existing coding-tool capability |

For unfamiliar indexed repositories, check indexing health and project overview.
Do not add mandatory discovery or model calls to exact known-file lookups. Validate
tool arguments and project boundaries; keep automatic execution read-only with
bounded steps, time and context. Missing storage or stale indexes require explicit
limitations and appropriate source fallbacks. Retrieved content remains evidence,
not authoritative instructions.

## Frameworks and architectural layers

| Framework | Verified upstream capabilities / core license | Proposed fit |
| --- | --- | --- |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Stateful workflow orchestration, durable execution, checkpoints and human review; MIT | Optional FIRE agent runner when branching/resumption warrants it; later separate-project workflows |
| [LightRAG](https://github.com/HKUDS/LightRAG) | Entity/relationship retrieval with local/global/hybrid modes and vector chunk retrieval; MIT | Benchmark documentation, manuals, notes and transcripts |
| [Fast GraphRAG](https://github.com/circlemind-ai/fast-graphrag) | Personalized PageRank graph exploration and incremental updates; MIT | Benchmark connected-evidence retrieval on a fixed document corpus |
| [Graphiti](https://github.com/getzep/graphiti) | Temporal facts, bi-temporal tracking, source episodes and incremental hybrid retrieval; Apache 2.0 | Separate personal-memory prototype; potentially authorized incident timelines |

LangGraph's graph represents workflow steps; the repository knowledge graph
represents code facts. Orchestration does not itself decide a correct evidence
route. LightRAG/Fast GraphRAG are retrieval candidates, not replacements for
parser-derived symbols and call edges. Graphiti provides temporal knowledge
management, not capture hardware, consent handling or a complete application.
The temporal framework discussed as “Graphit” appears to be Graphiti.

Graphiti distinguishes when facts apply from when knowledge is recorded, with
history and episode provenance. Applications must still preserve the distinction
between observations, generated interpretations and confirmed facts. Automatic
extraction or invalidation does not prove a claim is correct.

## Storage, packaging and licensing limits

Graphiti currently documents Neo4j, FalkorDB and Amazon Neptune backends. Its Kùzu
driver is deprecated because upstream Kùzu is unmaintained. Embedded FalkorDB Lite
is documented, but that does not establish compatibility with our preferred
LadybugDB/LanceDB direction. A Ladybug integration would require an adapter and
acceptance tests; no such compatibility is claimed. Docker is optional packaging,
not a requirement of the routing concept.

Core licenses above are permissive; they do not establish the license properties
of the complete dependency, storage, model or hosted-service distribution. Before
adoption, pin the candidate version, check that dependency closure, and test clean
Python 3.14 installation plus actual queries, transactions and failure behavior.
Upstream speed/cost claims are not measurements of this project's workloads.

## Evaluation and implementation order

1. Extend the existing catalog into an opt-in evidence-plan contract and router.
   Compare with current agent-selected tool use on fixed repository questions.
2. Measure answer correctness, evidence completeness, freshness errors, tool calls,
   native fallbacks, complete-request tokens and end-to-end latency. Hold source,
   models and index state constant and report setup separately.
3. In the separate personal-memory direction, trial Graphiti with synthetic events:
   changed appointments, corrected object identity and superseded locations.
   Test historical/as-of answers, knowledge-time distinction, source provenance,
   isolation and deletion of originals and derived records.
4. Compare LightRAG and Fast GraphRAG against the current document retrieval
   baseline on one fixed corpus. Change retrieval only after reproducible evidence.
5. Introduce LangGraph if durable branching/resumption justifies it. Preserve
   backend-neutral evidence/checkpoint contracts and ordinary MCP clients.

Own domain routing policy, evidence contracts, budgets and scope boundaries.
Reuse proven generic infrastructure when validated; do not reimplement entire
workflow or temporal-memory frameworks just to reproduce their feature lists.
This evaluation does not displace indexing recovery, coordinated publication,
embedded integration or existing FIRE continuity acceptance work.

## Primary sources checked October 4, 2026

- [LangGraph capabilities and MIT license](https://github.com/langchain-ai/langgraph)
- [LightRAG retrieval modes and MIT license](https://github.com/HKUDS/LightRAG)
- [Fast GraphRAG PageRank and MIT license](https://github.com/circlemind-ai/fast-graphrag)
- [Graphiti temporal model and Apache 2.0 license](https://github.com/getzep/graphiti)
- [Graphiti backend requirements and Kùzu deprecation](https://github.com/getzep/graphiti#installation)

# Open-source hybrid stack research — October 3, 2026

Interpretation: a portable, self-hosted appliance with an application server,
transactional storage, graph evidence and hybrid retrieval. The existing Python
application remains the application layer. Recommendations below are engineering
judgments based on upstream documentation and this repository's evidence;
no candidate database was installed or benchmarked during this research.

## Recommended direction

The original plan's primary product target is a permissively licensed, embedded,
zero-database-server distribution. Prioritize **LadybugDB (MIT) + LanceDB
(Apache-2.0)**, co-located with ts-pack behind the existing Python/MCP interface.
The current server profile is a compatibility and evaluation baseline. Apache AGE
is a secondary contingency, not a prerequisite or the recommended first migration.
A container appliance can eventually package the same embedded engine with one
storage-owning application process.

MIT and Apache-2.0 top-level licenses support the intended permissive selection,
but do not establish that a complete shipped artifact is GPL-free or that every
enterprise will approve it. Audit pinned transitive/native dependencies, ts-pack,
optional extensions, model assets and distributed utilities; produce an SBOM and
required notices. Separate the minimal embedded distribution from legacy database
services and heavyweight optional extras. No complete distribution license audit
has been performed here.

[Ladybug MIT license](https://github.com/LadybugDB/ladybug/blob/main/LICENSE),
[LanceDB Apache-2.0 license](https://github.com/lancedb/lancedb/blob/main/LICENSE).

Deployment profiles behind the same tool contracts:

- **Shared server:** Linux, FastAPI/Uvicorn + MCP, PostgreSQL + pgvector + native
  full-text search, current Neo4j graph backend, optional Valkey cache. Add Caddy
  for TLS when exposing HTTP, pgBackRest for PostgreSQL recovery, and an
  OpenTelemetry Collector for operational signals. Evaluate Apache AGE as the
  graph consolidation candidate before replacing Neo4j.
- **Local desktop:** FastAPI/MCP with one storage-owning process; evaluate
  LadybugDB for graph storage, LanceDB for vector/lexical indexes, and SQLite for
  durable job/run metadata if PostgreSQL is removed. Keep caches in-process
  initially. This profile is a proposed target, not an existing supported backend.

Our immediate need is correct transactions, snapshot publication and trustworthy
source evidence. Additional databases are justified by measured requirements.
The October 3 pilot did not show an MCP performance advantage; storage substitution
alone cannot be assumed to improve coding-agent outcomes.

## Graph shortlist

| Candidate | Upstream evidence | Fit for this repository | Decision |
| --- | --- | --- | --- |
| Neo4j Community | GPLv3; ACID, Cypher, single-instance deployment. Clustering and online backup are Enterprise features. | Operational baseline and current driver/query semantics. | Retain during evaluation; Community does not satisfy every enterprise availability requirement. |
| LadybugDB | MIT; active Kuzu successor; release v0.21.2 dated October 1, 2026. Explicit transactions and multiple connections. | Most direct embedded candidate, but inherited dialect/schema differences and process ownership matter. | First embedded proof of concept. |
| Apache AGE | Apache-2.0 PostgreSQL graph extension; SQL/openCypher interface using PostgreSQL transactions. | Potentially combines graph, vectors and relational metadata in one database service. Requires a new SQL adapter, result decoding and query/schema translation. | First server consolidation proof of concept. |
| ArcadeDB | Apache-2.0 multimodel engine advertising Cypher, graph and vector support. | Another server alternative if AGE cannot satisfy traversal requirements; compatibility claims need replay. | Reserve candidate to avoid three simultaneous migrations. |
| FalkorDB | Repository identifies SSPLv1 license. | Source-available licensing needs separate consideration if strict open-source distribution is required. | Outside the initial permissive/open-source shortlist. |

Sources: [Neo4j editions](https://neo4j.com/docs/operations-manual/current/introduction/),
[Neo4j license/features](https://neo4j.com/pricing/),
[Ladybug repository](https://github.com/LadybugDB/ladybug),
[Ladybug releases](https://github.com/LadybugDB/ladybug/releases),
[AGE overview](https://age.apache.org/overview/),
[AGE license](https://github.com/apache/age/blob/master/LICENSE),
[ArcadeDB repository](https://github.com/ArcadeData/arcadedb),
[FalkorDB repository](https://github.com/FalkorDB/FalkorDB).

Ladybug changes the earlier conclusion that an archived Kuzu leaves us without
an obvious embedded successor. It does not make our current wrapper correct.
Our `execute_write` simply calls the callback, with no transaction boundary.
The failed rollback test therefore demonstrates an adapter defect, not a lack of
engine rollback support. Its documented API has BEGIN, COMMIT and ROLLBACK, with
multiple readers and one writer. [Transactions](https://docs.ladybugdb.com/cypher/transaction/).

Ladybug permits one read-write Database object, with separate Connections from
that object. Multiple independent database objects cannot safely access the same
file while a writer exists. Therefore the HTTP server, STDIO entrypoint and
indexing subprocess cannot each independently open one mutable embedded database.
A local profile needs a single owning daemon that also runs indexing, or immutable
read-only snapshots with coordinated publication. Separate connection ownership,
a bounded writer queue and cancellation cleanup are required; thread offloading
alone is insufficient. [Concurrency](https://docs.ladybugdb.com/concurrency/).

AGE invokes Cypher through `ag_catalog.cypher()`, returning declared SQL records
with `agtype` values; parameter maps require prepared statements. This is not a
Bolt-compatible driver substitution. Its current development README lists PG17
support, but release branches and migration notes must be matched to the exact
PostgreSQL version. Build and test AGE plus pgvector in the same disposable image
before proposing consolidation. [Cypher interface](https://age.apache.org/age-manual/master/intro/cypher.html),
[installation](https://github.com/apache/age), [releases](https://github.com/apache/age/releases).

## Retrieval and cache choices

**Keep PostgreSQL/pgvector for the server profile.** pgvector explicitly documents
combining vectors with PostgreSQL FTS and using reciprocal-rank fusion or a
cross-encoder. Native FTS ranking is not automatically BM25. Our code-aware
lexical rules, graph rescue, project filtering, deduplication and ranking policy
remain application behavior and need parity checks. Compare approximate indexes
against exact filtered search before selecting HNSW/IVFFlat settings.
[pgvector hybrid search](https://github.com/pgvector/pgvector#hybrid-search),
[PostgreSQL FTS](https://www.postgresql.org/docs/current/textsearch.html).

**Evaluate LanceDB for the local profile.** The OSS project is Apache-2.0 and
supports embedded retrieval. Current docs provide native FTS and hybrid search;
legacy Tantivy parameters are not interchangeable with the native implementation.
Test the exact pinned Python version, project prefilters, updates, deletions,
reopen behavior and fusion ordering. Passing standalone search tests does not
establish replacement of `memory/store.py`, provenance or job metadata.
[LanceDB repository](https://github.com/lancedb/lancedb),
[native FTS](https://docs.lancedb.com/search/full-text-search),
[hybrid search](https://docs.lancedb.com/search/hybrid-search).

**Prefer Valkey when retaining a separate cache.** It is BSD-3-Clause and supports
RESP and existing Redis clients. Upstream warns that newer Redis persistence
formats are not universally compatible; verify the actual source version rather
than moving a volume blindly. Our application uses `redis.asyncio` with keys,
TTLs, lists and summaries, making a scoped compatibility check feasible. Keep
cache loss recoverable and durable jobs in transactional storage.
[Valkey repository](https://github.com/valkey-io/valkey),
[migration documentation](https://valkey.io/topics/migration/).

Qdrant is a credible Apache-2.0 vector-server alternative, but adding it now would
introduce another service and synchronization boundary without a measured gap in
pgvector. Keep it as a scale-driven option. [Repository](https://github.com/qdrant/qdrant).

## Deployment software and responsibilities

| Role | Software | Required work |
| --- | --- | --- |
| Runtime | Existing FastAPI/Uvicorn/MCP, Linux, Compose for one host | Split core requirements from crawling/browser/local-model extras; remove compiler tooling from final image; pin tested images/digests; test arm64 and amd64. |
| HTTP edge | Caddy, conditional on remote HTTP | TLS, explicit authentication at application boundary, MCP-compatible streaming/timeouts, request limits. HTTPS alone is not authorization. |
| Cache | Optional Valkey | Test used commands, TTLs and outages; no authoritative source/index/job state only in cache. |
| PostgreSQL recovery | pgBackRest | WAL archiving, retention, encrypted backup destination and restore drill; configure explicit RPO/RTO. |
| Observability | Existing OpenTelemetry SDK + Collector | Tool latency, index age, failed jobs, query fallback, memory and storage metrics; exclude source bodies and credentials from default telemetry. |
| Operator dashboards | Optional Prometheus/Grafana | Add when collecting metrics that drive operational decisions; avoid making monitoring mandatory for local startup. |

[Caddy](https://github.com/caddyserver/caddy) provides automatic HTTPS;
[pgBackRest](https://pgbackrest.org/user-guide.html) documents backups and point-in-time
recovery; [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector)
provides a telemetry pipeline. These are selected components, not configured integrations.
Compose is sufficient for the current single-host appliance; Kubernetes and a
separate vector server need demonstrated deployment/scale requirements first.
Inference-provider selection stays an independent optional concern.

## Work that software selection cannot solve

1. Capture content hashes from the same bytes used by the native parser. Store
   run identity, parser version and embedding model/dimension alongside evidence.
2. Define backend-neutral operations at meaningful boundaries: file/symbol reads,
   traversals, batch writes, schema setup, snapshot promotion and cleanup. Avoid
   silently swallowing unsupported Cypher or pretending SHOW queries succeeded.
3. Publish a complete run only after structural and semantic phases agree. With
   separate engines, there is no assumed cross-database transaction: persist a
   run manifest and a commit marker, serve the last complete run, and make retries
   idempotent. Distinguish current disk source from indexed snapshot evidence.
4. Specify timeout, cancellation, rollback, reader visibility, retry classification
   and connection ownership. A Neo4j-shaped facade must preserve semantics as
   well as method names. Test failure midway through a multi-statement write.
5. Add service readiness separate from process liveness, durable indexing status,
   graph outage handling, reconnect tests and a documented restore/export route.
6. Define supported repository sizes and memory budgets before picking an engine.
   Measure p50/p95 query latency, peak RSS, indexing throughput, disk size, retrieval
   correctness and actual agent token/time outcomes on the same fixtures.

## Concrete evaluation sequence

**A. Capture the real workload.** Inventory structural/indexing and tool queries,
including the external ts-pack parser/indexer (not only Python query strings).
Fixtures must cover label predicates, MERGE/upserts, variable-length paths,
OPTIONAL MATCH, relationship properties, ordering, constraints and run promotion.
Record baseline results with Neo4j/Postgres, including source provenance.

**B. Ladybug bounded proof.** In an isolated pinned environment and disposable
files, replay the previously failing describe-file query, implement true rollback,
use separate connections and test concurrent readers/writer. Test process ownership,
reopen/crash recovery, deletion and schema evolution. Stop if correctness or memory
budgets fail. Do not migrate operational data during this experiment.

**C. AGE contingency proof, only if required for shared-server goals.** Build AGE plus pgvector for the chosen PG17 release pair,
implement explicit SQL/agtype conversion, and replay the same graph corpus. Test
transactional promotion, PostgreSQL permissions, graph/project isolation, explain
plans and restore of the combined store. AGE support for PostgreSQL does not by
itself establish security or performance parity with our queries.

**D. Decide profiles independently.** A local winner need not be a server winner.
Require passing contracts plus acceptable measured resource use; document every
translated query and unsupported capability. Run existing CI/retrieval gates only
when a candidate is wired into a disposable end-to-end indexing path.

**E. Verify user value.** Repeat counterbalanced unfamiliar-repository agent tasks
with the compact catalog, native fallbacks counted and independent grading.
Promote a backend only when correctness is preserved and deployment/resource
benefits are measured; no speed claim follows from vendor benchmarks.

Repository evidence: [October 3 report](../benchmarks/reports/2026-10-03/README.md),
[embedded failures](../benchmarks/reports/2026-10-03/embedded-feasibility.json),
[implementation plan](enterprise-tooling-plan.md),
[current Compose](../docker-compose.yml),
[current embedded wrapper](../memory/embedded_kuzu.py).

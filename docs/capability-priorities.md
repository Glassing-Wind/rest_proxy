# FIRE capability priorities — October 11, 2026

Basis: local source inspection at `8bc9bdf2bcd147accc4af1661afce5b258c0a9f6`, with
preexisting local changes preserved; saved Codex Cloud settings and historical setup
chat inspected. These are recommendations, not new implementation or benchmark results.

## Decisions

| Capability | Current evidence | Useful next use / acceptance |
| --- | --- | --- |
| Codex Cloud | A private `rest_proxy` environment for `Glassing-Wind/rest_proxy` is saved. Python 3.14.7, CI requirements and the pinned parser are configured. Earlier onboarding reports 801 tests passed and 11 skipped. | Run independent offline/Linux checks while the local lead investigates or prepares the report. Verify exact revision and runtime first; retain new logs and skips. No measured speedup follows from configuration. |
| Cloud Environment plugin | The runtime skill and an earlier cloud chat's environment-status call establish the intended runtime inspection route. This local chat does not expose that status tool. | Use it inside the attached cloud task for runtime, credential-readiness and network-policy checks. Adding/selecting a plugin does not move a local task automatically. Avoid duplicate environments. |
| Apache Spark | No Spark/PySpark dependency or executable integration found in the searched repository. The architecture study proposes downstream joins, windows and backfills. | Trial against a representative dataset when current processing misses an agreed throughput, memory or latency target. Compare end-to-end setup, compute, correctness and operating cost before adoption. |
| Pillow | Pinned in requirements/CI; no direct PIL/Pillow calls found in repository Python. | Image thumbnails, resizing/cropping, format conversion and report evidence sheets when image input/output is needed. Image text/meaning requires a separate OCR/vision component. Preserve original bytes/hash and identify derived images. |
| Existing FIRE relationship tools | Embedded symbol context, related files, import bindings, route overview and call chains are implemented. | Demonstrate one change-impact question with source citations, static-coverage limits and feasible regression checks. This can broaden the sample using existing code. |

Codex Cloud has isolated task checkouts; local parallel changes can use worktrees.
Keep Mac Shortcuts and local model services on the Mac. The saved cloud setup excludes
live inference, external databases and optional embedded workflows; those require
their own dependencies/access and acceptance before being included in a cloud result.
This describes the saved cloud setup, not an external-server requirement for the
embedded product. LadybugDB/LanceDB repository tools run without Neo4j/Postgres/Redis
servers; current real-vector operations still need a configured embedding provider.
See [the two configuration profiles](configuration.md).

## Node runtime

The inspected Mac uses Node 22.22.0 through nvm. Node 22 is still an LTS line;
its verified current maintenance release is 22.23.3. A maintenance update is a
reasonable first step. Node 24 LTS can be tested per JavaScript/TypeScript project,
while retaining Node 22 and checking version-specific global CLI availability.
No Node installation or upgrade was performed during this documentation inspection.

The repository application, MCP server and parser build use Python/Rust, with no
root Node package/runtime requirement found. Node remains useful for JavaScript/
TypeScript customer builds/tests and developer CLIs. Updating it does not itself
enable a new FIRE storage or retrieval capability or demonstrate a speedup.
See [Node release support](https://nodejs.org/en/about/previous-releases) and the
[Node 22 maintenance release](https://nodejs.org/en/download/archive/v22.23.3).

Follow-up implementation adds bounded common search/health routes for embedded
storage. Text search stays model-free and health checks only listed current hashes;
full ranking/deep-audit parity and standard indexing remain incomplete. See
[the acceptance record](../benchmarks/reports/2026-10-11/embedded-tool-routing.md).

## Machine-readable data before distributed processing

An explicit schema, stable record identity, source/revision/hash, scope and completeness
state make evidence machine-readable. Spark provides structured/distributed processing;
it does not supply those application contracts automatically.

The existing [gateway event validator](../gateway/events.py), lines 16–40, validates
bounded exact-field JSON and hashes (8 KiB payload / 10 KiB event). It does not persist
events, authenticate scope or ingest massive datasets. The
[Arrow/LanceDB chunk store](../memory/embedded_lance_runs.py), lines 24–48, 101–129,
stores/retrieves repository chunks. An arbitrary dataset importer remains additional
work, even when the file format is readable by a storage library.

A possible later dataset route is authorized source -> normalized schema-preserving
records -> optional Spark processing -> provenance-preserving FIRE ingestion ->
retrieval -> source-checked answer. It is a design option, not an implemented pipeline.
Size alone is insufficient: benchmark the actual joins, windows, backfills or ingestion
workload. A single cloud development container does not demonstrate a Spark cluster.

## Valuable next delivery

Extend the next audit with one question such as: “If we change this publication
function, which readers and tests are affected?” Use the implemented
[embedded relationship surface](../graphrag_core/indexing/embedded_repository.py),
lines 182–221 and 264–280, then verify candidates against current source. Static
relationships may miss dynamic behavior; apply the
[coverage limits](embedded-static-relationships.md). The publication sample remains
an older revision-bound acceptance, not proof for a new relationship investigation.

Improve repeatability with a fixed intake brief, revision/freshness checks, exact
evidence receipts and one lead integrating bounded parallel reviews. Reuse report
and fixture methods. Measure total elapsed time, usage, setup and correction cost on
equivalent questions before claiming a faster workflow or commercial advantage.

Authored-document retrieval also exists in
[docs pipeline](../tools/brain/docs/pipeline.py), lines 120–179, but needs configured
Postgres/embeddings. Demonstrate it when a customer question requires code plus
documentation; do not present it as an offline universal dataset importer.

Agree source scope, processing location/providers and retention/deletion before
customer data goes to cloud/model tools. Use read-only access and disposable fixtures;
exclude credentials/unrelated data. Keep the first sale bounded while recording the
input types and processing bottlenecks that would justify later adapters.

## Primary references

- [Codex cloud environment setup](https://learn.chatgpt.com/docs/environments/cloud-environment).
- [Spark SQL/DataFrames](https://spark.apache.org/docs/latest/sql-programming-guide).
- [Spark Structured Streaming](https://spark.apache.org/docs/latest/streaming/getting-started.html).
- [Pillow image operations](https://pillow.readthedocs.io/en/stable/handbook/overview.html).
- [Earlier gateway direction](durable-knowledge-gateway.md) and [architecture study](research/rest-proxy-architecture-study.md).

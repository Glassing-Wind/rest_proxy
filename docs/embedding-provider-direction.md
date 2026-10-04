# Embedding provider direction — October 4, 2026

**Keep embeddings pluggable. LM Studio is an optional adapter, not a requirement
of the native embedded distribution.** No provider or model has won a controlled
comparison, and this decision does not change the optional inference proxy.

The combined Ladybug/LanceDB owner already accepts an explicit embedding callback
and encoder identity. This boundary lets us compare runtimes without coupling
storage, FIRE continuity or context assembly to a model server.

## Candidates and intended roles

| Candidate | Role to evaluate | Constraints and acceptance |
| --- | --- | --- |
| LM Studio | Existing development adapter and first real-model baseline | Separate local service. Supports embeddings and headless llmster deployment. Keep optional; measure operational and resource costs. |
| FastEmbed / ONNX Runtime | In-process candidate for native embedding generation | No separate embedding server needed. FastEmbed is Apache-2.0; audit its exact dependencies and selected model artifacts separately. Verify code retrieval and Python 3.14/platform installation before adoption. |
| Hugging Face Text Embeddings Inference (TEI) | Optional dedicated embedding service for team deployments | Explicit model/hardware compatibility. Native Metal execution is documented; Docker is an available deployment route, not our product requirement. Benchmark service behavior and installation. |

These are roles to evaluate, not defaults or claims that one runtime is faster.
FastEmbed does not require adoption of Qdrant: our storage remains Ladybug/LanceDB.
Runtime selection and embedding-model selection are separate decisions. A model
supported by LM Studio is not automatically available with identical behavior
in ONNX or TEI.

Primary references: [LM Studio developer interface](https://lmstudio.ai/docs/developer),
[LM Studio app, llmster and lms](https://lmstudio.ai/docs/app/basics/lmstudio-vs-llmster-vs-lms),
[FastEmbed repository and license](https://github.com/qdrant/fastembed),
[FastEmbed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/),
[TEI supported models/hardware](https://huggingface.co/docs/text-embeddings-inference/en/supported_models),
[TEI native Metal execution](https://huggingface.co/docs/text-embeddings-inference/local_metal).

## Current evidence

A read-only check of the configured loopback LM Studio endpoint found
`text-embedding-jina-embeddings-v2-base-code` loaded as an embedding model.
No model was loaded/unloaded or downloaded by that check. It is a convenient
first real-model baseline, not evidence that it is the best model or runtime.

The existing provider uses native LM Studio lifecycle calls and `/v1/embeddings`.
The application still depends on that provider for its current embedding service;
provider independence is implemented at the experimental repository-owner boundary,
not throughout the existing indexing pipeline.

[Embedded run acceptance](embedded-run-publication.md) used synthetic 3D vectors
and proves storage/publication behavior only. Real-provider embedded indexing,
semantic quality, FastEmbed/TEI compatibility and a provider default remain unverified.
This update adds no runtime dependency, downloads no model and changes no
interpreter.

## Evaluation and adoption gates

1. Index a fixed source snapshot with the existing loaded LM Studio model through
   the combined owner; validate dimensions, finite/nonzero vectors, published
   run identities, citations, restart and failed-provider preservation.
2. Define source-reviewed coding investigations covering natural-language discovery,
   exact symbols, callers and provenance. Hold chunking, corpus, graph evidence,
   lexical fusion, limits and query set fixed. Report native fallbacks.
3. Separate comparisons: use the same model/preprocessing where feasible to isolate
   runtime differences; use distinct model trials to assess retrieval quality.
   When exact parity is impossible, report the configuration difference explicitly.
4. Record cold/warm startup, indexing wall time, peak memory, disk footprint,
   p50/p95 query latency, context limits, truncation and errors. Record retrieval
   accuracy with reviewed expected evidence; do not equate engine smoke success
   with better coding outcomes or token savings.
5. Verify clean Python 3.14 installs and runtime execution on each supported platform,
   offline operation after assets are cached, pinned model revisions/artifact hashes,
   notices and the exact shipped license/dependency set.
6. Select a default only after those measurements. Retain optional adapters when
   they have a measured deployment role; keep the minimal native install independent
   of an LM Studio or TEI service.

Encoder identity must cover model revision/artifact, dimension, tokenizer,
query/document instructions or prefixes, pooling, normalization and input
truncation policy. Query and indexed vectors must use a compatible identity.
Equal dimensions alone do not establish compatibility. Changing the encoder
requires publishing a newly embedded run; never mix vector spaces silently.
The current owner accepts a caller-provided identity string; automatic immutable
model/configuration identity capture remains integration work.

The existing LM Studio provider can truncate inputs to its context budget.
Acceptance must record the transformed inputs or reject unexpected truncation;
the stored source/chunk hash alone does not prove every character was embedded.

See [integrated platform plan](integrated-platform-plan.md),
[hybrid stack research](hybrid-stack-research.md) and
[current roadmap](next-session-roadmap.md).

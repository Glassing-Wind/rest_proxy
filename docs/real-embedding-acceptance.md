# Real embedding acceptance — October 4, 2026

Priority 2 now has a real-model functional baseline through the combined embedded
owner. The opt-in strict LM Studio adapter used the **already loaded Jina v2 base
code model**, without downloading, loading, unloading or changing inference setup.
LM Studio remains optional; no provider default is selected by this result.

## Implementation

`StrictLMStudioEncoder` uses a loopback endpoint and disables automatic model
loading. It probes dimensions, requires one reported loaded embedding instance,
checks its reported identity/configuration before and after batches, and rejects
provider input normalization that would truncate text. Vectors must have the
expected dimension and finite/nonzero norms. Encoder configuration changes are
refused during a session. There is no synthetic-vector fallback.

The shared LM Studio provider now requires response indices to cover every input
exactly once, then restores input order. Missing, duplicate, boolean and out-of-range
indices refuse the response instead of associating vectors with the wrong evidence.
Legacy context clipping remains available to existing callers; strict indexing
rejects clipping explicitly.

The owner can persist bounded encoder metadata alongside the encoder identity,
dimension, chunk count and publication run. The strict descriptor records local
artifact SHA256, model identifier, loaded configuration, input policy and vector
normalization. Query and document prefixes are empty in this baseline. Tokenizer
and pooling are artifact/runtime-defined; runtime version and resident-weight
binding are explicitly **not attested**.

## Executed baseline

A frozen manifest of **131 production Python files** from this repository produced
**1,629 chunks / 768-dimensional embeddings**. The model was already warm.
Indexing took approximately **23.2 seconds**, including parsing/chunking, embedding,
vector staging/FTS and graph publication. This excludes artifact hashing, initial
model/probe setup and query evaluation. It is one local operational observation,
not a controlled performance comparison; concurrent machine activity was not isolated.

A macOS process policy denied network access except localhost port 1234 for the
embedding service. A separate check confirmed that a connection to the Neo4j
port was denied while the model-list endpoint remained reachable. The existing
Neo4j/Postgres services were not stopped; the tested owner used local Ladybug and
LanceDB storage.

After closing/reopening the owner, five natural-language probes used the same real
encoder for queries. All returned citations matched published source hashes and
run IDs. Expected-file coverage within the top five was:

| Probe | Vector | Hybrid |
| --- | --- | --- |
| Staged vectors and atomic publication | Present | Present |
| Finite/nonzero cosine validation and normalization | Absent | Present |
| Dead/local writer recovery | Present | Present |
| Bounded source lines and SHA256 | Present | Present |
| LM Studio readiness and batching | Present | Present |

Vector-only coverage was **4/5**, hybrid **5/5**. The missed vector target was
`memory/embedded_lance_runs.py`; retrieved alternatives included the strict
encoder and other search helpers. Expected-file coverage is a small source-anchor
pilot, not independent correctness grading, a coding-task evaluation or a comparison
with another embedding model/runtime. Do not claim provider superiority from it.

## Artifact identity limitation

The local GGUF contains **322,997,920 bytes**; CLI/API metadata reports
**322,999,136 bytes**, a 1,216-byte discrepancy. Its cause is not established.
The local artifact hash is recorded, but neither that hash nor the serving API
proves which bytes are resident in the running model. Reported instance/configuration
checks are not cryptographic weight attestation. Runtime version is also unverified.
This remains a production identity/reproducibility gate, not a reason to silently
reload a shared model or treat model metadata as ground truth.

## Verification and reproduction

Nine offline provider regressions and four strict-adapter tests passed and are
now included in gated CI. Four native combined-owner tests passed, including
persisted encoder metadata, stage/publication failures and crash recovery.
Full local CI passed. Optional engine tests remain separate from hosted CI gates.
[Receipt and source-anchor results](../benchmarks/reports/2026-10-04/real-embedding-acceptance.json).
Private frozen source, database and raw records are under
`.runtime/real-embedding-acceptance/` (mode 700).

The optional CLI uses the existing environment configuration and an explicit local
artifact fingerprint:

```sh
.venv/bin/python scripts/index_embedded_lmstudio.py /absolute/repository \
  --project-id proof --manifest /absolute/paths.json \
  --state /absolute/private-state --model-artifact /absolute/model.gguf
```

The manifest is a JSON array of normalized relative source paths defining complete
project replacement. The configured embedding model must already be loaded.
The artifact argument fingerprints a local file; it does not attest resident
weights. Use a separate state directory from synthetic-vector fixtures because
vector dimensions must match the stored schema. No packages or model assets are
installed by the CLI.

Next Priority 2 gates: complete graph/query compatibility, MCP/REST owner dispatch,
application metadata integration and robust model/runtime identity binding.
Controlled provider/model comparisons, clean native installs and restore/resource
validation remain acceptance work. FIRE checkpoints and whole-request budgeting
remain later priorities. See [provider direction](embedding-provider-direction.md)
and [combined publication](embedded-run-publication.md).

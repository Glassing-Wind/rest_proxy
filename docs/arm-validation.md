# Native ARM validation and measurements

The local code-intelligence path runs native ARM binaries. This is separate from
proving GPU/Metal/ANE use or an ARM performance advantage. The October 11
[disposable receipt](../benchmarks/reports/2026-10-11/architecture-arm/native-arm-benchmark.json)
records an Apple M3 Max, an ARM64 CPython 3.14.8 process, and ARM64 principal
extensions for ts-pack 1.20.0, Ladybug 0.21.2, LanceDB 0.39.0 and PyArrow 25.0.1.
Rosetta translation reported zero. The receipt identifies exact extension bytes;
it does not attest every transitive library or the dependency's complete build.

## CPU thread control

`LM_PROXY_LADYBUG_THREADS` sets the embedded graph engine's maximum worker count.
The default remains **2**. Accepted settings are ASCII decimal integers from **1
through 64**, with surrounding whitespace allowed. Empty/unset uses the default;
invalid settings raise `ValueError` before the driver opens database state.

For an isolated owner, use `EmbeddedRepositoryOwner(path, dimension,
graph_threads=4)`; the low-level driver accepts `max_num_threads=4`. An explicit
integer override takes precedence over the environment. This configures Ladybug,
not the parser's thread pool, LanceDB workers, inference GPU, or Apple performance
core affinity. Changes apply when constructing a new owner; they do not resize an
existing database. Driver operations retain their existing owner serialization.

## Repeatable model-free benchmark

Run with the existing project environment and an already cached Python grammar:

```sh
.venv/bin/python scripts/benchmark_embedded_arm.py \
  --files 12 --threads 2,4,8 --trials 2 --queries 10 --warm-reindexes 2 \
  --output /tmp/rest-proxy-arm-benchmark.json
```

The script creates a deterministic public Python fixture and an explicit manifest
inside temporary storage. Each thread setting/trial runs in a fresh process with
a new Ladybug/LanceDB owner. Native ts-pack parsing/chunking feeds the application's
actual vector staging, full-text indexing, graph publication and scoped text
queries. It verifies retrieved source hashes/current run IDs, replacement chunk
counts and durable reopen. Temporary database state is removed when the trial ends.
It does not index an existing workspace or alter environment/thread settings.

For a representative public repository slice, supply an explicit JSON array of
relative `.py` paths and a text query known to occur in that slice:

```sh
.venv/bin/python scripts/benchmark_embedded_arm.py \
  --source-root /path/to/authorized/public/repository \
  --manifest /tmp/explicit-python-files.json --text-query publication \
  --threads 2,4,8 --trials 3 --output /tmp/public-source-arm-benchmark.json
```

This mode reads only the manifest's 1–100 unique paths, rejects symlinks,
traversal/binary files, and caps input at 64 KiB per file and 4 MiB total. The parent
freezes source bytes once and passes the same copy to every worker, so later edits
to the original repository cannot change the compared corpus. Workers parse and
publish copies inside their temporary state. Receipts retain paths and hashes,
not source text. A failed worker retains the partial receipt, exit/timeout status
and a stderr hash; a successful result is marked `passed`.

Synthetic SHA256 vectors fill the required storage contract in the default path.
They measure publication/storage mechanics and are **not semantic embeddings**.
No inference provider is called, and no model is loaded. A missing cached Python
grammar fails with a diagnostic instead of initiating parser download from the
benchmark. Prefetch required grammars separately during an authorized setup.

The receipt separates import/preflight, first owner open, first publication,
first text query, repeated publication/text queries and durable reopen. It also
records state footprint and peak process RSS. “Cold” means a fresh process and
empty database; OS/disk/parser caches are not cleared. Thread order rotates across
trials, but concurrent machine activity is not isolated. Small sample p95 values
are descriptive and are not service SLO estimates. Larger representative corpora,
more repeats and controlled workload conditions are required for tuning decisions.

On macOS, the executed October 11 run used an OS policy denying networking:

```sh
sandbox-exec -p '(version 1) (allow default) (deny network*)' \
  .venv/bin/python scripts/benchmark_embedded_arm.py \
  --files 12 --trials 2 --queries 10 --warm-reindexes 2 \
  --output /tmp/rest-proxy-arm-benchmark.json
```

This policy blocks networking; it is not a filesystem sandbox. State remains
disposable by construction. Local fixture suites ran concurrently with this
observation, reinforcing the receipt's explicit lack of machine isolation.

## Optional real embedding observation

Real embedding runs are a separate explicit choice. `--encoder lmstudio` requires
`--artifact-sha256` containing the local model artifact's lowercase SHA256. The
strict adapter accepts only a loopback endpoint and one already loaded embedding
model; it never loads/unloads a model. It checks dimension, finite/nonzero vectors,
unchanged loaded configuration and input truncation policy. For example, after
separately verifying the model and fingerprint:

```sh
.venv/bin/python scripts/benchmark_embedded_arm.py \
  --encoder lmstudio --artifact-sha256 <verified-64-character-sha256> \
  --output /tmp/rest-proxy-real-embedding-benchmark.json
```

The real path records encoder setup separately and includes embedding service
time in publication measurements. It retains the strict adapter's limitations:
resident model artifact binding and inference runtime version are not attested.
Neither hardware identity nor a successful HTTP embedding request proves Metal,
MLX, GPU offload, MPS or ANE execution. No optional real embedding run was executed
for this change.

## Runtime and CI evidence

`scripts/runtime_hardware.py --output /tmp/runtime.json` records safe hardware
fields, process/executable ISA, package versions, installed wheel metadata/tags and
one principal extension SHA256 per native package. `--wheel /path/to/artifact.whl`
adds an exact built wheel hash. It reads no environment dump or model state;
GPU/SIMD/ANE fields explicitly remain unprobed/unmeasured.

The `embedded-native-arm` application CI matrix selects `macos-15` and
`ubuntu-24.04-arm`, documented ARM64 labels in the
[GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Each job asserts its process architecture, builds the pinned ts-pack fork on that
runner, installs core/embedded requirements and pinned PyArrow, prefetches fixture
grammars, executes native lifecycle/publication tests and the
model-free benchmark, and uploads an OS/architecture-specific wheel and receipts.
No x86 wheel artifact is shared with these jobs. Binary-only engine installation
prevents an unnoticed source-build fallback; Ladybug's pinned
[release files](https://pypi.org/project/ladybug/0.21.2/#files) include Python 3.14
macOS ARM64 and Linux aarch64 wheels.

The workflow was syntax-checked locally, but its new remote jobs have not been
dispatched. Local installed-runtime acceptance does not establish a fresh minimal
profile install, Linux ARM application parity, release packaging or performance
superiority. Default thread settings remain unchanged until measured workloads
justify a change. See [current architecture](current-architecture.md) for the
deployment and acceptance boundary.

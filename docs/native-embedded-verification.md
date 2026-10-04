# Native embedded verification — October 3, 2026

**Docker is not required by the tested graph and retrieval engines.** Native
installation and disposable operations passed on this Mac. The complete
rest_proxy embedded backend is not yet implemented or verified.

## Actual native execution

A new isolated Python 3.11.15 environment installed binary wheels for Ladybug
0.21.2, LanceDB 0.39.0 and PyArrow 25.0.1, including their dependencies. No Docker
command, database server, source compilation or operational database was used by
the engine probe. `pip check` passed. Existing application dependencies were not
changed. Resolved requirements and installation logs remain under
`.runtime/native-embedded-verify/`.

Disposable fixtures passed:

- Graph traversal from a file through a function to its called function.
- Explicit transaction rollback following a deliberate application exception.
- Concurrent reads using two separate connections to one Database object.
- Graph persistence after closing and reopening the database.
- Native full-text retrieval and vector/text hybrid search with explicit RRF.
- Project-prefiltered retrieval, idempotent vector updates and project deletion.
- Vector persistence after reconnecting.

The original `describe_file` Cypher still failed on the Neo4j label predicate
`WHERE s:Function OR s:Class ...`. An explicit-label query returned the expected
symbol name, lines and signature. This establishes a bounded translation path,
not compatibility of the complete query corpus. Full cross-project scoping,
concurrent writes, cancellation and crash recovery remain untested.

The existing native ts-pack installation separately parsed a small Python file
and returned its two expected exported symbols. This uses the existing editable
build, not a clean ts-pack installation in the new environment, and does not
exercise the structural indexing writer.

## Platform package evidence

PyPI metadata for pinned CPython 3.11 wheels lists Linux x86_64, Linux ARM64 and
Windows x86_64 artifacts for all three engine packages. macOS ARM64 artifacts
also exist; Ladybug's wheel is tagged **macOS 15 or later**. Older Mac support
must not be promised from this test. Only this Mac was executed; other platform
wheel availability is not proof of a successful full installation or runtime.

## Remaining application work

1. Replace the experimental Kuzu wrapper with a pinned Ladybug integration,
   implementing transaction boundaries and per-session connections.
2. Translate and replay the actual tool/indexer query corpus and schema.
3. Route chunk, embedding and metadata operations through the embedded backend;
   the current memory/store path still uses PostgreSQL and the LanceDB adapter
   remains standalone.
4. Integrate ts-pack structural indexing with the embedded writer. Coordinate
   HTTP, STDIO and indexing through one mutable database owner; do not let
   independent subprocesses open the same mutable file.
5. Publish consistent graph/vector run identities, handle deletions, and test
   source provenance, reopen/recovery and optional service absence.
6. Create a minimal native installation manifest; verify clean ts-pack installation
   on each supported OS and audit the entire shipped dependency/license set.
7. Verify real indexing and MCP source/symbol/search requests with external service
   access explicitly disabled. Existing services were left untouched by this test.

Docker can remain an optional packaging and isolation mechanism for the same
engine. The current multi-database Compose deployment remains the tested server
baseline while native integration is built.

## Reproduce and evidence

Run the probe in an environment containing the three pinned engines:

```sh
python scripts/verify_native_embedded.py --output /tmp/native-embedded-verification.json
```

The probe records query compatibility separately from engine smoke success and
always reports application adoption as unverified. It uses temporary databases.
The first native run passed with the older deprecated FTS API. A follow-up using
an incorrect new-API column argument failed; the corrected explicit column API
passed. Both attempted logs are retained; the final artifact reflects the final
successful run.

[Engine results](../benchmarks/reports/2026-10-03/native-embedded-verification.json),
[wheel metadata](../benchmarks/reports/2026-10-03/native-wheel-availability.json),
[native parser check](../benchmarks/reports/2026-10-03/native-ts-pack-verification.json),
[reproducible probe](../scripts/verify_native_embedded.py).

API references: [Ladybug Python](https://docs.ladybugdb.com/client-apis/python/),
[LanceDB quickstart](https://docs.lancedb.com/quickstart/).

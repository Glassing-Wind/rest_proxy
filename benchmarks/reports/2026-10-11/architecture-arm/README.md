# ARM implementation acceptance — October 11, 2026

[Native benchmark receipt](native-arm-benchmark.json) uses the existing macOS ARM64
Python 3.14.8 environment with pinned native engines and the retained Python
grammar. Its source hashes identify the changed driver/owner and benchmark scripts;
this is working-tree acceptance, not a released binary or remote CI run.

Six fresh-process trials covered Ladybug thread limits 2, 4 and 8 twice each. Every
trial published the same 12 deterministic Python files, 48 symbols and 12 chunks,
then checked text-query source/run integrity, warm replacement and durable reopen.
Synthetic vectors were explicitly labeled and no inference/model API was called.
The macOS execution policy denied all networking. Temporary source/database state
was removed by the benchmark; existing operational state was not indexed or opened.

| Ladybug threads | First publication median | Warm publication median |
| --- | ---: | ---: |
| 2 | 112.9 ms | 88.7 ms |
| 4 | 102.4 ms | 91.0 ms |
| 8 | 109.4 ms | 95.5 ms |

The [explicit public-source receipt](public-source-native-arm-benchmark.json)
freezes `memory/embedded_ladybug.py`, `memory/embedded_lance_runs.py` and
`graphrag_core/indexing/embedded_repository.py` from the working tree based on
`8bc9bdf2bcd147accc4af1661afce5b258c0a9f6`. Each of its six fresh-process trials
published the same three files, 68 symbols and 28 chunks using copied source bytes,
and queried `publication`. It retained source hashes and passed the same integrity,
replacement and reopen checks. First/warm publication medians were 158.9/144.6 ms
at two workers, 160.7/146.5 ms at four, and 167.2/153.6 ms at eight. This is a
bounded real-source slice, not a representative whole-repository workload.

The [earlier built-in observation](initial-native-arm-benchmark.json) is retained
from before the manifest/failure-receipt additions. A deliberately absent lexical
query produced a nonzero exit and the [expected failure receipt](expected-failure-receipt.json),
verifying partial failure retention. That failure is an exercised benchmark
acceptance boundary, not a production incident.

These tiny-fixture observations do not justify selecting a winner or changing the
default of two workers. OS caches were not cleared and fixture tests ran
concurrently; this is not a controlled ARM-versus-x86/GPU speed comparison or a
coding outcome evaluation. Peak RSS and native extension/wheel metadata hashes are
in the receipt. GPU/SIMD/ANE execution remains unmeasured.

Focused local validation passed: runtime hardware 2 tests, explicit source corpus
2 tests, Ladybug configuration and transactions 7 tests, embedded outlines 4 tests,
embedded repository 23 tests, native common-tool bridges 5 tests (**43 total**),
and changed Python Ruff checks. YAML syntax/matrix validation passed.
The new macOS/Linux ARM CI jobs were not remotely dispatched and no dependency was
installed, service restarted, or model loaded for this acceptance.

Reproduction and limits: [ARM validation guide](../../../../docs/arm-validation.md).

## Architecture, deployment and proxy implementation

Added the [canonical architecture](../../../../docs/current-architecture.md), linked
it from the README/documentation index, and reconciled profile/runtime guidance.
GOOFY expands to **Graph-Oriented Operations For You** as a proposed assistant name;
FIRE remains the evidence core and existing package/API names are preserved.

Compose now selects the same Neo4j 5.26 minor baseline as CI and no longer installs
APOC or grants wildcard procedures. Unspecified Neo4j database selections default
to `neo4j` throughout indexing/bootstrap/recovery; explicitly selected existing
databases remain honored. No database was renamed and `.env` was not rewritten.
PostgreSQL `ts_rank` labels now say full-text relevance rather than BM25.

The proxy now uses one FastAPI lifespan for provider clients, optional memory
pools and supervisor PID cleanup. Clients reuse provider/timeout pools within the
application event loop; standalone helper calls remain scoped. Streams close their
upstream response on normal completion, rejection, cancellation and ASGI send
failure without closing shared clients. Cleanup is shielded against disconnect
cancellation; optional memory failure still permits inference requests. Ten new
offline lifecycle checks cover these contracts, including lazy streams and actual
ASGI lifespan requests. Existing adapter and persistence checks pass.

## Integrated verification

[Implementation receipt](implementation-validation.json) and
[full isolated CI log](isolated-ci.log) retain the verification. The complete
`scripts/run_ci_checks.sh` passed in a disposable current-source Git snapshot,
using the existing Python environment and an unused loopback port. It executed
100 unittest suites / **892 test executions, including 9 explicit skips**, plus
Ruff, graph-pipeline checks and MCP STDIO/protocol/restart/roots checks. These are
executions, not a count of distinct tests; some pipeline checks repeat suites.
The temporary server was stopped. Changed Python files match the tested snapshot.
The log retains existing SQLite fixture `ResourceWarning` messages.

The separate native acceptance ran Ladybug 7, outlines 4, repository 23 and common
tool bridges 5 checks (39 total). Runtime/corpus checks are also included in the
complete gate. Compose configuration validated with dummy credentials, and final
workflow YAML/matrix, Ruff, shell syntax and diff whitespace checks passed.
The final ARM install step enforces binary wheels for Ladybug/LanceDB/PyArrow.

Both the deterministic fixture and [frozen public-source sweep](public-source-native-arm-benchmark.json)
passed six fresh-process trials under denied networking. The latter used three
explicit public core files, 68 symbols and 28 chunks. The small observations do
not justify raising the default thread count. An intentional absent-query run
retains its [expected failure receipt](expected-failure-receipt.json); the earlier
fixture observation is retained separately rather than overwritten.

Existing services, indexes, models and unrelated working-tree edits were preserved.
No inference request, dependency reinstall, commit, push or hosted CI dispatch was
performed. Application readiness/authentication/OTel, operational backups and
complete embedded parity remain open; the Neo4j minor tag is still patch-mutable.

## Commit-boundary verification

The [staged-source receipt](staged-validation.json) records a second complete CI run
after excluding unrelated existing working-tree edits. Its disposable checkout
contains only the proposed architecture/ARM commit; no common search/health bridge
implementation or its new test was pulled into this change. The committed ARM job
checks native lifecycle/publication, while the earlier working-tree acceptance
above also exercised the separate common-tool fixtures.

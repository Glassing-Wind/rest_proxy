# RepoAnalyzer gateway acceptance — October 12, 2026

Rest-proxy now exposes optional RepoAnalyzer profiles and comparisons through MCP
and the existing REST evidence dispatcher. Both use one captured embedded publication
context and the same gateway. [Setup and contracts](../../../../docs/repository-learning.md)
describe the configuration, sampling, provenance and failure statuses.

The [isolated full gate](isolated-ci.log) passed **922 unittest test executions across
104 suite executions, including 9 skips**, plus Ruff, graph pipeline and MCP
STDIO/protocol/restart/roots checks. These are executions, not distinct tests.
The [gate summary](gate-summary.json) identifies the staged tree. The first
[harness attempt](harness-attempt-gate-summary.json) stopped at distribution acceptance
because the temporary snapshot lacked Git metadata; its [log](harness-attempt-isolated-ci.log)
is retained. Adding disposable Git metadata fixed the harness without application changes.

The new slice contributes 13 native snapshot/gateway tests, six real in-memory MCP
and ASGI transport tests, nine subprocess adapter tests and one full native/worker/
transport integration test. Both optional actual-checkout tests were enabled in the
local full gate. Ordinary CI explicitly skips them when no checkout is configured.
Native fixtures use real ts-pack/Ladybug/LanceDB and synthetic three-dimensional
vectors. Analysis then uses the real RepoAnalyzer indexed-payload APIs with network
and learning-runtime initialization refused. Deleting live source files does not
change the captured evidence. Returned JSON is equal across REST and MCP.

The final [packaging check](packaging.log) also requires the new worker/gateway/tool
files in wheel and source archives, compiles them and imports the installed stdlib
gateway/worker without optional dependencies. It passed against final staged source.
Production Python bytes match the full-gate tree; subsequent changes strengthen
the packaging test, preserve the CI script's executable mode and update documentation.
[Source hashes and validation](validation.json) identify that final source.

Independent read-only review found no remaining issue. Coverage includes source/fact/
declaration tampering, stale outline contracts, explicit sampling/budgets, source/run
scope isolation, annotation exclusion, forged citations, disabled/missing dependencies,
worker size/deadline/cancellation handling and POSIX descendant cleanup.

No operational daemon, index, model, `.env`, dependency environment or sibling analyzer
source was changed. The full gate started/stopped only its separate temporary loopback
daemon. Nothing was pushed or dispatched to hosted CI. Native platform acceptance
here is macOS ARM64 with the existing Python 3.14 environment, not a platform matrix.

This is the profile/comparison integration. It does not establish persistent pattern
learning, custom plaintext/adaptive parser integration, GithubAnalyzer reuse, shared
service authentication, model training, recommendation correctness or improved
coding outcomes. Earlier publications need an explicitly authorized reindex to add
declaration hashes before these learning reads can process them.

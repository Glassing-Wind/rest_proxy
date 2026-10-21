# Persistent repository learning acceptance — October 12, 2026

Rest-proxy now retains revisioned structural patterns and historical RepoAnalyzer
profiles/context, learns actual authored plaintext AST observations, and reuses
GithubAnalyzer's genuine requirements/gitignore components. Shared MCP and REST
services expose explicit mutations and bounded inspection/transfer suggestions.
[Setup and contracts](../../../../docs/repository-learning.md) describe the opt-in
private memory, backend roots and operation limits.

The final [isolated full gate](isolated-ci.log) passed **980 unittest
execution counts across 108 suite executions, including
9 skips**, plus Ruff, graph pipeline and MCP STDIO/protocol/restart/roots checks.
Counts describe executions rather than distinct tests. Both actual analyzer checkouts
were enabled; ordinary CI skips those checks when no checkout is configured.
[Gate identity](gate-summary.json) and [final source validation](validation.json)
record the exact staged source and component hashes. Production bytes match the
final gate. Subsequent files only record these receipts.

Acceptance includes 13 private SQLite tests, 14 native/persistent pattern tests,
9 configuration-learning tests, 7 Github component-worker checks, 17 RepoAnalyzer
adapter checks, 12 MCP/REST contract checks and 2 full native/real-worker/transport
checks. Native fixtures use real ts-pack, Ladybug and LanceDB with synthetic vectors.
Default profile/parser fixtures are explicitly labelled; enabled actual tests invoke
the genuine analyzer APIs/components with network/runtime/external-write guards.
The [focused mutation receipt](focused-mutation-integration.log) also exercises REST
writes, MCP idempotent reuse, retained-reference comparisons after graph deletion,
plaintext AST reads across both transports, and revision-fenced deletion.

Coverage includes source/outline/retained-context consistency, UTF-8 bytes/character
spans and point units, typed configuration fields, normalized occurrence/source counts,
repeated-artifact deduplication, explicit historical scope, CAS races, publication
changes during analysis, cancellation draining, private path/schema/hash rejection,
retained-profile identity/citation rebinding, source-label non-acquisition and size
limits. Stored reads and provided-text/configuration reuse need no live graph,
analyzer process or model. Recommendations remain candidates for review.

The first [gate attempt](packaging-fixture-attempt-isolated-ci.log) stopped at the
new installed-store check because its temporary `/var` path contained macOS's
symlink; the private-store guard refused it. Resolving that fixture path fixed the
test without changing the guard. [Focused packaging](packaging.log) passed afterward.
A [review-stage gate](review-stage-isolated-ci.log) also passed before final review
added retained-profile scope/citation checks. The final gate above includes that
repair and its [focused regression receipt](focused-retained-evidence.log).
Wheel/sdist acceptance includes the new store, providers, workers and tool wrapper;
installed stdlib imports do not initialize a learning directory.

Validation is local macOS ARM64/Python 3.14. Actual provided-text/configuration
originals are retained only in disposable private fixture state. No operational
configuration, existing daemon, indexed repository data, model, dependency environment or
sibling analyzer source was changed. The gate started/stopped only its separate
temporary loopback daemon, and its final listener was verified closed. Nothing was
pushed or dispatched to hosted CI.

This establishes bounded extraction, durable artifacts and historical reuse.
RepoAnalyzer's authored regex/node-factory slice excludes full parser/adaptive/AI
initialization; overlapping/DOTALL matches and unresolved relationship templates
remain explicit. Github's simple requirements/ignore declarations do not establish
PEP 508 coverage, dependency solving, effective ignore evaluation, repository
acquisition or its full legacy runtime. The trusted-code guards are not an OS
sandbox. Authentication, model training, recommendation correctness, controlled
coding-outcome improvements and an operational retention/backup policy remain
separate gates. This continues the [first gateway slice](../repository-learning/README.md).

# Embedded common-tool routing acceptance — October 11, 2026

Base revision: `8bc9bdf2bcd147accc4af1661afce5b258c0a9f6`, branch
`codex/task-provider-adapter`, plus uncommitted changes identified by file hashes
in [the acceptance receipt](embedded-tool-routing.json). This is new focused
implementation acceptance, separate from the original audit at
`d2ae74c4adc6233981b16d38b26ae290e023a47b`.

## Result

Common `search_codebase` and `get_indexing_health` now dispatch to the selected
LadybugDB/LanceDB owner before requesting legacy services. Supported embedded
text search and manifest freshness checks require neither an encoder nor
Neo4j/Postgres/Redis servers. The current operational daemon was not restarted
or switched; this record proves disposable fixture behavior in the changed source.

Search returns bounded JSON evidence with unchanged stored chunk bytes,
project/run/ref identity and full-file/chunk hashes. It resolves committed roots
and IDs, rejects ambiguity/publication changes, and preserves project isolation.
Same-named files across projects retain independent caps and identities. Native
rank is interleaved across projects; legacy ranking parity is explicitly false.
Unsupported policies return a diagnostic before opening engines or models.

Health reports the active publication separately from the latest indexing attempt.
It hashes only the explicit manifest and detects byte edits even when the mtime is
preserved. Missing files are stale; symlink/unreadable/budget/platform failures
are explicit unknown/incomplete checks. It does not enumerate unlisted files or
perform the service-backed deeper audit.

## Methods and validation

Existing `.venv`, CPython **3.14.8**, macOS ARM64. Installed packages: Ladybug
**0.21.2**, LanceDB **0.39.0**, PyArrow **25.0.1**, modified
tree-sitter-language-pack **1.20.0**, MCP **1.26.0**, Ruff **0.15.7**.
The retained Python grammar cache was reused; no dependency/model downloads or
fresh parser source-build attestation were performed.

All six suites ran in separate processes under macOS `sandbox-exec` with
`(deny network*)`. A loopback socket probe failed with `Operation not permitted`.
Runtime/config/temp paths were disposable, memory/watchers disabled, and native
fixtures additionally patched configuration loading and legacy/model calls to
fail if reached. The policy denies networking; it is not a filesystem sandbox.

| Suite | Final passing tests | Evidence |
| --- | ---: | --- |
| Embedded common search | 7 | Modes/identity, alias/missing scopes, filters/caps, receipt races, UTF-8/output bounds and dispatch before legacy storage. |
| Embedded health | 15 | Hash freshness, absent paths/roots, scope and symlink refusal, reader/budget limits, attempt/publication separation and unsupported deep audit. |
| Native common-tool bridges | 5 | Real engines, durable reopen, two-project scope, same-mtime edits, missing/symlink sources, corrupted stored chunk refusal and unsupported fallback. |
| Existing service-backed search | 15 | Service behavior/rendering regressions remain passing. |
| Existing service-backed health | 23 | Existing alignment/freshness regressions remain passing. |
| Existing embedded runtime | 14 | Existing owned runtime/transport dispatch contracts remain passing. |
| **Total** | **79** | **No skips reported in these six suites.** |

The first native attempt passed one method, then four failed because the fixture's
whole-module-table mock removed newly imported native extensions during cleanup.
Replacing it with restoration of only the three overridden aliases fixed test
isolation; all five native methods then passed. The failed log and first receipt
are retained alongside the final receipt. No production workaround was added.

Changed-code Ruff, tracked diff whitespace and shell syntax checks pass. Offline
search/health tests were added to the existing local gate; native bridge tests were
added to the existing hosted native job. Neither the complete daemon-changing gate
nor new hosted/cloud CI was run. Linux and unsupported-reader platform acceptance
remain unverified for this new bridge.

## Scope, security and remaining gates

Search accepts 1..8 workspaces, queries up to 8192 UTF-8 bytes, at most 100 native
candidates per project and 48 KiB JSON output. Filters/caps are applied after this
bounded retrieval; recall is incomplete, and an empty result is not exhaustive
absence evidence. Whole chunks can be omitted for output size. Indexed chunk
content is not a manufactured exact source-line excerpt.

`precise` aliases native hybrid; vector/hybrid queries still require the matching
local encoder. Legacy metadata boosting, implementation-intent expansion and
clone reranking are not applied. Broad/custom boost/debug/Cargo/grep-fallback
policies remain unsupported. Standard `index_workspace` is still guarded in
embedded mode; indexing uses the complete replacement manifest tool.

Health is bounded to 5000 files, 2 MiB/file, 64 MiB total and 50 issue details.
The safe reader requires POSIX directory descriptors and no-symlink open support;
unsupported platforms report unknown rather than follow unsafe paths. It is a
sequential comparison, not an atomic filesystem snapshot. Parser/stored-source/
vector/graph integrity, semantic quality and unlisted files are not audited.

The tools inherit the server's local filesystem privileges; project selection is
not authenticated tenant isolation. Keep endpoints on loopback until remote
authentication and project isolation are validated. Agree source/provider/retention
scope before customer processing, exclude credentials/unrelated data, and use
disposable state for writes. This acceptance used synthetic three-dimensional
vectors and fixed fixture source; it does not establish real-model quality,
production readiness, speed/token savings or a paired manual investigation outcome.

## Source locations

- `tools/brain/search/semantic.py:175`: embedded branch before legacy memory/encoder use.
- `memory/embedded_runtime.py:92`: owned workspace search; `:115`: owned health wrapper.
- `memory/embedded_search.py`: scope, native policy, filters/caps and bounded receipts.
- `tools/hands/indexing.py:1243`: embedded health dispatch before legacy path/pool setup.
- `memory/embedded_health.py`: safe bounded source hashing and publication/attempt receipt.
- `test_embedded_search.py`, `test_embedded_health.py`, `test_embedded_tool_bridges.py`:
  new focused regression and native fixture methods.

The downloadable completion report/evidence package retains logs, code patch,
source hashes, exact commands and final receipt. Existing user changes and the
original audit/evidence bytes were preserved. No Node upgrade, model/service
lifecycle change, live index mutation, Git commit/push or external message occurred.

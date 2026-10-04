# Integrated embedded context platform plan

Product name: **FIRE — Find, Integrate, Retrieve, Explain**. See
[the FIRE direction](fire-platform.md) for the first checkpoint/provenance slice
and compaction/resumption acceptance scenarios.

Updated October 3, 2026. This is the coordinating implementation plan; linked
research and evidence retain their detailed findings.

## Outcome

Deliver a native, permissively licensed code intelligence and persistent-context
platform for coding agents. LadybugDB provides graph storage, LanceDB provides
vector/lexical retrieval, and ts-pack supplies native parsing. The context engine
retrieves, verifies and concatenates selected evidence within a finite model
budget, preserving task continuity across sessions. MCP is a primary access path;
the REST inference proxy is optional and provider-independent. Docker is optional
packaging. Existing server backends remain supported during the transition.

## Decisions and evidence

- Embedded engine smoke tests passed natively on macOS ARM64 / Python 3.11.15.
  Ladybug 0.21.2, LanceDB 0.39.0 and PyArrow 25.0.1 installed from binary wheels.
- Original Neo4j label predicates fail in Ladybug; an explicit-label query works.
  The full query corpus, indexing writer and storage routing still need integration.
- Compact catalog, current-file outline and development appliance are implemented.
  Compact schemas measured 77.54% fewer tokens; paired outcomes showed no MCP
  performance superiority. Preserve that distinction in product claims.
- Permissive top-level engine licenses do not establish a GPL-free distribution.
  Audit the exact artifact, dependencies, native extensions, utilities and models.
- One process owns mutable embedded graph storage. Indexing must share that owner,
  rather than opening the same database independently in subprocesses.

## Implementation milestones

| Order | Deliverable | Acceptance gate |
| --- | --- | --- |
| 1 | Minimal native packaging and runtime matrix | Clean isolated install; native parser/engine imports; documented OS/Python support; reproducible dependency resolution. |
| 2 | Backend contracts and real query corpus | Capture tools plus ts-pack indexing operations; baseline fixtures; explicit supported capabilities and failure semantics. |
| 3 | Ladybug graph adapter and owned indexing | Correct schema/query translations; commit/rollback; separate connections; bounded writes; cancellation and reopen/crash checks. |
| 4 | LanceDB and durable metadata integration | Route real chunk writes/search/deletion; preserve project isolation, ranking and provenance; select metadata/checkpoint store explicitly. |
| 5 | Consistent run publication and freshness | Same-byte source hashes; run manifest; serve last complete graph/vector run; idempotent recovery and stale/deleted evidence handling. |
| 6 | Shared context bundle and budgeter | Deterministic selection/deduplication; whole-request token accounting; output reserve; citations, freshness and explicit omissions. |
| 7 | Persistent task continuity | Scoped checkpoints and original evidence references; resumption and corrected/superseded decisions; retention/export/deletion. |
| 8 | MCP and opt-in REST integration | Same bundle semantics; no duplicate injection; provider continuation handled; existing schemas and optional-failure behavior preserved. |
| 9 | Native end-to-end distribution | Index and investigate a real repository with external service access disabled; source/symbol/search tools work; installation and upgrade/recovery documented. |
| 10 | Quality and deployment acceptance | Existing CI/retrieval gates; repeated unfamiliar-repository tasks with independent grading; resource budgets, SBOM/notices and restore drill. |

Milestones 1–5 establish the storage foundation. Context contracts and budgeting
can be developed against current backends while that foundation is built; embedded
release acceptance depends on the same contracts passing on the new stores.

## Python decision

Observed local interpreters:

- Apple-controlled `/usr/bin/python3`: 3.9.6; leave it unchanged.
- Shell default `/opt/homebrew/bin/python3`: 3.14.4; separate Homebrew installation.
- Existing project `lmproxy` environment: 3.11.15; current CI and Docker use 3.11.
- Native engine verification environment: 3.11.15.

Keep 3.11 as the verified baseline during adapter work. Its upstream security
support runs through October 2027, so schedule a project-runtime migration before
that deadline. Evaluate standard CPython 3.14 in a separate environment first:
resolve the complete chosen dependency profile, install/build ts-pack, run native
engine probes, CI and retrieval gates, then compare indexing/retrieval behavior.
Do not infer full compatibility from `requires-python >=3.11` or engine wheels.
If 3.14 fails, document blockers and evaluate an intermediate supported version.
After passing, update project tooling, CI, packaging, launch scripts and optional
container runtime together, retaining the working 3.11 environment for rollback.
A Homebrew patch update is a separate maintenance task; no interpreter was changed
as part of this planning work.

Sources: [Python support lifecycle](https://devguide.python.org/versions/),
[Apple-controlled Python guidance](https://docs.python.org/3/using/mac.html).

## Distribution and operational requirements

Separate core/embedded dependencies from crawler/browser/local-model/server extras.
Confirm clean ts-pack packaging rather than depending on this machine's editable
checkout. Test macOS ARM64, Linux x86_64/ARM64 and Windows only as support is
explicitly added; Ladybug's tested macOS wheel requires macOS 15+. Published wheel
metadata does not establish cross-platform runtime success.

Specify repository-size and memory budgets, indexing throughput, p50/p95 query
latency, citations and fallback rates before promoting a backend. Preserve user
instruction authority when concatenating retrieved content. Archive evidence
outside the active prompt and keep summaries expandable to originals.

An optional container should package the native engine with one storage owner.
Shared remote access adds authentication, isolation, TLS and operational recovery
requirements; local engine success does not establish enterprise service readiness.

## Next concrete work

Python 3.14 installation and runtime cutover have passed the local checks recorded
below. Remaining packaging work includes a minimal embedded dependency profile,
supported-platform validation and a controlled upstream sync of our ts-pack fork
that preserves its modifications and validates native wheel loading.
The [ts-pack investigation](ts-pack-upgrade.md) records upstream v1.20.0's verified
API gaps, binding reorganization and upgrade acceptance gates.

In parallel with storage work, implement the FIRE evidence/checkpoint contract and
preserve provenance through retrieval. Inventory the actual graph/indexer query
corpus before replacing the experimental wrapper with a transaction-correct
Ladybug adapter. No migration of operational databases is part of these probes.

## Supporting documents

- [Original-plan correction and acceptance](enterprise-tooling-plan.md)
- [Software and licensing research](hybrid-stack-research.md)
- [Native verification](native-embedded-verification.md)
- [Context architecture and contracts](context-platform-direction.md)
- [October 3 measurements and grading](../benchmarks/reports/2026-10-03/README.md)
- [Session roadmap](next-session-roadmap.md)

## Python 3.14 verification update

October 3: full manifest installation, native engines, local CI and the complete
live retrieval-quality gate passed on standard CPython 3.14.4 / macOS ARM64.
The default ts-pack source build had a Mach-O load error also reproduced with
Python 3.11; rebuilding the same pinned revision with
`CARGO_PROFILE_RELEASE_STRIP=false` resolved it. Keep 3.11 operational while fixing
reproducible packaging and testing supported platforms. No runtime cutover occurred.
See [verification and retained attempts](../benchmarks/reports/2026-10-03/python314-README.md).

## Python 3.14 runtime cutover

The local project now selects the tested Python 3.14.4 `.venv`; the ordinary MCP
daemon and background jobs use it by default. Python 3.11 is retained for rollback.
CI and optional container configuration target 3.14. The new native setup command
builds and load-checks an unstripped pinned ts-pack wheel. Default-path local CI
passed after cutover. See [runtime setup and rollback](python314-runtime.md).

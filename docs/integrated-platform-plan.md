# Integrated embedded context platform plan

Product name: **FIRE — Find, Integrate, Retrieve, Explain**. See
[the FIRE direction](fire-platform.md) for the first checkpoint/provenance slice
and compaction/resumption acceptance scenarios.

Updated October 4, 2026. This is the coordinating implementation plan; linked
research and evidence retain their detailed findings.

For current delivery status rather than the original milestone sequence, consult
[the October 6 five-priority table](five-priority-status.md). It distinguishes
validated slices from incomplete end-to-end acceptance and supersedes earlier
pending-work statements in this plan.

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
- One process owns mutable embedded graph storage. The [embedded MCP owner](embedded-mcp-owner.md)
  shares indexing/retrieval ownership; [durable project discovery](embedded-project-discovery.md)
  bridges standard resolution/overview tools. [Parser facts](embedded-parser-facts.md)
  supply hashed import/call/route observations. [Static relationships](embedded-static-relationships.md)
  resolve a conservative subset with source citations; broader resolution remains incomplete.
  Full graph queries and remaining application
  metadata/REST integration are still acceptance gates.

## Embedding provider direction

Keep embedding generation behind a provider boundary. LM Studio remains an
optional development adapter and the first available real-model baseline; it is
not a requirement of the native embedded distribution. Evaluate FastEmbed/ONNX
for in-process generation and TEI for optional team serving. Select defaults from
measured code retrieval, resource use and installation reliability, with pinned
model/configuration identity and Python 3.14/platform acceptance. No candidate
runtime has been adopted or shown superior. The inference proxy remains separate.
See [embedding provider direction](embedding-provider-direction.md).

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

- [Evidence routing and framework evaluation](evidence-routing-framework-research.md)
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

### October 5, 2026 metadata checkpoint

[Project annotations](embedded-project-metadata.md) now persist in the embedded owner
with bounded JSON, revision/publication preconditions and atomic scoped deletion.
Annotations remain separate from repository evidence. Session/watch/job persistence,
FIRE checkpoint history and REST integration remain outstanding.

[Embedded indexing journal](embedded-indexing-journal.md) adds durable latest-attempt
metadata, atomic success and recovery without automatically resuming work.
IDE session and watch-intent migration remain separate integration tasks.

[Workspace activity](embedded-workspace-activity.md) now provides typed durable watch
intent and client leases. Legacy IDE registry migration, process liveness validation
and worker dispatch remain separate integration gates.

[IDE registrar/discovery](embedded-ide-registration.md) now uses the existing HTTP
MCP owner through explicit CLI commands. Lease expiry, ambiguity and published-root
matching are enforced. Automatic IDE refresh and watcher dispatch remain pending.

[Automatic IDE refresh and embedded watch dispatch](embedded-refresh-and-watching.md)
now renew leases through the existing HTTP owner and poll current manifest paths
inside the shared runtime. Both are opt-in; broader enrollment/deployment and
controlled outcome validation remain pending.

[Watch this project setup](embedded-watch-setup.md) now checks service/model/source
readiness, shows current-manifest scope and enables project intent with preview
preconditions. Global service permission remains an explicit deployment choice.

## October 6 shared-owner REST read surface

[REST evidence reads](embedded-rest-evidence.md) now share the HTTP MCP daemon’s
owner and existing tool contracts through a bounded opt-in endpoint. This is a read
surface; whole-request context bundles and inference-proxy integration remain gates.

## October 6 explicit FIRE recovery

[Durable task/original recovery](fire-durable-continuity.md) now has a private local
store and opt-in MCP tools with real process-reopen acceptance. Client cadence,
current-source validation and operational restore/outcome gates remain; whole-request
bundle budgeting is the next coordinated implementation slice.

## October 6 FIRE-backed context bundles

The [snapshot adapter](fire-context-bundles.md) now connects scoped checkpoint/original
recovery to the shared budgeted contract through MCP/REST. Provider formatting,
forwarding, actual usage and continuation measurements remain the next gate.

## October 6 distribution layout

[Artifact acceptance](distribution-acceptance.md) now verifies packaged indexing
workers, source-archive contents and isolated continuity imports. This repairs a
reproduced wheel omission; minimal dependencies and installed native runtime
acceptance remain release gates.

## October 6 minimal installed runtime

[Core/native dependency separation](minimal-embedded-install.md), explicit writable
runtime paths and installed fixture indexing/fresh-daemon REST acceptance now pass
on macOS ARM64 / Python 3.14. Exact-pin rebuilds, artifact notices/SBOM, restore and
controlled coding outcomes remain required.

## October 6 commercial sequencing

The [commercial direction](commercial-direction.md) now prioritizes a source-graded
repository-investigation demo and validation of a bounded paid service. The user's
need for income favors testing a delivery outcome before expanding the platform.
CLARITY relationships/team/contact/access concepts and smart glasses remain distinct
longer-term applications. All installed-product release gates remain in effect;
no outreach, paid engagement or income has been established.

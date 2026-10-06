# Five-priority completion status — October 5, 2026

This is the current summary; dated reports contain the supporting evidence.
Historical roadmap checkpoints describe their own dates and are not current
instructions. "Implemented" below means the named slice has evidence, not that
the whole priority or enterprise release is complete.

| Priority | Current status | Completed and evidenced | Remaining acceptance |
| --- | --- | --- | --- |
| 1. Reliable indexing | Core local recovery and golden-resolution slice complete; broader operations remain. | Tracked shadow ownership/heartbeat, guarded cleanup/adjudication, cancellation identity checks, publication-aware reconciliation, SIGTERM/SIGKILL preservation. Source-reviewed parser fix yields 78 correct route links versus the faulty baseline's 80; historical 81st edge cannot be reconstructed. | Keep uncertain/remote writers protected; extend operational recovery coverage before claiming coordinated multi-host recovery. Golden changes still require source review. |
| 2. Embedded storage | Substantial functional implementation; full application cutover incomplete. | Ladybug transaction adapter, owned graph/vector publication, LanceDB text/vector/hybrid reads and deletion, source/parser facts/static links, standard related-file, static symbol-reference and declared-import overview bridges, project discovery, annotations, latest-attempt journal, IDE registration/lease renewal, opt-in watcher dispatch and guarded setup. Real 131-file/1,629-chunk indexing and MCP reads with external storage networking denied. | Broader symbol-reference coverage, resolved symbol-import and route summary query bridges and broader resolution, REST ownership integration, chosen IDE adapter deployment/legacy registry migration, new-file watch enrollment, production model/runtime identity. Latest-attempt recovery does not provide job history or automatic resume. |
| 3. FIRE continuity | Contracts and provenance implemented; durable task recovery incomplete. | Versioned EvidenceReference/TaskCheckpoint contracts and structured retrieved evidence retained through ranking/assembly; offline scope/provenance/deduplication/outage checks. | Persist scoped task checkpoints and original evidence; explicit resume/corrections; retention/deletion; recover goal and supporting originals after removing the original conversation. Existing generic memory checkpoints are not proof of this acceptance. |
| 4. Bounded context assembly | Partial foundations; complete-request contract incomplete. | Compact tool catalog, bounded numbered source, structured provenance and existing selection/deduplication. Compact schema pilot measured 77.54% fewer schema tokens. | Tokenizer-aware whole-request accounting including instructions/history/tool schemas/output reserve; deterministic selection/freshness/omissions; equivalent MCP/REST bundles; no duplicate history injection. Individual tool limits and character caps are not this gate. |
| 5. Release and outcome validation | Runtime/parser validation and pilots complete; release/outcome gates incomplete. | Project Python 3.14 cutover/rollback, published modified ts-pack pin, native fork-wheel validation, isolated installs and local CI receipts. Prior paired pilot and source-based grading exist; they establish no MCP superiority. | Minimal embedded distribution, exact artifact/dependency/grammar/model notices and SBOM, supported-platform install matrix, backup/restore drill, repeated controlled coding investigations with actual usage/latency/resource/fallback measurements. Qwen worker trial is one functional case, not a paired savings evaluation. |

## Evidence by priority

1. [Reliable indexing report](../benchmarks/reports/2026-10-04/reliable-indexing.md)
   and [operator recovery procedure](indexing-lifecycle-safety.md).
2. [Real embeddings acceptance](real-embedding-acceptance.md),
   [shared MCP owner](embedded-mcp-owner.md),
   [symbol/call evidence](embedded-static-relationships.md),
   [standard related-file bridge](embedded-related-files.md),
   [declared-import overview bridge](embedded-import-overview.md),
   [static symbol-reference bridge](embedded-symbol-references.md),
   [metadata](embedded-project-metadata.md), [journal](embedded-indexing-journal.md),
   [IDE leases and watcher dispatch](embedded-refresh-and-watching.md),
   [guarded watch setup receipt](../benchmarks/reports/2026-10-05/embedded-watch-setup.json).
3. [FIRE implementation and acceptance](fire-platform.md).
4. [Context contract and budgeting direction](context-platform-direction.md),
   [measured schema/outcome pilot](../benchmarks/reports/2026-10-03/README.md).
5. [Runtime setup](python314-runtime.md), [fork upgrade](ts-pack-upgrade.md),
   [integrated release requirements](integrated-platform-plan.md),
   [dependency repair receipt](../benchmarks/reports/2026-10-05/dependency-refresh.md).

## Work order from this checkpoint

The [bounded Qwen investigation/patch/review trial](../benchmarks/reports/2026-10-05/qwen-worker-trial.md)
is complete, including a rejected truncated attempt, supervisor corrections and
an implemented optional embedding-authentication patch. It proves one functional
local-worker case; proxy-route integration and paired savings remain unverified.
Resume Priority 2 with the missing embedded query bridges and
explicit owner routing; choose and exercise one real IDE adapter before calling its
integration deployed. Keep Priority 3 checkpoint/original-evidence persistence and
Priority 4 bundle/budget contracts as the next coordinated implementation slices.
Priority 5 packaging and paired outcome measurements remain release gates.

Subsequent Priority 2 slice: [embedded related files](embedded-related-files.md)
now bridges the standard tool to cited incoming/outgoing import, call and route
file relationships. Native reopen/reindex/deletion, bounded output and real
STDIO/HTTP parity pass. Symbol-reference/import summaries and full graph parity
remain open; this file-level view does not supply them.

The [declared-import overview](embedded-import-overview.md) now bridges the standard
summary tool to verified parser observations with citation samples and explicit
scan/output limits. This completes the declared-import summary slice, not resolved
symbol-import edges or implicit usage. Symbol references, resolved bindings, route
summaries and remaining owner integrations remain Priority 2 work.

The [CLARITY/personal cockpit direction](clarity-cockpit-direction.md) is exploratory
and separate from repository tooling delivery. No audio, wearable capture, citizen
publication network or automatic cloud supervisor is implemented by these documents.

The [static symbol-reference bridge](embedded-symbol-references.md) now exposes
verified incoming call candidates through the standard tool, including workspace
deduplication, ambiguity and continuation. Native persistence and real transport
parity pass. Exhaustive references, resolved imports and route summaries remain open.

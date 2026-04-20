# Large Repo Indexing Notes

This note captures the current behavior for very large structural indexing runs such as
[`swiftlang/swift`](https://github.com/swiftlang/swift).

## What Stays Enabled

These features remain on for large repos:

- core structural parsing
- symbol extraction
- import and relationship writes
- Swift SourceKitten enrichment
- semantic chunking and embeddings
- normal retrieval over Postgres plus Neo4j

Large-repo safeguards are intended to preserve indexability, not to turn off the main retrieval path.

## What Gets Gated

### Clone enrichment

`ts-pack-index` now auto-disables clone enrichment on large manifests.

- env: `TS_PACK_CLONE_ENRICH_MAX_FILES`
- default: `5000`
- `0` means no automatic cutoff

This only disables duplicate/clone grouping work. It does **not** disable Swift enrichment or core graph extraction.

Why:

- clone candidate accumulation was a major native-memory risk on `swiftlang/swift`
- clone enrichment is optional for retrieval quality
- losing clone groups is a better trade than failing the whole structural index

### GDS analytics

Large graph analytics are also guarded:

- `TS_PACK_GDS_STANDARD_MAX_FILE_NODES`
- `TS_PACK_GDS_PAGERANK_MAX_SYMBOL_NODES`

These thresholds let large repos skip expensive GDS jobs instead of exhausting Neo4j heap or stalling the run.

Skipping GDS affects ranking/community-style signals more than baseline retrieval correctness.

### Swift enrichment index augmentation

Swift enrichment stays enabled on large repos, but the heaviest Xcode/SourceKitten
augmentation path is now guarded so finalize does not disappear into a silent
multi-minute `sourcekitten index` / `xcodebuild -showBuildSettings` phase.

Relevant knobs:

- `TS_PACK_SWIFT_ENRICH_USE_XCODE_INDEX`
- `TS_PACK_SWIFT_ENRICH_INDEX_MAX_FILES`
- `TS_PACK_SWIFT_ENRICH_INDEX_MAX_TARGET_FILES`
- `TS_PACK_XCODEBUILD_TIMEOUT_S`
- `TS_PACK_SWIFT_ENRICH_BATCH_SIZE`

Current defaults:

- xcode/index augmentation: `off`
- scoped Swift file cutoff: `5000`
- per-target index augmentation cutoff: `2000`
- `xcodebuild` timeout: `30s`
- Neo4j Swift enrichment write batch: `2000`

Behavior:

- structure-based SourceKitten enrichment still runs
- doc comments, inherited types, qualified names, and primary owner promotion still run
- Xcode/index augmentation is opt-in and remains off by default
- very large scoped runs also skip the heavyweight Xcode/index augmentation step instead of stalling finalize

Tradeoff:

- very large repos may lose some extra USR/index-assisted Swift enrichment fidelity
- retrieval remains materially better than disabling Swift enrichment altogether

## Source-of-Truth Policy

For Swift indexing, tree-sitter-owned fields should remain canonical whenever tree-sitter can produce them.

Current policy:

- `qualified_name`: tree-sitter-owned
- `doc_comment`: tree-sitter-owned
- SourceKitten may still populate sidecar fields such as:
  - `swift_sourcekitten_qualified_name`
  - `swift_doc_comment`
  - `swift_usr`
  - `swift_extended_type`
  - `swift_inherited_types`

The Xcode/SourceKitten index path is no longer assumed to be part of default indexing.
It is now an opt-in precision path, not the baseline source of truth.

## Swift Pathological Parse Guard

Structural indexing now guards pathological Swift inputs with extreme delimiter nesting before they hit the native parser.

Current guard:

- paren nesting limit: `2048`

Example file that required this:

- [`/Users/michaelmarler/Projects/swift/test/Parse/structure_overflow_paren_exprs.swift`](/Users/michaelmarler/Projects/swift/test/Parse/structure_overflow_paren_exprs.swift)

Behavior:

- structural indexing logs a `swift parse guard fallback`
- the file is kept indexable without driving the native parser into a crash path

## Neo4j Sizing Used On This Machine

For the local `128 GB` M3 Max machine, Neo4j is currently sized to:

- heap: `16G`
- page cache: `16G`

Those values are meant to support large graph writes without over-tuning the DBMS.

## Current Operational Guidance

For very large repos:

1. Prefer reliability over optional analytics.
2. Treat clone enrichment as expendable before treating Swift enrichment or semantic retrieval as expendable.
3. If structural progress is unclear, inspect:
   - native process RSS
   - write-phase logs
   - whether finalize has started
4. If a full run fails, isolate the structural slice first before changing broad settings.

## Post-Run Comparison Checklist

When a large-repo run completes, compare these against the prior failed attempts:

- structural parse time
- structural write-phase time
- structural finalize time
- peak struct RSS
- semantic total chunk count
- semantic total elapsed time
- average `pg_copy_codebase_embeddings_batch` latency
- whether GDS ran, skipped, or partially skipped
- whether clone enrichment was skipped by policy
- whether any Swift parse-guard fallbacks fired

The main success criteria are:

- no runaway structural memory regime
- no early native crash
- structural finalize reached and completed
- semantic completed without mixed-state cleanup issues

# Post-run index health — 2026-09-06

Initial MCP health reported 283 files and aligned successful runs. A later status
inspection found structural runs had advanced automatically during this session.
The failed job exposed structural run `6f8dead37cb2:58296:1788712752820000000`,
whose timestamp falls during the investigations. The semantic phase failed.
Therefore the pilot's warm label describes its initial state only; the condition
was not held stable and cannot establish controlled retrieval differences.

At post-run inspection:

- Disk and structural files: 296; semantic files: 278; stale semantic files: 18.
- Structural run: `6f8dead37cb2:59931:1788713385200000000`, done.
- Semantic status: failed, not aligned to the current structural run.
- Shadow residue remains one namespace, 4,478 nodes, 1,031 relationships.
- No explicit index/rebuild/cleanup call was made by either benchmark agent.

Two distinct errors appear in the failed semantic job:

1. Native stage copy SQL fails near `SELECTchunk_id` (likely missing SQL whitespace;
   diagnosis still requires inspecting the pinned ts-pack implementation).
2. Fallback upsert fails because PostgreSQL cannot load `$libdir/dict_snowball`.

Read-only host inspection found a PostgreSQL 17.9 (Homebrew) server still running
with executable path `/opt/homebrew/opt/postgresql@17/bin/postgres`, while
`/opt/homebrew/opt/postgresql@17` no longer exists. The server data directory remains
`/opt/homebrew/var/postgresql@17`. This supports a missing local PostgreSQL installation
as the library-load failure, but no reinstall or shared database restart was performed.
Do not change tokenization to work around a missing database library.

Before a confirmatory benchmark: restore a matching PostgreSQL 17 installation,
coordinate any required restart of the shared database, verify text-search operations,
reproduce/fix the native staging SQL issue independently, and run both index phases
successfully. Isolate or suspend automatic indexing for the benchmark snapshot and
record beginning/end run IDs. Clean shadow data only in a verified quiet window.
No shadow data was deleted in this session.

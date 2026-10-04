# Evidence-driven code intelligence plan — October 3, 2026

Goal: improve correctness and effort on defined repository investigation and coding
change tasks. Native exact-name searches and bounded file reads remain valid choices.
No enterprise certification, productivity superiority, latency target, or token
savings claim follows from tool-contract tests alone.

## Context platform direction

Add persistent context with bounded working sets as a first-class product goal.
Concatenate selected, deduplicated evidence into a token-budgeted package with
provenance, freshness and explicit omissions. Preserve durable task state and
retrieve prior evidence on demand across sessions. “Limitless context” describes
continuity as an aspiration; it does not increase model input capacity or promise
perfect recall. Share the context contract between MCP and the optional REST
inference proxy, without changing inference providers.

Prioritize native LadybugDB/LanceDB integration; Docker remains optional packaging.
Reuse existing memory assembly/checkpoint foundations. See
[the context direction](context-platform-direction.md) for contracts, budgeting,
implementation order and multi-session acceptance tests.

## Sequence and acceptance criteria

1. **Valid treatment exposure.** Require a fresh CLI session to successfully invoke
   MCP health before any paired questions. Keep setup usage/time separate. Catalog
   readiness and per-tool read approvals are already configured. The October 3
   probe passed with discovery permitted; September 30 results remain excluded.
2. **Small controlled pilot.** Freeze source and index identity, exclude evidence
   hints and indexed benchmark reports/case files, alternate conditions, retain actual completed-turn usage and native
   fallbacks, and independently grade source citations. Abort on drift or missing
   usage. A shared stable index is a pilot limitation; a dedicated isolated index,
   repeated randomized orders, unfamiliar repositories, stale/partial conditions,
   and code-change tasks remain confirmatory requirements.
3. **Compact profile.** Twelve tools include the dispatcher. Default remains `all`
   for compatibility; appliance explicitly selects `primary`. Discovery filtering
   is not authorization. Dispatcher permits an explicit set of secondary read
   analyses, excludes writes/recursion, forwards Context and preserves results.
   Catalog can return one secondary tool's input schema on demand. Measure schema
   tokens with `scripts/measure_tool_profiles.py`; encoding-specific counts are
   neither exact model usage nor per-turn billing. Existing tool descriptions
   dominate the remaining catalog; further reduction needs selection replays.
4. **Truthful working-tree evidence.** Successful current-file AST parsing replaces
   the indexed outline completely, including after deletion of every symbol.
   Body/signature/location changes cannot establish alignment from names alone.
   Indexed graph relationships and semantic previews are explicitly historical.
   Source hash equality must be captured by the native indexing reader before
   claiming indexed alignment: existing File nodes have no indexed content hash.
   Until then, report alignment unknown. This is a live file outline, not a graph
   overlay, and does not read unsaved editor buffers. Future graph overlays need
   invalidated relationships, deletion handling, run-scoped hashes and regression
   fixtures; post-hoc hashing must not certify an earlier parse.
5. **Portable development appliance.** Parameterize workspace and credentials;
   keep database ports internal and MCP published on loopback. Exclude secrets and
   runtime data from build context. Bound startup waits, fail schema bootstrap,
   bind the daemon to the checked container port. Verify build, schema startup,
   indexing, queries, restart persistence and outage recovery in isolated volumes.
   Configuration validation alone is not deployment or production proof.
6. **Bounded embedded feasibility.** Keep Neo4j/Postgres as the operational backend.
   Kuzu is archived; reopen graph selection before adoption. Evaluate maintained
   candidates against the actual query corpus, project isolation, optional failure,
   rollback, concurrent sessions, teardown, schema evolution, indexing and tool
   parity. Current wrappers are experimental and do not implement end-to-end
   zero-server indexing. LanceDB standalone smoke tests do not establish RRF,
   IVF-PQ construction or retrieval parity. Use version-pinned native FTS APIs;
   shared Rust implementation does not imply zero-copy Arrow integration.

## Enterprise acceptance beyond the pilot

Define supported repository sizes, indexing duration and memory budgets, p50/p95
query latency, citation correctness, fallback rate and maintenance policy. Establish
access controls, tenant/project isolation, backup/restore, versioned migrations,
health/readiness semantics, resource limits, dependency security and an upgrade
policy. Separate optional inference-proxy compatibility from this tooling work.

## Current evidence

See `benchmarks/reports/2026-10-03/` for measurements and checks. Record failed
attempts and limitations alongside successes. Each architectural decision needs
measured evidence before promotion from experiment to supported backend.

Sources: [Kuzu archive notice](https://github.com/kuzudb/kuzu),
[LanceDB full-text search](https://docs.lancedb.com/search/full-text-search),
[official MCP configuration](https://learn.chatgpt.com/docs/extend/mcp).

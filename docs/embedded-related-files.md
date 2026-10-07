# Embedded related-file bridge — October 5, 2026

Existing `get_related_files(project_path=None, file_path="", workspace_id=None)`
now routes embedded mode through the shared repository owner. Its response schema
remains a string; embedded results are JSON containing published static evidence.
Legacy server-backed behavior is unchanged. Embedded dispatch precedes legacy
workspace hashing, Neo4j queries and memory-module loading.

## Implemented behavior

Resolve the workspace against committed project discovery, normalize absolute file
paths within the published root, and verify the stored original source. Return six
ordered groups: incoming/outgoing imports, calls and HTTP-route candidates. Each
relationship retains both endpoint source hashes, its publication run, resolution
rule and original call/route location when available. Same-file edges are excluded.

The owner holds its lock across source and relationship reads; every page must match
the source publication. Live file edits do not silently change historical results.
Reindexing replaces stale relationships; reopen retains them and scoped deletion
removes access. Missing projects/files and older relationship contracts have explicit
statuses. An absolute path outside the workspace is refused.

The result reports `coverage_complete: false`. These are the conservative
[static relationships](embedded-static-relationships.md), not runtime dispatch,
symbol-import usages, implicit imports, crate/build/resource links or same-directory
heuristics. Empty pages cannot establish absence of other dependencies.

## Bounds and continuation

Each of six groups reads at most ten relationships; an oversized page retries with
one. The combined serialized JSON budget is 48,000 bytes, with a 44,000-byte insertion
threshold leaving room for remaining metadata. `truncated` and each group's
`next_cursor` disclose page/output limits. A single relationship that cannot fit
the underlying reader's budget is refused, not silently shortened.

Continue a group through the existing explicit tool:

```text
get_embedded_relationships(project_id, kind, file_path, direction,
                           limit=10, after=next_cursor)
```

For a truncated group with an empty-string cursor, begin that group with `after=""`:
the combined response had no room to emit its first relationship. `None` means there
is no further page within that reader's published candidate set. Explicit pages may
include same-file links omitted from this related-file view. Cursors are ordered
relationship IDs, not snapshot tokens; verify the run on every subsequent page.

## Validation and limits

Nineteen native combined-owner tests pass, including this bridge's incoming/outgoing
import/call evidence, source hashes, live-edit isolation, reopen, reindex, deletion
and standard MCP invocation. Eleven runtime/MCP tests pass, including shared-owner
dispatch without legacy path resolution or an encoder. Three offline checks cover
missing/old publications, mixed-run refusal and byte/cursor/self-link limits.

Real sequential STDIO and Streamable HTTP MCP calls matched the direct native result
on a disposable two-file fixture with two inter-file relationships. Fixture indexing
used explicitly synthetic vectors; the model-independent reads loaded no encoder.
Memory integrations and watchers were disabled in isolated service processes. No
operational service or repository index was replaced. This is transport/storage
acceptance, not real-model retrieval or performance evidence.

[Machine-readable receipt](../benchmarks/reports/2026-10-05/embedded-related-files.json).
Logs and transport fixture code remain under `.runtime/embedded-related-files-acceptance/`
(mode 700). Full local CI is recorded in the receipt; no new hosted result is claimed.

Priority 2 still needs symbol-reference/import/route summary bridges, broader
resolution, REST ownership integration and IDE deployment/new-file enrollment.
See [current five-priority status](five-priority-status.md).

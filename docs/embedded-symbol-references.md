# Embedded symbol-reference bridge — October 5, 2026

The standard `find_references(workspace_id, symbol_name)` tool now routes embedded
workspaces through the shared runtime and repository owner. It returns JSON in the
existing string result. Legacy graph/full-text behavior remains available when
embedded storage is not selected.

For each unique published project, exact-name symbol context supplies the definition,
publication run, source hash and up to 20 incoming static call candidates with
endpoint citations. Reads use published snapshots and require no embedding model.
The runtime serializes dispatch and the repository owner guards each project's
source/relationship reads. Projects may legitimately have different publication runs;
the result is not an atomic snapshot across independent projects.

One to eight workspace names or paths are accepted, aliases of the same project
are deduplicated in request order, and the combined JSON has a 48,000-byte ceiling.
Oversized results fail explicitly; request fewer workspaces. Underlying symbol
context reads also apply their existing output limits, including outgoing adjacency
verification even though this response omits outgoing calls.

Missing workspaces/symbols, ambiguous definitions and old relationship publications
retain explicit statuses. Ambiguous names are not merged; inspect candidate files
using `get_symbol_context` with `file_path`. A caller page with more results includes
arguments for `get_embedded_relationships` continuation. Compare publication run IDs
across pages and restart after reindexing; cursors are not retained snapshots.

`coverage_complete` is always false. Current resolution covers Python module/function
bindings, not all languages or runtime dispatch. Type usages, field accesses, string
mentions, resolved symbol-import references and dynamic calls remain unsupported.
This tool alone does not establish safe rename/delete completeness.

Validation: 21 native repository tests and 13 runtime/routing tests, full local CI,
and real STDIO/Streamable HTTP parity on an isolated two-file fixture. Native checks
cover live-edit snapshot stability, reopen, reindex changes and ambiguous definitions;
runtime checks cover alias deduplication, mixed statuses, continuation and byte limits.
Synthetic vectors test storage/transport behavior, not semantic quality or savings.
See the [acceptance receipt](../benchmarks/reports/2026-10-05/embedded-symbol-references.json).

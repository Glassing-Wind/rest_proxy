# Embedded declared-import overview — October 5, 2026

The existing `get_symbol_imports_overview(project_path, limit=20,
include_implicit=False)` tool now has an embedded-owner path. Its response remains
a string; embedded results are JSON. Legacy Neo4j behavior is unchanged.

## Completed slice

The shared runtime resolves a committed project without legacy workspace hashing,
external graph access or an embedding encoder. The owner holds its lock across the
summary. Reads verify original source and parser-fact hashes against the publication;
corruption or mixed runs are refused. Older fact contracts request reindexing.
Live file edits do not silently alter published evidence; reindex, reopen and deletion
follow the existing ownership/publication rules.

The overview ranks **declared named-import observations**, grouped by source module
and imported name, and files by named-item/declaration counts. It also reports total
import declarations and wildcard declarations within the scanned files. Each ranked
name includes up to three file/run/source/fact citations. Native zero-based line spans
are converted to one-based lines; missing spans remain null. `citations_complete`
indicates whether all observations fit the citation sample. File rows retain the
source/fact hashes and inherit the response run.

These are parser observations, not resolved `IMPORTS_SYMBOL` edges. Named aliases
are not reconstructed when absent from stored facts. Imports without named items
contribute to declaration counts, not named-item rankings. Wildcard imports do not
invent individual names. `resolved_symbol_edges` is false and implicit imports are
explicitly unsupported even when requested; they are not reported as verified zero.
This bridge therefore does not complete semantic symbol-binding or reference parity.

## Bounds and interpretation

- `limit` must be 1..100 and bounds each ranked section.
- Scan at most 100 manifest files in deterministic path order.
- Retrieve at most 8 MiB of source/fact bytes; a preflight four-byte-per-character
  UTF-8 ceiling can conservatively stop before a file that might fit. Manifest
  metadata is outside this source/fact counter and retains publisher limits.
- Serialized JSON is capped at 48,000 bytes; insertion thresholds reserve metadata
  space. Ranked-row and scan truncation are separate fields.

`scope` is always the scanned published files, not automatically the whole project.
`published_files`, `scanned_files`, `scan_next_file`, `scan_read_bytes`, `truncated`
and `truncation_reasons` disclose coverage. Rankings and counts from a partial scan
are not global project totals. This summary has no new paging parameter: inspect
remaining known paths through `get_embedded_file_facts` with its per-file pagination,
then original source through `describe_embedded_file`. Revalidate run IDs after a
publication change. Individual scan/output limits do not complete Priority 4's
whole-request token budget.

## Acceptance

Twenty native repository tests, twelve runtime/MCP checks and two offline overview
checks pass. The new cases verify declared names versus aliases/wildcards,
unsupported implicit coverage, source citations, reopen/live-edit isolation,
reindex/deletion, corrupted-fact refusal, scan/byte/output limits, old publications
and missing source spans. Gated CI includes the offline overview checks.

Real sequential STDIO and Streamable HTTP MCP calls matched the direct native
summary on a disposable two-file fixture. Indexing used explicitly synthetic vectors;
summary reads needed no model. Optional memory integrations and watchers were disabled
in isolated service processes. This is functional/transport evidence, not a semantic
quality or performance comparison. No operational index or provider changed.

[Acceptance receipt](../benchmarks/reports/2026-10-05/embedded-import-overview.json).
Private logs and fixture code: `.runtime/embedded-import-overview-acceptance/` (mode
700). Full local CI status is recorded in the receipt; no new hosted result is claimed.

Priority 2 still needs symbol-reference investigation, resolved symbol bindings and
route summaries, broader resolver coverage, REST owner integration and IDE deployment.
See [five-priority status](five-priority-status.md).

## October 6 extension

The [function-import binding extension](embedded-symbol-imports.md) now adds a
separate bounded resolved-candidate page. Declaration rankings retain their original
semantics; full bindings and implicit imports remain unsupported.

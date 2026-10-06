# Embedded function-import bindings — October 6, 2026

New embedded publications include `symbol_imports` relationships for conservative
Python module-level `from ... import function` bindings. Aliases and relative
imports retain the imported name, local name, source declaration line, defining
symbol ID, endpoint source hashes and publication run. These are static source
candidates, not runtime dispatch guarantees.

The resolver requires a unique local snapshot module, a single undecorated top-level
function definition and one unmodified importer binding. Rebinding (including match
captures), wildcard/dynamic namespaces, known module-export writes, missing or
ambiguous modules, classes, nested imports and reexports remain unresolved. Python
module lookup uses the snapshot root; it does not reconstruct deployment search
paths. Other languages and full binding coverage remain unsupported.

`get_embedded_relationships(kind="symbol_imports")` supports existing file/direction,
limit and SHA cursor arguments. To find imports of one published function, pass
`direction="in"` and its exact `symbol_id`. Outgoing bindings are file-level; they
have no caller-symbol ID. Compare publication runs between pages and restart after
reindexing. Relationships retain existing manifest-membership/hash/endpoint checks.

`get_symbol_imports_overview` keeps declared-import rankings and now adds a separate
`resolved_import_bindings` page of at most 20 relationships, limited by the requested
row count. Rankings still have `resolved_symbol_edges=False` because their counts
describe declarations. `binding_coverage_complete=False` describes the separate
binding page. Continuation arguments are supplied when more bindings exist.
Implicit imports remain unsupported. The standard reference tool still reports
static callers only; use this relationship query for importing files.

Older publications without the new resolver capability return
`reindex-required-for-symbol-imports-v1` for this kind, rather than claiming no
imports. Other relationship kinds remain readable. No automatic reindex or schema
reset occurs. New publication manifests record this capability and per-kind counts.

Import identifiers are bounded at 512 characters and declaration-expression samples
at 2,000 characters with an explicit truncation flag. Original source remains in
the published evidence. The combined overview retains a 48,000-byte JSON limit;
oversized overview-plus-binding results fail explicitly and request a lower limit.
Reads share the owner lock and require no embedding model.

Validation: 23 native repository tests, 14 runtime tests and two offline binding
tests, full local CI and real STDIO/HTTP overview parity. New native checks cover
aliases, target-symbol filtering, paging, snapshot stability, reopen, reindex,
old-capability status and deletion. Offline subcases exercise unsafe/unsupported
bindings, relative imports and expression bounds. Synthetic vectors establish no
semantic-quality, performance or savings claim. See the
[acceptance receipt](../benchmarks/reports/2026-10-06/embedded-symbol-imports.json).

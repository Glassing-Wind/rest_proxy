# Embedded static relationships — October 4, 2026

Priority 2 now publishes static source candidates as actual Ladybug `EVIDENCE_LINK`
relationships between files. Their payloads retain callable symbol IDs where
supported, resolution rules, expressions/locations and both endpoint source hashes.
They commit with outlines, originals and the sole graph publication receipt after
vector staging. This is an explicit compatibility slice, not full graph parity.

## Resolution rules

| Kind | Supported rule | Left unresolved |
| --- | --- | --- |
| Python imports | Unique module file under the snapshot root, including relative imports | External/stdlib modules, competing module/package files, non-root source layouts, conditional/function-local imports |
| Python calls | Unique undecorated module function through a stable same-module name, named import alias or directly imported module alias | Parameters, local rebinding, duplicate definitions, wildcard imports, known global/dynamic namespace mutation, class/nested/comprehension/lambda scopes, reexports and general value/type resolution |
| JS/TS/TSX imports | One exact relative module candidate among supported file/index paths | Package aliases, multiple extensions/index candidates, bundler configuration and reexports |
| HTTP routes | One native `file_route` definition with the same literal method/path | Unknown methods, duplicate providers, parameter/wildcard paths, mounted routers and framework configuration |

Python definition binding accounts for function parameters (including type
parameters), direct local stores/deletes/imports and module bindings. Known writes
through directly imported module aliases invalidate affected callable exports.
Module-level calls before the supporting declaration/import are excluded. Symbol
IDs come from the native outline spans; call sites must match native byte-range
observations. Two calls on the same line remain separate candidates. Native route
and import summaries can collapse repeated identical file dependencies.

These rules identify **static source candidates**, not guarantees of runtime
dispatch or execution. Python module lookup assumes the snapshot root; `sys.path`,
namespace-package competition outside the snapshot, runtime monkeypatching,
reflection and conditional execution are not fully modeled. Python syntax accepted
by the native parser but unsupported by the running interpreter AST is retained
as source/facts and left unresolved. Route matches inherit
the native extractor's file-layout conventions; route groups, prefixes, rewrites
and runtime configuration are not certified. More coverage requires explicit
resolution work and independent validation, not broader name guessing.

## Publication and reads

Schema initialization adds and validates the `EVIDENCE_LINK` property columns.
Existing outline databases retain their version-1 schema marker. New snapshots
include a relationship contract/counts and ordered payload-hash membership IDs in
the hashed manifest. Publishers validate endpoints, source hashes and symbol IDs
against the snapshot before writes. Snapshot candidates are capped at 50,000.

The explicit read tool is:

```text
get_embedded_relationships(project_id, kind="calls", file_path="",
                           direction="out", limit=50, after="", symbol_id="")
```

Kinds are `calls`, `imports` or `http_routes`; direction is `out` or `in` relative
to the optional file path. Incoming call candidates expose caller IDs and source
files; outgoing candidates expose callee IDs and target files. Limits are 1..100;
follow the SHA256 `next_cursor`. Optional `symbol_id` filters exact caller/callee
IDs using the canonical payload field before pagination. Each page reports its run ID; a cursor is not a
multi-page snapshot token. A 48,000-byte response budget refuses oversized pages.

Reads filter both file endpoints and relationship project/run/kind, then verify
payload hashes, manifest membership, endpoint paths and source citations. They do
not load an embedding model. Original source/fact bytes can be verified through
the existing published evidence tools. Reindex replaces stale links atomically;
rollback restores previous links/receipt, and scoped deletion detaches them.
Older publications return `reindex-required-for-relationships-v1` and retain their
other read capabilities. They are not silently resolved using current files.

## Standard symbol-context bridge

Existing `get_symbol_context` now routes embedded workspaces through the shared
owner, bypassing legacy workspace hashing and external memory imports. It keeps
its string response schema; embedded responses contain JSON. Exact native symbol
names can be narrowed by relative/absolute file path or available signature text.
Qualified/fuzzy names and unavailable signatures are not synthesized.

A unique match returns symbol metadata, publication identity, source SHA256,
bounded numbered original source and separate static caller/callee candidate pages
(up to 20 each). Multiple matches return up to 20 choices; missing names are marked
`symbol_not_published`. Preview limits are capped at 200 lines / 16,000 characters;
full previews still respect caps. Source can be omitted, while citation verification
remains. Context explicitly reports its resolution scope and `call_graph_complete:
false`. All source/relationship pages must share the symbol run. Total output is
capped at 48,000 bytes. Empty candidate lists do not prove
absence of runtime callers: the conservative resolver's coverage still applies.
Older relationship contracts report their reindex requirement explicitly.

## Standard call-chain bridge

Existing `get_call_chain` now routes embedded workspaces through the same owner
and exact symbol selection as context. Embedded responses remain JSON strings.
Downward/upward traversal follows published caller/callee IDs breadth first, with
1..5 hops, 20 candidates per adjacency, 64 discovered symbols, 128 relationships
and a bounded output budget (44,000 bytes before final metadata; 48,000 total).
Relationships retain citations, minimum discovery hop and `revisits_symbol`.
Repeated symbols are expanded once, so cycles terminate; a revisit can also be a
shared dependency. Depth is a requested horizon, not proof of graph completeness.

`truncated` and `truncation_reasons` report adjacency, symbol, relationship or byte
limits. These are bounded graph edges, not enumeration of every path. Ambiguous or
missing roots keep the existing explicit statuses. All reads must match the root
publication; no mixed-run chain is returned. The owner lock serializes publication
and traversal. Old relationship contracts report a reindex requirement.

Four offline traversal tests cover directions, horizons, cycles, ambiguity,
publication changes and every traversal cap. Ten native repository methods pass,
including cyclic chains and the standard MCP bridge. Real sandboxed STDIO/HTTP
results match for a nonempty two-edge chain from `build_outline_snapshot` in the
previous frozen publication. Full local CI passed. This adds no runtime-dispatch,
full graph coverage or performance claim.

[Call-chain acceptance receipt](../benchmarks/reports/2026-10-04/embedded-call-chain.json).

## Executed acceptance

A fresh real Jina embedding run on **131 frozen production Python files / 1,629
chunks / 768 dimensions** published **1,118 call candidates and 186 import
candidates** (1,304 total). All relationship hashes, membership and endpoint
citations verified across all pages. All 131 original source/fact hashes verified
as well (806 import observations / 12,996 call observations). The narrower candidate
count is not a semantic correctness score or full-call-graph coverage claim.

This Python-only sample produced no HTTP-route candidates. Native TypeScript
fixtures prove one exact GET route match while refusing the POST mismatch and
ambiguous import targets. Seven gated native-parser resolver tests also cover aliases,
relative modules, local recursion, forward module execution, rebinding, parameters,
known module mutation, conditional wildcard imports, generic type parameters and unsupported interpreter
AST syntax without hiding competing module candidates.
Nine optional native combined-owner methods pass, including exact/ambiguous
standard symbol dispatch, original source surviving live edits, incoming/outgoing
relationship queries, pagination, genuine graph rollback, reopening, payload
corruption refusal, stale-link replacement and deletion. Four transaction and four
outline tests pass; seven offline runtime/MCP checks and full local CI pass.

Real sequential STDIO/HTTP MCP relationship and standard symbol-context results
match, alongside discovery, overview, facts, original source and text search. Indexing allowed only the already
loaded loopback model endpoint; verification denied external-storage networking.
No model lifecycle mutation or inference-provider change occurred. The frozen
source snapshot is not claimed to match current HEAD. Model artifact/runtime
attestation and controlled coding outcomes remain acceptance gates.

[Acceptance receipt](../benchmarks/reports/2026-10-04/embedded-static-relationships.json).
Private logs/data: `.runtime/embedded-relationships-acceptance/` (mode 700).
Next: broaden validated symbol/module/route resolution, bridge remaining
reference/import queries, and complete remaining workspace metadata and REST
integration. Existing standard indexing workers remain guarded.

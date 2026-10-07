# Embedded parser facts — October 4, 2026

Priority 2 now publishes cited import, call-site and native route/HTTP observations
with original source. Use `get_embedded_file_facts(project_id, file_path, limit=50,
offset=0)` through the shared embedded owner on STDIO or HTTP MCP. The read tool
appears in the primary discovery profile and needs no embedding connection.

## Contract and scope

Each newly indexed file stores a versioned fact envelope and a SHA256 of its exact
serialized facts in the publication manifest. Native parser imports are preserved.
Call extraction supports Python, JavaScript, TypeScript and TSX; other languages
report `unsupported-language` while retaining available imports/native facts.
Native `extract_file_facts` now receives the relative file path, enabling supported
file-route conventions as well as HTTP request-method observations.

Call records contain the observed target expression, byte start/end, one-based
start/end lines and the smallest containing Function/Method's ID/name, when present.
Byte ranges are verified against the original UTF-8 source. Ownership is lexical
containment, not proof of execution: calls in default arguments or declaration
expressions may execute outside a function invocation. Targets are syntactic
observations, not resolved callee IDs or CALLS edges. Dynamic expressions, imports,
aliases and overloads still need semantic resolution. Native import spans retain
the parser's zero-based lines/columns and byte coordinates. Route/HTTP summaries
retain their native extractor's coverage and limits; file-level source hashes cite
them, but they do not gain call-site coordinates by implication.

Reads require the source record's run to match the published receipt, verify its
source bytes/hash against the manifest and verify the fact hash. Returned evidence
includes project/path/run/source SHA256, per-group counts and truncation/pagination
metadata. Limits are 1..100 per group; offsets are 0..10,000. Follow `next_offset`.
The fact payload has a 24,000-byte output budget; excessive output refuses and asks
for a smaller limit. Scalar native metadata is retained on each page.

Before publication, files exceeding 10,000 call observations or 1,000 imports are
refused. A snapshot also refuses more than 50,000 imports+calls or 64 MiB of fact
JSON. These explicit limits avoid silent call truncation. Existing file/source,
symbol and chunk limits still apply. Parsing/fact extraction completes before
vector staging and the atomic graph visibility switch.

Older snapshots without versioned, hashed facts return
`reindex-required-for-fact-contract-v1`. They remain usable for source/text/vector
reads and discovery; the tool does not silently reparse current files. No schema
migration is required. Reindexing through the existing explicit manifest path
publishes the new fact contract. Fact reads after project deletion return no result.

## Executed acceptance

A fresh real Jina embedding run on the frozen production-source manifest indexed
**131 files / 1,629 chunks / 768 dimensions**, preserving **806 imports and 12,996
call observations**. All 131 source/fact hashes verified against the publication
after reopening. Indexing allowed only the existing loopback model endpoint;
verification denied all networking. The serving model was already loaded, with no
lifecycle mutation. Resident artifact binding/runtime attestation remains unresolved.

Real sequential STDIO/HTTP MCP checks returned identical project discovery,
standard resolution/overview, bounded source, text search and version-1 fact
results. Their server policies denied external-storage networking; fact reads
required no model. This uses the frozen source manifest, not a claim that it matches
current HEAD or that retrieval/coding outcomes improved.

Three gated native-parser tests cover UTF-8 byte spans, nested lexical ownership,
imports, JavaScript/TypeScript/TSX HTTP method isolation and file-route detection,
and call-limit refusal. Seven optional native combined-owner methods pass,
including pagination, bounded cited facts, unchanged originals after live edits,
reopen, scope isolation, tampering detection and deletion. Six offline runtime/MCP
checks and full local CI pass. Native storage tests remain optional hosted gates.

[Acceptance receipt](../benchmarks/reports/2026-10-04/embedded-parser-facts.json).
Private evidence: `.runtime/embedded-facts-acceptance/` (mode 700).
Next: resolve targets/modules/routes into trustworthy relationships, support the
existing symbol/caller/import investigation queries, complete remaining workspace
metadata and REST integration. This slice does not establish full graph parity.

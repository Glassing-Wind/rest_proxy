# Shared context-bundle contract — October 6, 2026

`assemble_context_bundle` now exposes a version 1 provider-neutral request/evidence
packing contract through MCP and the opt-in REST read surface. It does not call a
model, retrieve evidence automatically or modify inference requests. Clients supply
the complete text request and candidate evidence; the result preserves original
instructions/messages/tools and adds selected evidence as a clearly labeled data
message. Only the returned `payload` is the accounted request, not the whole tool
response containing metadata and duplicate representations for inspection.

## Configuration and request

Set `LM_PROXY_CONTEXT_ENABLED=1` before starting either MCP transport. REST also
requires the existing `LM_PROXY_EMBEDDED_REST_ENABLED=1` and embedded daemon setup;
call `/evidence/read` with `tool="assemble_context_bundle"` and
`arguments={"request": ...}`. Both paths invoke the same registered function.

Optional `LM_PROXY_CONTEXT_TOKENIZER_FILE` names a local tokenizer JSON file, at most
32 MiB. The builder uses the optional `tokenizers` library, disables truncation and
padding, and identifies the exact artifact by SHA256. It never downloads assets.
A configured tokenizer failure is explicit rather than silently using an estimate.
Without a tokenizer, accounting is labeled `utf8-byte-estimate` (one unit per UTF-8
byte of canonical JSON). This is not a claim of measured provider token usage.

Request fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Optional, defaults to 1; other versions rejected. |
| `project_id`, `session_id`, `task_id`, `goal` | Required exact nonempty scope and goal. |
| `instructions`, `messages`, `tools` | Complete caller-supplied text request; defaults empty. Up to 200 messages and 100 schema objects. |
| `context_capacity`, `output_reserve` | Required declared total capacity and reserved output. |
| `safety_margin`, `framing_tokens` | Defaults 256 each; caller-owned reserves for safety and provider framing. |
| `provider_continuation`, `provider_context_tokens` | Provider-held context: continuation defaults false. When enabled, omitted/null usage means unknown and blocks added evidence. |
| `evidence` | Up to 100 uniquely identified candidate objects. |
| `existing_evidence_ids`, `existing_bundle_ids` | Explicit caller deduplication hints; default empty. |

An evidence candidate includes `id`, exact project/session/task scope, `kind`
(`repository`, `history`, `task-state`), `text`, finite numeric `relevance`, and a
structured `citation` with `source_id`. Preserve original source/run/line fields
in that citation. Repository evidence additionally requires a lowercase SHA256
`citation.content_hash` and equal `current_hash`; otherwise it is omitted as changed
or unverified. Hash agreement is caller-supplied, not automatic disk/editor validation.
The rendered excerpt gets a separate `text_sha256`, so an original-source hash is
not confused with a numbered or shortened excerpt's hash.

## Selection and accounting

The builder counts the entire canonical request JSON for every candidate addition,
including instructions, goal, messages, tool schemas, evidence boundaries and
citations. It adds the declared provider-held context, framing, output and safety
reserves, and selects only whole items that fit. Candidates sort by descending
relevance then ID. Duplicate source/hash/excerpt representations are omitted; scope
and freshness failures have explicit reasons. Citations are never truncated to fit.

Mandatory overflow preserves the base request and returns `mandatory-over-budget`;
it does not silently discard instructions or conversation. Unknown provider context
returns `provider-context-unknown`. Neither status certifies budget fit. Results
include mandatory/assembled input counts, evidence increment, every reserve, a stable
bundle ID, selected citations, sorted omissions and `fits_accounted_budget`.

`fits_accounted_budget` applies to this declared canonical-payload accounting, not
an unimplemented provider serializer. Local tokenizer JSON must match the intended
model, and its chat template/special tokens require a correct framing reserve or a
future provider adapter. The byte estimate is explicitly approximate. Images/audio
and other multimodal content are rejected. Tool-call/result pairs must be complete;
the builder preserves them and never cuts messages or schemas.

Exact history text already present in supplied messages is omitted. Retrieved history
is also omitted under provider continuation. Existing IDs suppress equivalent evidence
even under another candidate ID; an already-present bundle adds no second data message.
The caller must supply current messages and deduplication hints correctly. The builder
cannot discover hidden client history or detect partial text overlap reliably.
Candidate IDs must identify an evidence version; change them when source version or
excerpt changes before supplying existing-ID hints. The builder has no prior-client
state with which to detect an ID incorrectly reused for changed content.

Input JSON is limited to 128,000 bytes and response JSON to 48,000 bytes; REST retains
its smaller 16,000-byte request cap. Oversized responses fail explicitly; reduce request
or evidence sizes. These are resource bounds, separate from token accounting.

## Acceptance and remaining gates

Seven contract/tool tests cover deterministic packing, whole-request/reserve accounting,
mandatory overflow, unknown provider state, scope/freshness/citation preservation,
history/ID/bundle deduplication, complete tool pairs, local tokenizer counting and
MCP/REST parity. Full local CI passes. A real indexed-source fixture produces identical
bundles through STDIO, Streamable HTTP and REST, using the labeled byte estimate;
the accounted fixture fits its declared budget. Synthetic vectors do not establish
semantic quality or performance. See the
[acceptance receipt](../benchmarks/reports/2026-10-06/context-bundle-contract.json).

Priority 4 now has a shared supplied-request contract. Provider-specific request
serialization and model/tokenizer/framing verification, automatic retrieval/live-source
freshness, FIRE checkpoint-to-bundle adaptation and inference-proxy forwarding remain
unfinished. Exact provider usage, duplicate-injection behavior in a real continuation
flow and paired coding outcomes require end-to-end measurements.

## October 6 FIRE adaptation

[Scoped FIRE snapshot adaptation](fire-context-bundles.md) now supplies checkpoint
state and original excerpts to this contract through one historical-data snapshot.
Earlier pending-adaptation statements are historical; provider forwarding and actual
usage remain unimplemented.

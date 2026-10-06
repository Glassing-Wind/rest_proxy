# FIRE snapshot-to-bundle integration — October 6, 2026

`assemble_fire_context_bundle` now connects the scoped durable checkpoint store to
the shared context builder. It reads one checkpoint/original snapshot and adapts it
into cited historical candidates. The current caller goal, instructions, complete
messages and schemas stay intact. Recovered constraints or decisions do not become
new system instructions automatically.

Enable both `LM_PROXY_CONTEXT_ENABLED=1` and `LM_PROXY_FIRE_STATE=/private/path`
before starting MCP. REST uses the same function through `/evidence/read`, with the
existing embedded REST setup enabled. No storage owner, model lifecycle or inference
provider changes occur. The tool is absent when FIRE or context configuration is off.

Arguments are the existing version 1 context `request`, optional `current_hashes`
and `expected_revision`, plus `max_originals` (0–10, default 5) and `max_chars`
(1–8,000, default 4,000). All three project/session/task fields must match the saved
scope. An expected revision mismatch includes no recovered candidates; omission of
that argument accepts the latest snapshot at read time.

## Recovery and packing

The store's single snapshot query returns checkpoint state and originals from the
same payload. Hash verification happens before adaptation; there are no repeated
page reads that could combine revisions. A concurrent correction after the read
does not alter that snapshot. The result discloses its revision and expiry; clients
must recheck state if they require the latest revision at later inference time.

Checkpoint navigation includes its goal, accepted constraints, current decisions
with origins, open questions, next actions and correction metadata. Only the current
revision is adapted; obsolete conclusions are not merged. Stored goal is historical
navigation and does not replace the current request goal.

Original candidates are selected in source-ID order before packing. Full-content
hashes remain distinct from rendered excerpt hashes. Oversized originals become
explicitly marked first-page excerpts with continuation information for
`get_fire_original`. Missing originals, changed/deleted sources and original-count
limits produce diagnostics. `current_hashes` is caller-supplied; there is no live
filesystem/editor validation. Historical originals are never asserted to be current
code merely because they were recovered.

Candidate IDs include revision/content identities and excerpt settings so changed
recovery content does not reuse previous deduplication IDs. Checkpoint state has
default relevance 100 and originals 90; caller evidence may have higher scores.
All recovered candidates are `history` evidence for packing policy. Provider
continuation therefore suppresses them, as do matching existing evidence/bundle
hints. Clients that already hold provider history must request fresh repository
evidence separately if needed. Whole-item budget omissions and citations follow the
[shared context contract](context-bundle-contract.md).

The response adds `fire_recovery` with snapshot status, revision/expiry, candidate
identity, original count and diagnostics. True recovery omissions also appear in
the bundle's omission list; excerpt notices stay separate because that candidate
may still be selected. Missing, expired, deleted or unavailable state leaves a
usable caller-only bundle with an explicit recovery status. A recovery exceeding
the combined input/candidate contract similarly degrades to caller-only context.
Invalid caller requests or final response-size overflow remain explicit failures.

Byte/character limits and canonical payload accounting remain unchanged. This
integration does not certify provider serialization/token usage, auto-forward to
inference, schedule checkpoints or automatically promote historical state to user
instructions.

## Acceptance

Six adapter tests plus six durable-store and seven builder tests pass. Checks cover
current corrections, revision/scope boundaries, original/excerpt hashes, missing and
changed sources, count/budget omissions, provider/history deduplication, expiry/outage,
deletion, one-read snapshot consistency under concurrent correction and oversized
recovery degradation. Full local CI passes.

A real transport drill saves through STDIO, closes the process, removes the fixture
conversation/source and obtains identical bundles through fresh HTTP MCP and REST.
The accounted fixture fits its declared byte-estimate budget; correction changes
bundle identity and deletion leaves explicit missing-state context. See the
[acceptance receipt](../benchmarks/reports/2026-10-06/fire-context-bundles.json).

The next gate is one explicit provider request adapter with serializer/tokenizer/
framing verification, actual usage and continuation handling. Operational restore
drills and paired investigation outcomes remain release gates.

See [opt-in stateless LM Studio context forwarding](context-provider-forwarding.md)
for the implemented provider adapter, actual usage acceptance and remaining limits.

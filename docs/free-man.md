# Free Man and Rest-proxy

As of October 19, 2026. This document separates the research purpose from the
operational platform and defines the first bounded connection between them. See
the [lessons baseline](lessons-corpus-baseline.md) for the underlying aims and
the [corpus guide](lessons-corpus.md) for source acquisition and representation.

## Purpose and architectural decision

Rest-proxy serves its operator: repository investigation, personal assistance,
memory, model collaboration and authorized use of computer tools. Free Man is a
separate research initiative concerned with learning, judgment, responsibility,
freedom and the user's longer-term ambition of superintelligence. That ambition
does not establish that this implementation is superintelligent, conscious,
self-directed or benevolent. Those are distinct research questions.

Free Man should support examining the Bible, philosophy, history and other
traditions, comparing interpretations, and revising lessons when contrary evidence
appears. It should preserve the source and the disagreement rather than turn one
interpretation into an unchallengeable doctrine. Intelligence, moral judgment,
personhood and the authority to act are separate questions.

The systems share code and representation contracts. They do not automatically
share credentials, private memory, configuration or permission to act. They can
initially live in this repository with separate entry points and explicit data
paths; a second Git repository alone would not provide a security boundary.

| Layer | Existing foundation | Boundary for this implementation |
| --- | --- | --- |
| Shared source representation | Original bytes, structural text ASTs, source hashes, exact citations, retrieval and lesson revision contracts. | Reuse code and schemas; select every corpus explicitly. Preserved wording is evidence of what a source says, not proof that it is true. |
| Rest-proxy operations | Existing MCP/REST tools, repository investigation and FIRE/task infrastructure. Availability depends on the configured deployment. | Operational credentials, callbacks, tools and private state stay with the operational runtime. The research entry point does not import that runtime. |
| Free Man research reads | A dedicated corpus MCP factory and STDIO launcher, sharing bounded source reads with the optional corpus REST adapter. | Read only the selected corpus. No file manipulation, network acquisition, lesson review, model execution or operational tools are exposed. |
| Lesson review and exchange | Append-only lesson histories and exact source citations. | Explicit offline review and release artifacts; no automatic delivery into operational memory, prompts or tools. |
| Future research execution | Not implemented by the read adapter. | Restricted execution, independent state, resource limits and an explicit network policy are required before untrusted research code runs. |

FIRE remains an operational memory implementation. A future Free Man memory
service may reuse its abstractions with its own store and retention policy;
sharing a running FIRE singleton or its private database is not part of this
boundary. The same restriction applies to operational graph stores, application
accounts, environment secrets and provider sessions.

## First implementation: selected evidence over MCP

The research entry point is an explicitly launched STDIO process:

```sh
.venv/bin/python -m scripts.free_man \
  --corpus /absolute/private/path/site-capture/representation
```

Its implementation is a dedicated low-level MCP factory in
`graphrag_core/lessons_corpus/mcp.py`, using the standalone corpus reader. It does
not load `.env`, operational configuration, FIRE or the main MCP tool registry.
Importing this module does not start a listener or choose a default private
corpus. STDIO access belongs to the local client that launches the process; it is
not an authenticated shared-service deployment.

The five tools are `free_man_status`, `free_man_list_sources`,
`free_man_read_source`, `free_man_search_sources` and
`free_man_list_references`. They provide bounded source discovery, exact excerpts,
lexical search, checked reference occurrences and corpus status. Quotes retain Unicode
offsets and source/text hashes. Source integrity is checked on read. The adapter
retains coverage gaps and the qualification of candidate reference recoveries;
it does not silently promote a candidate to a recovered historical original.

References are data. Listing a link does not fetch its target. Instructions
embedded in a book, page, reference or retrieved passage do not grant tools,
change system policy or authorize further work. This read surface offers no
mutation, acquisition, provider, review or training operation. A caller's other
tools remain the caller's responsibility.

A separate process under the same macOS account is **not an OS sandbox**. Its
account may retain access to other files and the network even when its MCP
catalog offers no such operation. A restricted account or container, filesystem
mount policy and enforced network policy are later execution requirements, not
capabilities delivered by this adapter. Do not execute untrusted research code
in this process on the strength of its read-only tool catalog.

## Human review and controlled exchange

Investigation precedes lesson derivation: preserve attributed claims, examine their
evidence and alternatives, review findings, then propose a lesson within those
findings' scope. The [baseline connection](lessons-corpus-baseline.md#investigation-to-lesson-connection)
uses versioned dependencies from each new lesson to exact reviewed non-lesson
records. Proposal, acceptance and release preparation enforce this prerequisite;
source integrity and an explicit review do not establish that a finding is true.

At the user's direction, both actual private lessons remain pending proposals
after independent model critique. No release of these lessons has been recorded.
Their original records, critiques, revised wording, counter-context and predicted
outcome checks remain in the revision history. A model's assessment does not
constitute human acceptance, and a human acceptance is an attributed decision,
not proof of historical truth or beneficial outcomes.

The bounded exchange workflow is local and data-only:

1. Record and review investigation findings, then save a proposed lesson with
   exact `investigation_refs`; see the [authoring workflow](lessons-corpus.md#investigate-before-proposing-lessons).
   Produce a review packet bound to the selected corpus manifest, exact current
   lesson and investigation revisions. Preparation validates ledger histories and
   source citations; the packet contains revision envelopes, previous hashes,
   supporting and counter-citations, uncertainty, outcomes and authored records.
2. Record a human acceptance of the exact lesson under review. Any changed
   wording, source identity or review history requires another review packet;
   regenerate the packet after acceptance so the accepted review revision is included.
3. Record a separate human release of that exact packet for a named intended
   use, with an explicit rights basis and allowed usage. Acceptance of a lesson
   alone does not authorize its transfer, redistribution or training use.
4. Create a bounded release bundle and inspect its hashes, references, review and
   release records before considering any downstream use.

Choose a selection file containing exactly a `lessons` list. Each selected item
has an exact current revision and its recorded SHA-256 fingerprint; the values
below are placeholders, not approvals of either actual private proposal:

```json
{
  "lessons": [
    {
      "lesson_id": "example-lesson",
      "revision": 4,
      "revision_sha256": "<64-character lowercase SHA-256 from the current ledger event>"
    }
  ]
}
```

Prepare the packet with explicit absolute paths. Output files must be fresh,
under an existing directory owned by the current user with mode `700`; generated
files use mode `600`. Keep input selection and release records private too.

```sh
.venv/bin/python -m scripts.lessons_corpus review-packet \
  --corpus /absolute/private/path/site-capture/representation \
  --ledger /absolute/private/path/site-capture/lesson-ledger.sqlite3 \
  --selection /absolute/private/path/review/selection.json \
  --output /absolute/private/path/review/packet.json
```

New packets use `free-man-review-packet/2`; release bundles use
`free-man-lesson-exchange/2`. Packets select at most ten lessons, each with 1–32
investigation references. The `investigations` array includes each shared finding
once, preserving its actual accepted, disputed or rejected review. Exchange JSON
artifacts have a 1 MiB bound.
Preparation refuses a stale lesson or investigation revision. A packet can
represent proposed lessons for review, but release additionally requires every selected event to be
the current exact `accepted` review with an explicit reviewer whose `kind` is
`human`. A legacy reviewer string is recorded as `operator`, not `human`, and
does not satisfy this gate. Model acceptance does not satisfy it either.

A finding correction blocks acceptance, packet preparation and live release even
when the dependent lesson's own hash is unchanged. Save a new lesson revision
with current reviewed dependencies, then review it again. Historical version-1
lesson records remain readable; acceptance and new release require an explicit
version-2 save. Negative reviews of stale or legacy lessons remain available.

To record an explicit human review through the existing authoring CLI, supply a
private reviewer JSON file such as `{"kind":"human","name":"Human reviewer name"}`:

```sh
.venv/bin/python -m scripts.lessons_corpus review \
  --corpus /absolute/private/path/site-capture/representation \
  --lesson-id example-lesson --expected-revision 3 --decision accepted \
  --reviewer-record /absolute/private/path/review/reviewer.json \
  --reason 'Accepted as a working interpretation within the stated limits.'
```

This explicit command appends a review to the corpus's sibling
`lesson-ledger.sqlite3`; the read-only research process cannot perform it. Supply
the actual current revision and decision. A JSON `kind` remains an operator-supplied
identity assertion. Neither actual private proposal was accepted during this work.

After the human decision, regenerate the selection and packet for the accepted
revision. A separate release file has exactly these fields, with the packet
fingerprint returned by `review-packet`:

```json
{
  "expected_packet_sha256": "<exact packet artifact_sha256>",
  "destination": "rest-proxy:evidence-only",
  "reviewer": {"kind": "human", "name": "Human reviewer name"},
  "reason": "Reason for releasing this exact reviewed material.",
  "rights_basis": "Basis permitting this selected material for private local evidence use.",
  "use": "local_inference_evidence_only"
}
```

Only the shown destination and use are supported. The command revalidates the
packet against the current source corpus and ledger before creating a bundle:

```sh
.venv/bin/python -m scripts.lessons_corpus release-bundle \
  --corpus /absolute/private/path/site-capture/representation \
  --ledger /absolute/private/path/site-capture/lesson-ledger.sqlite3 \
  --packet /absolute/private/path/review/packet.json \
  --release /absolute/private/path/review/release.json \
  --output /absolute/private/path/review/bundle.json

.venv/bin/python -m scripts.lessons_corpus inspect-bundle \
  --bundle /absolute/private/path/review/bundle.json \
  --expected-sha256 '<exact bundle artifact_sha256 returned by release-bundle>'
```

Inspection checks the supplied artifact fingerprint, versioned syntax, internal
hashes, use constraints and approval-record structure. It does not compare a
saved bundle to the live ledger or source corpus; its result explicitly reports
`currentness: not_checked_against_source_ledger`. Reprepare and review the current
packet before any operational consumption. Neither inspection nor a matching hash
authenticates the supplied reviewer identity, external origin, legal authority,
or an independently attested full history. Permission to archive a source remains
distinct from permission to redistribute it or use it for training.

Version-2 inspection binds the included reviewed findings and reports
`investigation_prerequisite: bound_reviewed_revisions; currentness_not_checked`.
Historical version-1 bundles remain inspectable with
`investigation_prerequisite: unavailable_in_legacy_artifact`; they cannot be used
to create a new release. Reading an old artifact does not endorse its lessons.

Full source bodies are absent from the bundle, but exact cited quotes and authored
lesson wording are included. This is **not anonymization or redaction**. A selected
record could contain personal identifiers or sensitive text; selection does not
scrub them. Inspect the actual content and rights before release. A bundle is data
for a receiving system to inspect, not a new instruction or a grant of authority.

The review workflow reads an explicit existing private ledger through a separate
read-only reader. It requires a quiescent rollback-journal database; it does not
create a ledger, migrate it, enable WAL or start a writer. The original corpus and
ledger remain outside Git. The workflow does not discover or merge the private
investigation exercise; the operator must still keep its records out of selections
and inspect each release for sensitive material.

This slice does not import bundles into Rest-proxy memory, inject their text into
prompts, activate tools, change model weights or start adaptive learning. Any
operational consumption needs its own explicit integration and outcome evidence.

## What to measure next

First verify the engineering boundary: launch and MCP reads work with only the
selected corpus; optional operational services can be absent; source tampering
fails; outputs are bounded; forbidden mutation tools are absent; and denied or
invalid requests leave corpus and ledger bytes unchanged. Review packet tests
must preserve dissent, reject stale revisions and prevent a model reviewer from
standing in for the required human release.

Then evaluate reasoning on frozen, reviewed tasks. Compare the same model/runtime
with and without selected evidence, retain exact prompts and source versions,
and assess faithful quotation, competing interpretations, calibrated uncertainty,
correction after contrary evidence, and consequences for affected people. Include
adverse and ambiguous cases, not only examples expected to support a lesson.
Record exact model identity, runtime and settings, usage and latency when
available, and human review. Mark unavailable identity or provenance explicitly.

Source retrieval and sound moral language alone do not demonstrate learning,
stable ethical behavior or improved outcomes. A model tier is an experimental
variable, not a grant of moral authority. Training, persistent adaptation and any
expansion of permissions require separate designs, rights records and measured
acceptance criteria.

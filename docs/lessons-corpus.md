# Local lessons corpus

The [baseline](lessons-corpus-baseline.md) defines the purpose and source policy.
This implementation preserves an authorized WordPress source, represents its
words and references, retrieves exact passages, and records attributed lessons.
It is a separate local corpus inside Rest-proxy's tooling package. It does not
change the running MCP service, repository index, inference provider or model.
The [Free Man research boundary](free-man.md) gives this foundation a separate
research entry point and an explicit, reviewed lesson-exchange contract.
The [James pilot](james-pilot.md) adds a bounded structured scripture adapter and
an initial two-edition investigation using the same source and ledger contracts.

## Acquire and build

Use the existing project environment from the repository root. Choose a fresh,
absolute directory outside Git; captures and representations are not overwritten.
The capture creates owner-only directories and files. Supply an explicit archival
rights basis for the seed site.

```sh
.venv/bin/python -m scripts.lessons_corpus capture \
  --url https://21stcenturyprogressivism.com/ \
  --output /absolute/private/path/site-capture \
  --rights-basis 'Owner authorized archival acquisition; other uses recorded separately.'

.venv/bin/python -m scripts.lessons_corpus build \
  --capture /absolute/private/path/site-capture \
  --pdf-python /absolute/path/to/python-with-pypdf

.venv/bin/python -m scripts.lessons_corpus verify \
  --corpus /absolute/private/path/site-capture/representation
```

PDF extraction is optional. When no PDF runtime is supplied, original PDFs remain
preserved and the representation records the extraction gap. A Python environment
with `pypdf` is sufficient; the acquisition, AST, ledger and search need no new
database daemon. The desktop's bundled document runtime can supply `pypdf` without
replacing the project's working environment.

Acquisition combines public WordPress APIs with sitemaps and public HTML, including
numbered content pages and archive pagination. It captures published posts, pages,
approved public comments, media metadata, terms and public authors. Same-origin
referenced PDFs are preserved separately. It does not acquire external reference
bodies, media image/audio/video bytes, private or deleted records, historical
revisions, or image OCR. Bounds and failures appear in `coverage.json`; completion
within this scope requires consistent initial/final API inventories and no pending
in-scope acquisition. A changing website is not an atomic database snapshot.

Network requests use HTTPS certificate validation, a fixed seed-host policy,
bounded redirects and response sizes. Ordinary private network addresses are
refused. Environments that resolve public hosts through a synthetic `198.18/15`
proxy route require matching public DNS answers from the fixed HTTPS resolver;
the corroboration is recorded in the inventory. No network settings are changed.

## Inspect and retrieve

```sh
.venv/bin/python -m scripts.lessons_corpus search \
  --corpus /absolute/private/path/site-capture/representation \
  --query 'service' --source-kind wordpress_post_content_html --limit 5

.venv/bin/python -m scripts.lessons_corpus show \
  --corpus /absolute/private/path/site-capture/representation \
  --source-id post:123:content_html
```

Search uses SQLite FTS5 with its own BM25 ranking. It is lexical retrieval, not
semantic embedding or a model interpretation. The source-kind filter applies
before ranking and limiting. Every result includes an exact quote, half-open
Unicode character offsets, source/text hashes and a resolvable source identifier.
Retrieval validates the original-response lineage and the stored representation.

Stable identifiers include `post:N:content_html`, `page:N:content_html`, public
HTML acquisition identifiers and PDF page identifiers. `sources.jsonl` lists them.
Full WordPress metadata is preserved as explicitly derived canonical JSON;
published dates, modification dates and capture times keep their separate meaning.

`references.jsonl` preserves reference occurrences, not only unique URLs. HTML
attributes include labels and source spans; JSON URL scalars and PDF annotation
URIs retain their own provenance. Literal HTTP(S) URLs in HTML/PDF prose also
remain distinct observations, with exact text spans, original source segments,
and explicit punctuation/decoding policy. A prose URL is not an authenticated
hyperlink. Compound attributes such as `srcset` are retained
without guessing individual candidates. Malformed, admin, contact and unresolved
targets remain visible. `external_unacquired` and `same_site_unacquired` are gaps,
not proof that a target is valid or that its contents have been reviewed.

## Investigate reference gaps

The checked reference frontier groups HTTP(S) occurrences by their exact URL
spelling with the fragment removed. It preserves occurrence counts and up to five
source-bound samples per target. It does not merge different hosts, infer identity,
or contact the network. The default `authored` scope covers post/page content;
`all` also includes metadata, public HTML and extracted PDF pages.

```sh
.venv/bin/python -m scripts.lessons_corpus frontier \
  --corpus /absolute/private/path/site-capture/representation \
  --scope authored --origin external --offset 0 --limit 50
```

Select candidates explicitly in a private JSON file. Copy `target_id` from the
frontier; a candidate may be a new location, but that relationship stays unreviewed:

```json
[
  {
    "target_id": "<64-character target hash from frontier>",
    "candidate_url": "https://reference.example/article/",
    "relation": "candidate_counterpart"
  }
]
```

Use `same_url_recheck` only when the candidate exactly matches the frontier URL.
A second private JSON file records processing rights by exact permitted hostname:

```json
{
  "reference.example": "Owner authorized private archival processing; redistribution and training rights assessed separately."
}
```

Permission descriptions are operator records, not independently verified license
grants. An additional hostname, including a `www` alias or redirect destination,
requires its own entry. Recovery checks public addressing and refuses admin/login
paths under the same network policy as seed acquisition.

```sh
.venv/bin/python -m scripts.lessons_corpus recover \
  --corpus /absolute/private/path/site-capture/representation \
  --output /absolute/private/path/reference-capture \
  --selections /absolute/private/path/selections.json \
  --permissions /absolute/private/path/permissions.json

.venv/bin/python -m scripts.lessons_corpus build \
  --capture /absolute/private/path/reference-capture
```

Recovery fetches at most 20 selected candidates, with a 64 MiB total response
budget, and follows no discovered links. Original response bytes, failures, rights
bases, selected relationships and the parent manifest hash remain in a separate
snapshot. A changed parent during preflight refuses the operation; a change during
fetching remains a coverage warning. HTML and optional PDFs use the existing AST
pipeline. Build a separate representation and pass it explicitly to search or REST.

Recovery source identifiers include a snapshot UUID. The parent sources, citations,
coverage warnings and lesson history are never rebound to the candidate wording.
`interpretation_status` qualifies candidates in search and REST source/catalog reads
as `reference_recovery_unreviewed; historical_equivalence_not_established`.
Successful body capture proves neither historical equivalence nor authorship.
Archive-index metadata alone is not a recovered historical body. Training and
redistribution rights for another site do not follow from permission for the seed.

## Investigate before proposing lessons

First record the investigation as an attributed `source_claim`, `experience`,
`value` or `hypothesis`. Use exact search citations in a UTF-8 JSON record.
Records require all of these fields; `outcomes` is optional. Replace the empty
citation list with checked source citations before saving:

```json
{
  "kind": "source_claim",
  "statement": "An attributed assertion to investigate in the cited passage.",
  "author": {"kind": "human", "name": "Author name"},
  "context": "Circumstances and source context.",
  "values": ["care"],
  "affected_parties": ["readers"],
  "uncertainty": "Limits, disagreement and missing outcome evidence.",
  "citations": [],
  "counter_citations": [],
  "outcomes": []
}
```

Each citation requires `source_id`, `source_sha256`, `text_sha256`, `start_char`,
`end_char` and the exact `quote`. Model authors additionally require `model_id`.
Kinds are `experience`, `source_claim`, `value`, `hypothesis`, or `proposed_lesson`.
An optional outcome has exactly `kind`, `description`, `method`, and `citations`.
Reported and measured outcomes require citations; predicted outcomes may omit them.
These classifications are supplied by the author, not independently established
by the ledger. No training or automatic lesson generation occurs.

```sh
.venv/bin/python -m scripts.lessons_corpus propose \
  --corpus /absolute/private/path/site-capture/representation \
  --lesson-id care-finding --expected-revision 0 --record /absolute/path/finding.json

.venv/bin/python -m scripts.lessons_corpus review \
  --corpus /absolute/private/path/site-capture/representation \
  --lesson-id care-finding --expected-revision 1 \
  --decision disputed --reviewer 'Reviewer name' --reason 'Competing interpretations remain.'
```

A reviewed finding can be accepted, disputed or rejected. These are attributed
assessments, not proof of truth; preserve contrary evidence, uncertainty and scope.
Copy the resulting review event's `lesson_id`, `revision` and `revision_sha256`
into the new lesson record. Use the same required author/context/citation fields
as above, set `kind` to `proposed_lesson`, and add:

```json
{
  "investigation_refs": [
    {
      "record_id": "care-finding",
      "revision": 2,
      "revision_sha256": "<exact lowercase SHA-256 from the current review event>",
      "role": "context"
    }
  ]
}
```

This snippet is an addition to the record, not a complete record. Allowed roles
are `supports`, `challenges` and `context`. Each new lesson requires 1–32 distinct
current, reviewed non-lesson records in the same ledger; self-reference is refused.
Non-lesson records must omit `investigation_refs`. Save the lesson explicitly:

```sh
.venv/bin/python -m scripts.lessons_corpus propose \
  --corpus /absolute/private/path/site-capture/representation \
  --lesson-id care-example --expected-revision 0 --record /absolute/path/lesson.json

.venv/bin/python -m scripts.lessons_corpus lessons \
  --corpus /absolute/private/path/site-capture/representation
```

The append-only `lesson-ledger.sqlite3` lives beside the representation. Writes
require the caller's expected revision. Changed wording creates a new proposed
revision; accepted, disputed and rejected reviews preserve previous records.
Citations and the revision hash chain revalidate after reopen. A review records an
operator-supplied identity and assessment; it does not authenticate the reviewer
or elevate an interpretation to historical fact.

New saves use schema version 2. Lesson acceptance and review-packet/release
preparation recheck the exact dependencies. A later finding correction blocks
these operations even if the lesson itself did not change. Save a new lesson
revision referencing the current reviewed finding, then review it again. Historical
schema-1 records stay readable; acceptance or a new release requires an explicit
schema-2 lesson save. Disputed/rejected reviews remain possible for stale or legacy
lessons, and earlier history stays readable after corrections.

`review --reviewer NAME` preserves the legacy `operator` identity kind. Use
`--reviewer-record /absolute/private/path/reviewer.json` instead for an explicit
`{"kind":"human","name":"Reviewer name"}` or model identity record. This is still
an operator assertion, not authentication. The research MCP cannot write reviews;
see [controlled exchange](free-man.md#human-review-and-controlled-exchange) for
the separate human acceptance and release steps.

An independent model critique is another attributed assessment. Retain its
counter-context and limitations, then append revised wording as a new proposal;
model review does not stand in for human approval. Predicted outcome checks remain
plans until observations or measurements are actually collected. The
[October 15 recovery and review receipt](../benchmarks/reports/2026-10-15/lessons-recovery/README.md)
records this process for the first two private lesson proposals.

## Optional local REST reads

The REST adapter is opt-in and read-only. Set `LM_PROXY_LESSONS_TOKEN` to an
unpredictable capability of 32 through 256 ASCII characters through your local
secret-management workflow, then launch explicitly:

```sh
.venv/bin/python -m scripts.lessons_corpus serve \
  --corpus /absolute/private/path/site-capture/representation --port 8781
```

It binds `127.0.0.1` and requires `Authorization: Bearer <capability>` on every
request, including `/health`. The exact Host must match `127.0.0.1:8781` for this
launch. Actual loopback peers are required, proxy headers are ignored, browser
Origin headers are refused and CORS is disabled. Access logging is disabled to
keep source queries out of routine request logs. The adapter starts no inference,
acquisition or lesson mutation. It is intended for native local clients; do not
embed it behind a remote gateway as an authenticated team-service boundary.

| Endpoint | Read contract |
| --- | --- |
| `/health` | Authenticated process liveness; no claim of full dependency readiness. |
| `/sources?offset=0&limit=50` | Metadata catalog, at most 100 sources. |
| `/source?source_id=post:123:content_html&start_char=0&end_char=512` | Exact Unicode excerpt/citation, at most 4096 characters. |
| `/search?query=service&limit=5&source_kind=wordpress_post_content_html` | At most 50 source-bound lexical results. |
| `/references?source_id=post:123:content_html&status=external_unacquired&limit=50` | At most 100 checked reference occurrences with pagination. |

Source, search and reference I/O runs outside the event-loop thread. Responses
project bounded fields and use private cache/error headers; source URLs are capped
at 4096 characters with an explicit `url_truncated` flag. Full ASTs, internal
paths, raw JSON literals and raw reference attributes stay in the local artifacts.
Tampered artifacts fail their read checks. HTTP mutations are unavailable; authored
lesson writes and reviews remain explicit CLI operations.

## Storage and verification boundary

- `raw/`: byte-identical acquired response bodies, addressed by SHA-256.
- `acquisitions.jsonl`, `documents.jsonl`, `inventory.json`, `coverage.json`:
  requested/final URLs, public objects, rights, DNS evidence, bounds and gaps.
- `representation/`: original decoded fields, derived text, ASTs, references,
  search index, PDF extraction records and artifact hashes.
- `lesson-ledger.sqlite3`: attributed interpretations, citations and review history.

AST tokens reconstruct every character of the decoded source. Readable text
records entity decoding, omitted nonprose and generated separators explicitly.
PDF text is an extraction with page provenance, not original PDF byte offsets or
proof of complete visual transcription. Hashes detect changed stored artifacts;
they do not establish authorship, truth or independent chain-of-custody attestation.
Full originals and private interpretations remain outside Git and ordinary repo
indexing. Repository receipts should contain counts and verification results,
not source bodies or private case records.

The dedicated [Free Man MCP reader](free-man.md#first-implementation-selected-evidence-over-mcp)
shares source projections with REST and runs as a separately launched STDIO process.
It does not register tools with the operational daemon or inherit its FIRE store.
Review packets and separately approved data bundles support explicit exchange;
neither importing into operational memory nor automatic model prompting is implemented.
The current CLI and optional adapters work without changing a live daemon. Training datasets,
model adaptation and claims of improved behavior require their own evaluations.

## Offline checks

```sh
.venv/bin/python -m unittest test_lessons_ast test_lessons_acquisition \
  test_lessons_ledger test_lessons_corpus test_lessons_server test_lessons_cli \
  test_lessons_frontier test_lessons_recovery test_lessons_ledger_reader \
  test_lessons_exchange test_lessons_investigation test_lessons_mcp
.venv/bin/python -m ruff check graphrag_core/lessons_corpus \
  scripts/lessons_corpus.py scripts/free_man.py scripts/extract_lessons_pdf.py test_lessons_*.py
/absolute/path/to/python-with-pypdf -m unittest test_lessons_pdf
```

The dedicated `lessons-corpus.yaml` CI workflow runs these offline checks and
installs the pinned optional PDF test runtime. The PDF tests need `pypdf`; without it they explicitly skip. The other tests use
synthetic sources and mocked network requests. They start no services and mutate
no production index. Live acquisition is a separate, explicit operation.

The [October 19 investigation acceptance](../benchmarks/reports/2026-10-19/investigation-lessons/README.md)
records dependency checks, correction, legacy compatibility and separate-process
CLI reopen/release. These are engineering checks, not evidence that a source
author's conclusions or a proposed lesson have been substantively validated.

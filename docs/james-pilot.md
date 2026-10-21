# James: first bounded scripture investigation

Implemented October 19, 2026. This pilot represents one book in two editions,
one attributed cross-reference dataset and one investigation question. It uses
the existing corpus, citation, ledger and lesson-exchange contracts.

## Scope and sources

| Role | Exact selected source | Rights and attribution |
| --- | --- | --- |
| Structured edition | [eBible WEB Protestant `engwebp` USFM](https://ebible.org/Scriptures/engwebp_usfm.zip), member `89-JASengwebp.usfm` | [Publisher public-domain notice](https://ebible.org/engwebp/copyright.htm). Preserve wording; the World English Bible name has separate trademark conditions. |
| Comparison edition | [eBible ASV 1901 `eng-asv` USFM](https://ebible.org/Scriptures/eng-asv_usfm.zip), member `89-JASeng-asv.usfm` | [Publisher public-domain notice](https://ebible.org/eng-asv/copyright.htm). |
| Cross-references | [OpenBible.info TSV archive](https://a.openbible.info/data/cross-references.zip), member `cross_references.txt` | Attribute [OpenBible.info](https://www.openbible.info/labs/cross-references/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); disclose James-outbound filtering. Its separately copyrighted displayed ESV quotations are absent from the TSV import. |

The original publisher ZIPs contain other Bible books. They are retained as
bounded original archives; only James enters the text representation. No other
book bodies or library collections are imported. The reference archive contains
344,799 rows; only 2,149 rows originating in James enter the representation.
Negative votes and original row order are preserved.

Pin archive/member hashes and acquisition times. Publisher export timestamps do
not establish a translation's textual revision. WEB and ASV share translation
history; two English wordings are not independent historical corroboration.

## Representation and relationships

The narrow `lessons-usfm-james/1` adapter preserves every decoded source character
in ordered tokens, with half-open Unicode character spans. Readable verse text
has explicit whitespace normalization and generated separators. Word attributes,
translator additions, headings and editorial notes remain separately source mapped.
Paragraphs are milestones that can occur inside a continuing verse; they do not
force verses into a false paragraph hierarchy.

The adapter supports the markers observed in these editions. It refuses ambiguous
or unsupported syntax, duplicate verse identifiers and unsafe/oversized archives.
It is not a complete USFM parser. Each selected edition must contain the expected
five chapters and 108 verse markers. Read validation reconstructs the selected
archive member and reparses the verse structure before returning it.

Source identifiers are scoped to the explicitly selected corpus:

- `scripture:engwebp:JAS`
- `scripture:eng-asv:JAS`
- `scripture:openbible:JAS-outbound`

Verse mappings include edition ID, passage address, source/text hashes and exact
text spans. A citation points into the derived verse text; its AST segments map
back to the original USFM. These offsets are not UTF-8 byte offsets. The selected
reference text similarly retains mappings to the original TSV line/character span
and member hash.

`relationships.json` records `editorial_cross_reference` edges. Each edge retains
its dataset identity, attribution, original row, votes and an exact source citation.
Its endpoints resolve separately for each edition. In this captured dataset,
146 target rows resolve to addresses within James; 2,003 remain
`outside_pilot_scope`. A resolved address does not establish translation equivalence,
independent evidence or interpretive truth. No target is fetched automatically.
These data artifacts are not an automatic publication to the operational graph.

## One investigation question

> In James 2:14–26, are actions presented only as evidence of professed faith,
> or also as completing it—and what does the passage establish about responding
> to someone lacking food or clothing?

The review document preserves two model-authored hypotheses: an evidential reading
that emphasizes demonstrating faith, and a completion reading that emphasizes
actions bringing faith to completion. Both have supporting and counter-citations;
both remain disputed in the model review. They are competing emphases, not an
exhaustive account or attributed scholarly/denominational positions.

A narrower textual finding examines the needs example in 2:15–17. Another finding
preserves the two editions' wording at 2:14 and 2:20 without inferring a Greek or
manuscript explanation. The comparison document prints exact captured quotations.

The four findings have explicit **model** author/reviewer records. Their narrow
accepted/disputed assessments do not stand in for human review. The session's exact
model identifier is unavailable and is labeled accordingly. The implementation
persists this bounded authored investigation; it does not generate novel findings
with an inference provider.

One tentative practical-help lesson pins all four reviewed finding revisions.
It remains `proposed`, with no human acceptance or release. Wanted help,
feasibility and checking effects are explicitly additional present-day application
proposals. No measured outcome, theological settlement or training permission is
recorded. The earlier private seed-site proposals remain unchanged.

## Run and inspect

Use module invocation from the repository root:

```sh
.venv/bin/python -m scripts.james_pilot \
  --acquisitions /absolute/private/path/acquisition.json \
  --output /absolute/private/path/new-james-capture
```

Inputs are explicit local downloads; this command contacts no network or model.
The receipt uses `source-research-receipt/1`, with exactly three `sources`, one
each for `engwebp`, `eng-asv` and `openbible`. Each entry requires `edition`, official
`url` and identical `final_url`, `status: 200`, canonical absolute `path`, byte
count `bytes`, lowercase `sha256`, timezone-qualified `acquired_at`, and optional
`headers`. ZIPs must be regular siblings of the receipt. Resolve macOS `/var`
aliases before creating this receipt; symlinked input paths are refused.
The receipt is operator-supplied provenance, not authenticated remote identity.

The output directory must be fresh under an existing canonical parent. Outputs
use private directories/files. It contains original ZIPs, acquisition/rights and
coverage records, `representation/`, `relationships.json`, a separate new
`lesson-ledger.sqlite3`, `pilot.json` and `review/` with the readable investigation,
version-2 review packet and summary. Existing captures and ledgers are refused.

```sh
.venv/bin/python -m scripts.lessons_corpus verify \
  --corpus /absolute/private/path/new-james-capture/representation

.venv/bin/python -m scripts.lessons_corpus search \
  --corpus /absolute/private/path/new-james-capture/representation \
  --query 'faith' --source-kind scripture_book --limit 2
```

Existing source-corpus REST/MCP readers can select this representation explicitly;
the pilot changes no running service, MCP registration, provider or index. Review
and release follow the [existing human review workflow](free-man.md#human-review-and-controlled-exchange).
Packet preparation and release recheck finding revisions even if the lesson's own
hash has not changed. Historical records remain readable after a correction.

## Acceptance and expansion gate

Offline checks cover Unicode round trips, notes, word attributes, overlapping
paragraph/verse milestones, unsupported syntax, archive limits, source/receipt
identity, complete-book selection, original/subset reference offsets, address
resolution, competing readings, exact dependencies and stale-finding refusal.
Separate-process reads and a disposable copy exercise the captured real sources;
the actual lesson remains pending.
The [dated acceptance report](../benchmarks/reports/2026-10-19/james-pilot/README.md)
retains exact counts, source hashes, checks and failed attempts/corrections.

Do not expand merely because parsing succeeds. Next review this question's
passages, limits and competing hypotheses with a human; then select any additional
source or independent expert interpretation deliberately. Spark and corpus-wide
ingestion remain deferred until the bounded workflow demonstrates a processing
need and an acceptable investigation/review process.

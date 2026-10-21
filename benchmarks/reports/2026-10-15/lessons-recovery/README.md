# Reference investigation and first lesson review

This continues the [seed acceptance](../../2026-10-14/lessons-corpus/README.md).
Original bodies and authored interpretations remain in private local storage
outside Git. This receipt contains public URL metadata, counts and hashes; it
contains no private case evidence or source bodies.

The reference frontier and selected-candidate recovery commands now make the
investigation repeatable. They preserve occurrence provenance, require explicit
host processing-rights records, and write a separate private snapshot. Search and
REST catalog/excerpt responses retain the candidate's unreviewed status.

| Check | Result |
| --- | --- |
| Authored post/page source records inspected by frontier | 123 |
| Authored reference occurrences | 2,061; 1,985 HTTP(S), 76 other |
| Distinct HTTP(S) targets after removing fragments | 587 |
| Selected live candidate pages acquired | 8; all HTTP 200; 1,183,647 transferred body bytes |
| Represented sources / syntax nodes | 8 / 7,748 |
| Reference occurrences in candidate pages | 614, including repeated page templates |
| Exact source-token round trips | 8 of 8 |
| Recovery integrity | 28 artifact files and 8 original responses verified |
| Parent integrity | 10,291 artifact files and 984 original responses verified; manifest unchanged |
| First lesson proposals | Two histories reopened through revision 3; human review pending |

The frontier groups exact observed URL spellings with fragments removed; it does
not infer shared authorship, people or work identity. Earlier counts that retained
fragments use a different grouping rule and are not equivalent target counts.

## Backstory findings

The seven missing article slugs have current live counterparts on the linked
family blog, `hammy64000.com`. Their same-slug and displayed author metadata make
them candidates for review, not proof of identical historical wording on the seed
site. The old James series URL remains unresolved. A related current index,
`/series/the-epistle-of-james/`, was acquired as the eighth candidate; it is not
presented as the missing legacy index or a complete series archive.

Bounded exact-URL Internet Archive metadata queries returned no captures for the
eight seed targets. Related-host article queries returned 17 indexed captures;
their historical replay bodies were not fetched. Empty results do not establish
historical nonexistence. Query URLs, bounds and returned metadata are in
[availability.json](availability.json).

All eight selected bodies were represented successfully, but **none of the eight
original targets has been adjudicated as historically recovered**. Separate source
identifiers prevent candidates from replacing original wording or old citations.
The original eight 404 responses, media count discrepancy and Contact form drift
warning remain unchanged. Permission for the seed does not establish separate-host
redistribution or training rights; this capture records private source investigation.

The live capture preceded the final exact-host and parent-preflight timing fixes.
All saved final URLs match the sole explicitly permitted host, and the parent
manifest and original artifacts were independently revalidated. No historical
capture files were rewritten; regression tests cover both fixes.

## Lesson review

An independent model reviewer read the two primary passages and selected contextual
passages. Each revision-1 proposal received a revision-2 model assessment marked
`disputed`, followed by a revised revision-3 proposal. Original wording, critiques
and citation history remain intact.

- **Help without humiliation:** the revised proposal keeps clear correction and
  necessary limits, and explicitly protects questioning, declining and seeking
  another source. Two supporting and two contextual counter-citations remain.
- **Intentions and context:** the revised proposal separates dated, self-reported
  aims from competing interpretations, supported actions and effects. Three
  supporting and two contextual counter-citations remain.

Contextual tensions are not measured counterexamples. Outcomes remain predicted
checks, including adverse effects and privacy concerns; no empirical outcome study
has run. All six ledger revisions revalidated after reopen. Exact runtime model
identity was unavailable and is recorded that way. Model critique is not human
approval. There was no training or automatic pipeline model invocation.

## Validation and reproduction

[122 offline project tests](offline-tests.txt), [4 actual PDF parser tests](pdf-tests.txt)
and [Ruff](ruff.txt) pass. New regressions check exact hostname permission, parent
mutation before/during recovery, qualified source identities, failed and nontext
responses, and candidate qualification in every source read surface.

[Live-data acceptance](live-acceptance.json) records full hash verification, all
eight source-token round trips, exact lexical search citations and five authenticated
REST reads. HTTP checks used in-process ASGI: no listener, production index or
running MCP service changed. Runtime was Python 3.14.8 arm64 / SQLite 3.54.0;
the optional PDF regressions used the existing bundled pypdf 6.10.0 environment.
Hosted workflow coverage was extended but not executed here. Full daemon-changing
CI was not run for this standalone feature.

Use the commands and input schemas in the
[corpus guide](../../../../docs/lessons-corpus.md#investigate-reference-gaps).
Recovery and build require fresh private directories; verify, frontier and search
can be repeated. [Fingerprints](fingerprints.json) bind the captured candidates,
representation manifest, private review artifacts and this run's machine-readable
receipts. The parent ledger hash changed intentionally as reviews were appended;
the earlier receipt remains a historical checkpoint.

Next work is human review of the two proposals and selected historical/source
comparisons, followed by evaluated MCP/model integration. These storage checks do
not establish moral understanding, historical truth or improved model behavior.

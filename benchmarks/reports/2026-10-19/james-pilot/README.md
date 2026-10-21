# James pilot acceptance

October 19, 2026. One book, two identified editions, one attributed cross-reference
dataset and one investigation question. The [acceptance receipt](acceptance.json)
records exact archive/member/source hashes, acquisition metadata, source-code
fingerprints, commands, successful checks and failed attempts/corrections.

The [implementation guide](../../../../docs/james-pilot.md) describes the narrow
USFM adapter, source permissions, import command and review boundary. Source
originals, representation, relationships, ledger and readable review remain in a
new private capture outside Git. This report contains no full source bodies.

| Captured material | Verified result |
| --- | --- |
| eBible `engwebp` James | Five chapters, 108 verses; exact decoded USFM reconstruction. |
| eBible `eng-asv` James | Five chapters, 108 verses; separate edition identity and wording. |
| OpenBible reference archive | 344,799 original rows, 2,149 selected James-outbound rows. |
| Within-James targets | 146 rows resolve separately to extant addresses in each edition. |
| Other-book targets | 2,003 rows remain explicitly outside the pilot; no bodies fetched. |
| Investigation | Four explicitly model-reviewed findings; evidential/completion hypotheses remain disputed. |
| Tentative lesson | Proposed with exact finding dependencies; no human acceptance or release. |

The original eBible ZIPs also contain other books, which were not represented or
indexed. Archive/member hashes identify the captured export, not an independently
attested translation history. Word attributes, translator additions, notes and
paragraph/verse milestones retain source maps. Readable text transformations are
explicit. Reference rows retain original/subset character offsets, row identities,
votes and [OpenBible attribution](https://www.openbible.info/labs/cross-references/)
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The investigation asks whether James 2:14–26 presents actions as demonstrating
faith, completing it, or both, and what it establishes about practical help for
someone lacking food or clothing. Two English readings and edition wording
differences remain inspectable. These are model-authored hypotheses, not attributed
scholarly positions or a settled theological conclusion. No Greek/manuscript,
historical-event or measured-outcome investigation is claimed.

Actual-source acceptance verified:

- **216 verse text-span round trips** and exact original-token reconstruction.
- **2,149 reference-row round trips** with attributed, edition-aware mappings.
- Corpus integrity and source-bound search in fresh CLI processes.
- Refused release of the real pending lesson; no human decision was fabricated.
- In a disposable ledger copy, changing a finding's review blocked the unchanged
  lesson's packet while old history remained readable.
- Original real pilot ledger and corpus manifest hashes remained unchanged by
  these checks. The earlier private seed-site corpus and proposals were untouched.

The final import/representation/relationship/investigation command completed in
**18.711 seconds** on the observed ARM64 Python 3.14 runtime. This is a single
functional run, not a latency benchmark or evidence of an ARM optimization.

Validation: **230 tests passed, four optional PDF cases skipped in the project
runtime; those four passed in the existing bundled PDF runtime.** That verifies
234 distinct tests, including 40 new James/parser checks. Ruff and whitespace
checks passed. Independent review found a receipt reread race; the fix freezes
the exact parsed receipt bytes and has a regression test. Final review found no
blocking accuracy issues.

The first real import refused the Mac's `/var` symlink alias; a canonical-path
receipt resolved that without changing source bytes. The first full test run
also caught a fixture that recreated a ZIP with a new timestamp/hash; it now
restores the original bytes. Both failures remain in the receipt.

No running service, provider, MCP registration or operational index was changed.
The pilot command invokes no inference provider or training; its interpretations
were authored in the Codex session and labeled as model assessments. The next gate
is human review of this bounded question and its limits before adding another book
or treating the tentative lesson as approved evidence.

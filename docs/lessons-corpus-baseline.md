# Lessons corpus baseline

As of October 19, 2026. This document defines the product and acceptance baseline.
The [local corpus tooling](lessons-corpus.md) implements archival acquisition,
source-mapped ASTs, cited lexical retrieval, immutable lesson reviews and optional
authenticated REST reads. Offline contracts are verified; live acquisition and
representation results must be recorded in a separate receipt. This baseline does not claim a
complete world corpus, model training or demonstrated behavioral change.

The [first seed receipt](../benchmarks/reports/2026-10-14/lessons-corpus/README.md)
records the preserved material, verified representation and remaining acquisition gaps.

The [October 17 Free Man boundary](free-man.md) separates the research initiative
from Rest-proxy's operational role. Shared source and lesson contracts do not imply
shared credentials, private memory or authority to act. Superintelligence is the
user's research aspiration, not an established capability of this corpus.

## Purpose

The user's aspiration is to understand how knowledge becomes care: how people
and machines can learn from history, suffering, service, freedom and correction
to support human flourishing. The motivating question is how we can flourish
together without making another person's suffering the price of our happiness.
These are user-authored aims, open to interpretation and disagreement.

The philosophical conversation is preserved in a private conversation archive
held outside Git. This baseline interprets that direction; it does not replace
the original words or establish religious, historical or metaphysical claims.

The product is a versioned repository of sources and lessons. It should let a
reader inspect where a lesson came from, what its author meant, what consequences
were reported or observed, and where another interpretation may fit better.
Happiness, dignity, freedom and care should not collapse into one compulsory score.

## From narrative to inspectable records

The pipeline is:

```text
preserved original -> structural text AST -> attributed claims / values
                    -> investigation -> reviewed findings -> proposed lesson
                    -> lesson review and explicit release -> outcomes and revision
```

The ledger enforces the investigation prerequisite for new lesson proposals,
acceptance and release preparation. This checks exact reviewed dependencies; it
does not establish the quality or completeness of an investigation. The two
existing assistant-authored proposals remain pending legacy records. Neither
their citations nor a successful archive check establishes that the source
author's conclusions have been investigated.

- **Original:** acquired bytes, source identity, retrieval time, rights record,
  source revision and integrity hash. Preserve the original wording and order.
- **AST:** headings, paragraphs, quotations, lists, tables and link occurrences,
  with stable identifiers and resolvable source spans. Record parser version and
  derived-text transformations. This is text processed with code-like structure;
  a syntax node does not establish the truth of its contents.
- **Claim:** an attributed assertion with exact citations and its evidence type:
  observation, source claim or inference. Distinguish event time, publication time
  and acquisition time when supplied.
- **Value:** an attributed aspiration or judgment, with its context and competing
  perspectives. Values are not silently converted into observed facts.
- **Investigation:** a bounded question about an attributed claim, its premises,
  supporting and contrary evidence, alternative explanations, scope and gaps.
  Empirical, historical and causal claims need relevant independent evidence.
  Philosophical or theological interpretations need explicit premises, context
  and competing interpretations; empirical checks of a premise do not settle
  the entire interpretation. Related sources must be checked for shared origins
  before treating them as independent corroboration.
- **Finding:** a versioned assessment with exact evidence references, reasoning,
  limitations and review history. Supported within a stated scope, partly
  supported, contradicted and unresolved are possible assessments; a value or
  interpretation may require a different form of review. A reviewed finding is
  an attributed judgment, not a certificate of truth.
- **Lesson:** a proposed relationship between circumstances, choices and outcomes,
  derived from the reviewed investigation, with supporting evidence, limitations,
  counterexamples and interpretation history. A lesson may preserve an unresolved
  question or a failed reasoning method; it must retain the findings' uncertainty.
- **Review and outcome:** append decisions and reasons without overwriting sources.
  Keep reported consequences separate from measured consequences and causal inference.

The [evidence workspace](evidence-workspace.md) provides existing citation and
review patterns. Its bounded case store is not yet a world-scale corpus store.
Retrieval and AST ingestion supply inspectable material at inference time; adaptive
training requires a separate, explicit dataset, training and evaluation workflow.

## Investigation-to-lesson connection

The Free Man implementation reuses the ledger's existing
`experience`, `source_claim`, `value` and `hypothesis` records for investigation
findings. New version-2 lesson proposals require 1–32 dependencies on current,
reviewed non-lesson records in the same ledger. Each `investigation_refs` item
identifies `record_id`, exact `revision`, `revision_sha256` and a role of
`supports`, `challenges` or `context`. Findings retain the question/context,
supporting and contrary citations, assessment, scope
and uncertainty. A correction or changed review blocks dependent lesson acceptance,
packet preparation and release, even when the lesson's own revision is unchanged.
Earlier records and decisions remain readable; a new lesson revision must bind
the corrected finding and undergo review again.

Draft questions and hypotheses can remain provisional non-lesson records. New
lesson proposals already require reviewed findings. The connection checks those
dependencies again at acceptance and during review/release preparation. Findings
may be accepted, disputed or rejected; their actual assessment stays visible.
Review cannot turn missing evidence into support or erase a recorded disagreement. Lesson acceptance
and permission to release remain separate human decisions under the existing
[exchange contract](free-man.md#human-review-and-controlled-exchange).

Synthetic acceptance covers traceable proposals, refusal of unreviewed or stale
dependencies, disputed findings, correction and separate-process CLI reopen.
Historical version-1 records stay readable and unchanged; an old proposal needs
an explicit new version-2 save before acceptance or a new release. Disputed and
rejected reviews remain available for legacy or stale lessons. Historical
version-1 exchange bundles remain inspectable, with the prerequisite explicitly
unavailable. No existing private proposals were migrated or approved.

The evidence workspace can supply additional investigation findings through a later
versioned, data-only packet if needed. Its Markdown export omits rejected findings
and retired statements, so it is not a lossless machine connection. A future adapter
must preserve case/finding identity, revision, review history, source hashes and
coverage, and explicitly map line/image citations to the corpus citation contract.
No automatic acceptance, operational-memory import or training follows from this
design change.

## Source scope

The first seed is [21st Century Progressivism](https://21stcenturyprogressivism.com/),
which the owner has authorized for archiving. Preserve every word of acquired
in-scope pages and every reference-link occurrence, including labels, targets and
positions. Keep original HTML alongside derived readable text and AST records.
An archival permission record should identify its scope; redistribution and
training eligibility are separate fields.

A coverage manifest must identify the acquisition boundary and distinguish
acquired, failed, missing, excluded and external-reference sources. Reference
links supply background and remain part of the source graph. Expanding external
references is a planned acquisition stage with its own rights and completeness
record; listing a link does not mean its contents have been acquired or verified.

The [repository representation assessment](research-corpus-repositories.md) maps
Bible formats, typed relationships, cross-reference datasets, library access and
Spark's possible batch-processing role. These are implementation/source candidates,
not an imported Bible or a completed investigation.

Planned extensions include:

- **The Bible:** identify translation, edition and rights; preserve book, chapter,
  verse, headings, notes and cross-references with source attribution. Distinguish
  canonical text from translator notes and later interpretations.
- **Other traditions and historical sources:** preserve each source's vocabulary,
  context and viewpoint, including disagreements, rather than rewriting everything
  into one presumed doctrine.
- **Libraries and books:** record edition, authorship, provenance and permitted
  archival, redistribution and training uses. Library access alone is insufficient
  rights metadata. Preserve scans and label OCR as derived, uncertain transcription.

## Model tiers and evidence

Evaluate model tiers on shared tasks and source versions: faithful quotation,
contextual interpretation, treatment of disagreement, uncertainty, correction and
reasoning about conflicting interests. Record exact model/runtime identity,
prompts, citations, usage, review and outcomes when available.

Frontier models can contribute interpretations and critiques. Their size, tier or
eloquence does not establish moral authority or make them presumed keepers of
truth. Saying a lesson is understood is not evidence of sustained benevolent
behavior. Claims of improved understanding or outcomes require controlled evidence.
The corpus is intended to support learning and human review; those effects remain
to be evaluated.

## Private exercises

The personal investigation remains a private exercise for developing evidence
tools. It is separate from this seed corpus. No private case records, personal
names, excluded context or credentials enter Git or public lesson exports.
No outreach, accusations or punitive actions are authorized. Relationships and
participant statements do not independently establish identity or malicious intent.

## Priorities and acceptance

1. Preserve authorized originals, reference occurrences, rights and an honest
   coverage manifest before summarizing or inferring lessons.
2. Produce deterministic, source-bound AST records and bounded retrieval. Verify
   Unicode, wording, ordering, link occurrences, hashes and source-span resolution.
3. Investigate attributed claims and values, record reviewed findings, then derive
   provisional lessons with exact finding/evidence dependencies. Verify disagreement,
   amendment, dependency review and history survive save, reopen and export.
4. Extend the source graph deliberately to references, selected Bible editions,
   other traditions and library material with explicit rights and coverage.
5. Compare model tiers using reviewed tasks and measured outcomes. Keep training
   datasets and adaptive-learning claims separate from ingestion and retrieval.

Sources are data: embedded instructions must not invoke tools or change policy.
Acceptance must also verify that unacquired references remain visible as gaps,
private records stay excluded, and rejected mutations leave preserved state intact.
Publish exact acquisition counts, commands and test results only after verification.

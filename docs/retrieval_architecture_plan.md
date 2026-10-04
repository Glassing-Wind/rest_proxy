# Retrieval Architecture Plan

> **Archived planning record.** This document preserves the detailed extraction
> plan and completed phases. For current ownership and change guidance, use
> [Retrieval Architecture](retrieval_architecture.md). For the dated readiness
> snapshot, use [Tool Trust Status](tool_trust_status.md).

## Goal

Make retrieval first-in-class and keep it there.

The governing rule is:

> If two different frontends should get the same answer, the logic belongs below `rest_proxy`.

`rest_proxy` should be an orchestrator and presentation layer, not the owner of retrieval semantics.

Enterprise quality also includes maintainability.

If files become too large or too entangled for routine upkeep, the system is no longer enterprise-grade even if it still works. A practical rule is:

> If a file is getting too large for reliable maintenance, it is already too large.

Large files and overloaded modules make regressions easier, ownership less clear, and refactors more dangerous.


## Current Problem

The system has suffered from boundary drift:

- retrieval behavior that should be a product invariant lives in Python MCP code
- useful behavior can disappear during refactors because it is not owned by a lower-level contract
- docs and code retrieval quality depends too much on tool-layer heuristics
- topic families and ranking rules have been inconsistent enough to make indexed content appear "missing"

This is not primarily a Python-version problem. It is an ownership and regression-control problem.


## Architecture Rule

Keep logic in `rest_proxy` only when one of the following is true:

- it is transport/session/job orchestration
- it is MCP-specific presentation or formatting
- moving it deeper would clearly reduce retrieval quality or slow down iteration in a way that matters

Everything else should move below `rest_proxy`.

Maintainability constraints apply at every layer:

- modules should have narrow, defensible ownership
- files should stay small enough to review and refactor safely
- new retrieval behavior should prefer new focused modules over expanding already-large ones
- if a file becomes difficult to reason about end-to-end, split it before adding more behavior


## Target Layering

### 1. Retrieval Core

Owns deterministic retrieval behavior for docs and code.

Responsibilities:

- query normalization
- query expansion and synonym handling
- exact-match/error-code matching
- topic-family resolution
- scope/filter resolution
- ranking policy
- fallback policy
- diversification policy
- result explanation metadata

Example contract:

```python
search(
    query: str,
    corpus: str,
    scope: dict | None = None,
    mode: str = "default",
    limit: int = 8,
) -> SearchResult
```

Where `SearchResult` includes:

- ranked items
- score
- score breakdown
- matched terms
- source type
- scope applied
- fallback used


### 2. Ingest / Metadata Layer

Owns facts that retrieval should not guess at runtime.

Responsibilities:

- doc type classification
- canonical source typing
- topic family metadata
- code/index metadata contracts
- chunk metadata needed for retrieval
- semantic/index facts that should be stable across repos

For code indexing, this should bias strongly toward `ts-pack`.


### 3. `rest_proxy`

Owns orchestration and presentation only.

Responsibilities:

- MCP tool registration
- job lifecycle
- session lifecycle
- background subprocess/runtime orchestration
- result formatting
- admin/reporting wrappers

`rest_proxy` should call retrieval, not define its core behavior.


## What Must Move Deeper

### Docs Retrieval

- topic-family semantics such as `neo4j` => `neo4j*`
- source typing such as KB vs operations manual vs driver manual
- exact error-code boosts
- incident-query interpretation
- synonym/query expansion
- ranking policy
- diversification rules
- canonical-source preference rules

### Code Retrieval

- semantic + exact + fallback interplay
- winnowing/fingerprint fallback behavior
- diversification strategy
- path/language/repo scope semantics
- guarantees about when grep/exact fallback activates

### Indexing / Metadata

- semantic extraction facts
- export/public-surface facts
- test classification and explicit test metadata
- code relationships that should be stable across repos

These belong in `ts-pack` or a lower-level retrieval/indexing layer, not in MCP wrapper logic.


## What Can Stay In `rest_proxy`

- MCP tool wrappers
- user-facing summaries
- job status and runtime reporting
- background process launching
- session-scoped orchestration
- admin tools for indexing/crawling/cleanup


## Current Gaps

### Boundary Drift

- docs retrieval semantics still partly live in Python MCP code
- legacy path fallback remains only where current semantic metadata is absent or
  cannot exist yet (for example pre-index source eligibility); promoted tools use
  the same semantic role precedence
- a few query families still rely on `rest_proxy` policy where lower-level
  metadata is not yet rich enough

### Topic Governance

- topic taxonomy is organic rather than canonical
- related corpora can look missing because they live under sibling topics

### Regression Protection

- helper-level tests exist
- product-behavior tests are too sparse
- important retrieval guarantees are not pinned by golden tests


## Current Status

As of 2026-06-27, the architecture is materially closer to the target layering
than when this plan was first written.

### What Has Already Moved Deeper

- semantic file-surface roles now come from `ts-pack` instead of being guessed
  only in `rest_proxy`
- focused dispatcher-anchor metadata and contract capabilities now come from
  `ts-pack`
- routing/request-handler semantic roles now come from `ts-pack`
- Swift attributed extension classification, inherited protocol metadata, and
  resource-reference query extraction now come from the pinned `ts-pack` fork;
  Swift protocol call-chain conformers should be graph-owned when the repo has
  been rebuilt with the current native extension.
- semantic chunk identity and several indexing invariants were fixed below the
  MCP layer instead of being papered over in retrieval logic
- docs retrieval now has a reusable core in `memory/docs_retrieval.py`
- code retrieval now has a reusable core in `memory/code_retrieval.py`
- implementation retrieval policy has been split into focused `memory/`
  modules for metadata, surfaces, query parsing, intent, semantic metadata,
  query intent classification, rank-intent policy, scoring primitives,
  workflow-specific scoring, role/node rank policy, enrichment, duplicate
  handling, telemetry, and contracts

### Current Retrieval Module Ownership

- `memory/docs_retrieval.py`: docs query expansion, topic-family matching,
  filtering, and docs ranking.
- `memory/code_retrieval.py`: code retrieval orchestration across semantic,
  exact, fallback, path-hint, crate, and final ranking stages; shared
  `_enrich_rescue_rows` helper keeps all rescue-injection enrichment uniform.
- `memory/cross_project_trace.py`: cross-project symbol definition selection,
  semantic-consumer ranking, graph/semantic trace orchestration, and source
  preview centering for non-MCP callers and MCP wrappers.
- `memory/code_retrieval_loaders.py`: database read helpers for code retrieval
  rescue rows, path-hint rows, Cargo crate metadata, and Neo4j read
  transaction policy.
- `memory/code_retrieval_clone.py`: optional Neo4j-backed clone deduplication
  for code retrieval results, including debug-row insertion when requested.
- `memory/code_retrieval_postprocess.py`: duplicate-aware reranking, per-file
  dedupe, dispatcher/routing telemetry, and final implementation post-ranking.
- `memory/retrieval_metadata.py`: shared metadata coercion, filtering, cargo
  scope handling, and result rendering helpers.
- `memory/retrieval_surfaces.py`: docs/support/generated/usage-heavy surface
  classification and final implementation rank tuple.
- `memory/retrieval_query.py`: query-symbol, member-expression, declaration
  subject, and explicit path-hint parsing.
- `memory/retrieval_semantics.py`: file roles, chunk roles, node types, member
  usages, and result surface flags.
- `memory/retrieval_intent_queries.py`: implementation query classification,
  exact identifiers, inferred filename hints, generated-surface acceptance, and
  query-family predicates.
- `memory/retrieval_intent.py`: rank-intent weight policy, support-surface
  penalties, usage-surface bonuses, and compatibility exports for intent
  callers.
- `memory/retrieval_scoring.py`: path/symbol/API/dispatcher/routing scoring
  primitives and penalties, plus compatibility exports for scoring callers; shared
  `_count_definition_patterns` helper keeps regex matching consistent.
- `memory/retrieval_scoring_workflows.py`: dispatcher, command, provider,
  routing, request-handler, controller, server-infra, and view-body scoring
  policy.
- `memory/retrieval_rank_policy.py`: implementation result role classification
  plus role and node-type ranking policy.
- `memory/retrieval_enrichment.py`: implementation result enrichment, rank-score
  component assembly, ranking traces, and per-file dedupe.
- `memory/retrieval_duplicates.py`: near-duplicate analysis, collapse, and
  diversity reranking runtime.
- `memory/retrieval_telemetry.py`: duplicate, dispatcher, and routing telemetry
  contracts.
- `memory/retrieval_policy.py`: compatibility facade for historical
  `semantic_helpers` callers plus duplicate contract wrappers.

### What `rest_proxy` Now Mostly Does

- orchestration and health/alignment gating
- role-aware ranking using lower-level metadata
- query-family intent detection where product policy still belongs in the tool
  layer
- live telemetry, enterprise evals, parity checks, and user-facing formatting

### What Was Successfully Deleted Or Reduced

- stale-index compatibility as a silent default
- dispatcher rescue/reordering branches that were only compensating for weak
  ranking
- routing partition and additive routing bonus branches once semantic roles and
  structural ranking were strong enough
- many path-only test/example/docs/benchmark suppressions in retrieval,
  orientation, summaries, and code-intel paths
- conflicting support-role demotion across promoted tools: implementation now
  overrides coexisting docs/config/support roles, while generated and
  test/example/benchmark classifications remain deliberately low signal

### What Is Still Intentionally In `rest_proxy`

These are not obviously wrong to keep higher:

- product-level retrieval intent and ranking tradeoffs between structurally
  valid answers, now mostly isolated in focused `memory/` modules
- presentation decisions such as “Inspect First” wording or graph summary shape
- telemetry and enterprise-eval diagnostics

### What Is Still Not Clean Enough

- primary and secondary promoted tools now classify candidates through the same
  semantic-role-first precedence contract; remaining path-only compatibility is
  confined to smaller admin/debug surfaces and legacy records
- some query families may still need richer lower-level metadata if real usage
  finds another repeated weak spot
- `memory/retrieval_policy.py` is now a small facade; continue splitting only
  when another clean ownership boundary emerges instead of moving logic for its
  own sake


## What To Do Next

This plan should not be interpreted as “move everything down immediately.”
Current next steps should follow this order:

1. Use live MCP workflows and telemetry to find the next real hesitation.
2. If the issue is a durable structural fact, move it down into `ts-pack` or a
   lower-level index contract.
3. If the issue is a query/product tradeoff, keep it in `rest_proxy`.
4. Only add new goldens or workflow cases when they protect a real current
   boundary.


## Golden Behavior Tests

These tests should be treated as product contracts.

### Docs

- `neo4j` + deadlock/locking query returns locking KB/ops pages first
- `neo4j` + Python transaction query returns the canonical Python driver page first
- family topic lookup for `neo4j` includes `neo4j-python`, `neo4j-cypher`, `neo4j-apoc`, `neo4j-gds`
- polluted mirror/GitHub sources do not outrank canonical docs

### Code

- sparse exact-token query triggers exact fallback when semantic results collapse
- winnowing/duplicate-aware fallback improves file diversity
- repo/path/language scope behavior is deterministic
- same query from two frontends yields the same ordered result set

### Orchestration

- background code indexing uses the configured deterministic runtime
- background docs indexing uses the configured deterministic runtime
- MCP status exposes the actual runtime used


## Duplicate And Diversity Design

Duplicate collapse and diversity are not the same problem.

They should be handled in two layers:

1. candidate generation
2. retrieval-time selection

Candidate generation should answer:

- which results are likely near-duplicates?
- which results are exact duplicates?
- which results are likely mirrors or translated copies?

Retrieval-time selection should answer:

- which of those candidates should be suppressed?
- which representative should be kept?
- how much diversity should be enforced without dropping the best answer?

This split matters because:

- winnowing and similar fingerprint methods are good at generating candidates
- they are not, by themselves, a complete ranked retrieval policy
- docs and code need different retrieval-time policies even if they share lower-level primitives


## Lower-Level API

The lower-level retrieval stack should expose separate contracts for:

- duplicate candidate generation
- result reranking/diversification

## MCP Tool Surface

`rest_proxy` is now the single owner of duplicate-aware retrieval policy at the MCP layer.

Cross-repo callers should use:

- `rerank_retrieval_results`
- `analyze_duplicate_results`

`rerank_retrieval_results` accepts a caller-supplied ranked list and returns:

- a compact ordered path/index decision list by default
- kept and suppressed counts plus redundancy before/after
- duplicate-relation counts and regression alerts
- the full results, groups, pairs, selection metadata, and candidate trace only
  when `include_debug=true`

`analyze_duplicate_results` exposes duplicate decisions without changing order. Its
default output includes only detected duplicate pairs with path labels; use
`include_debug=true` for full group and pair internals.

Both tools expect each caller-supplied row to contain `content` and either
`file_path` or `source_url`. Relevance can be supplied as `rrf` or `rank_score`.
For code ranking QA, current semantic metadata belongs under `metadata`, notably
`file_roles`, `chunk_role`, `node_types`, and `file_symbols`.

This keeps duplicate-policy logic centralized in `rest_proxy` while allowing other repos to reuse it without reimplementing retrieval semantics locally.

For normal agent usage, documentation retrieval already applies this reranking by default through `search_documentation`. That default path returns the post-rerank final list and collapses repeated chunks from the same documentation URL. The standalone MCP tools remain for debugging, evaluation, and caller-supplied custom candidate lists.

### Duplicate Candidate API

This should live below `rest_proxy`.

Suggested contract:

```python
find_near_duplicates(
    items: list[DuplicateInput],
    corpus: str,
    mode: str = "retrieval",
) -> DuplicateAnalysis
```

Where `DuplicateInput` includes:

- `id`
- `text`
- `language` or `doc_type`
- `source_path` or `url`
- optional `metadata`

And `DuplicateAnalysis` includes:

- duplicate groups
- canonical candidate per group
- pairwise similarity details
- method used
- explanation fields

Example explanation fields:

- `exact_match`
- `fingerprint_overlap`
- `token_jaccard`
- `literal_token_jaccard`
- `literal_kgram_jaccard`
- `length_ratio`
- `source_equivalent`

This API should support corpus-specific modes:

- `code_retrieval`
- `docs_retrieval`
- `index_clone_detection`

Those modes should share primitives where possible, but not force one threshold set to fit every use case.


### Retrieval Reranker API

This should also live below `rest_proxy`.

Suggested contract:

```python
rerank_results(
    items: list[RankedResult],
    corpus: str,
    mode: str = "default",
    duplicate_analysis: DuplicateAnalysis | None = None,
) -> RerankedResult
```

Where `RerankedResult` includes:

- final ordered results
- suppressed result ids
- representative selection per duplicate group
- diversification decisions
- score breakdown
- fallback/diversification explanation

This layer should own:

- representative selection inside duplicate groups
- exact duplicate suppression
- diversity-aware reranking
- conservative retention of the best answer


## Algorithm Direction

### Code Retrieval

Use a two-stage approach:

1. Rust candidate generation using winnowing and token/k-gram signals
2. retrieval-time reranking with conservative duplicate suppression

The current Rust primitive is the right place to start, but retrieval mode should stay separate from index-time clone grouping.

Recommended signals for code retrieval candidate generation:

- normalized-token winnowing for renamed-clone recall
- literal-token overlap as a guardrail
- literal k-gram overlap as a stronger sequence guardrail
- length-ratio checks

Recommended reranking rules for code retrieval:

- always preserve the highest-ranked result in a duplicate group
- suppress only when the second result is redundant enough to reduce user value
- prefer file diversity after exact redundancy is handled
- never let diversification demote the clearly best single answer below weaker alternatives


### Docs Retrieval

Docs should not reuse code-style identifier normalization as the primary signal.

Recommended candidate generation for docs:

- shingle-based MinHash or equivalent approximate Jaccard candidate generation
- canonical URL and canonical-source equivalence
- mirror/translation detection
- heading/title/url similarity

Recommended reranking rules for docs:

- prefer canonical sources over mirrors
- collapse translated or mirrored copies under the canonical page
- prefer the most exact title/heading match inside a duplicate family
- apply incident/reference/how-to intent before diversity


### Diversity Reranking

For both corpora, retrieval should include an explicit diversity-aware reranker.

MMR is an acceptable baseline.

But the contract should not be phrased as “run MMR.”

The contract should be:

- preserve the best answer
- reduce redundant near-duplicates
- improve useful breadth when the top results collapse to one file or one concept

Implementation can start with MMR and later improve without changing the product contract.


## Golden Tests For Duplicate Collapse And Diversity

These tests should be treated as release gates for retrieval changes.

### Code Golden Tests

- exact duplicate chunks collapse to one representative
- renamed clones with the same structure collapse only when they do not remove useful distinct coverage
- similar but behaviorally different snippets are both kept
- top-ranked best answer is preserved after diversification
- when top results all come from one file, diversification can add breadth without ejecting the best hit
- same result set from two frontends yields the same kept/suppressed decisions

### Docs Golden Tests

- exact mirror pages collapse to the canonical URL
- translated or mirrored pages do not outrank the canonical page
- exact title/heading match beats generic manual pages
- incident queries prefer KB/ops pages over broad reference pages
- family-topic queries still diversify across relevant doc subtypes when appropriate

### Negative Tests

- diversification must not hide the single best answer
- duplicate collapse must not merge results solely because identifiers were normalized away
- docs collapse must not merge distinct conceptual pages from the same domain


## Immediate Next Steps

1. Keep MCP wrappers thin: new search behavior should enter through
   `memory/docs_retrieval.py`, `memory/code_retrieval.py`, or a focused
   `memory/retrieval_*` module.
2. Keep `memory/retrieval_policy.py` stable as a compatibility facade unless
   another extracted unit has a clear product boundary and preserves existing
   callers.
3. Use live workflow misses to decide whether a weakness belongs in
   `ts-pack` metadata, retrieval scoring, retrieval intent, or presentation.
4. Keep standard-gate coverage aligned with promoted tool behavior and avoid
   adding historical one-off cases that do not protect current trust.
5. Update this document and `docs/tool_trust_status.md` whenever a module
   changes ownership or a promoted MCP workflow changes its trust level.


## Recommended Implementation Order

### Phase 1. Freeze Current Good Behavior

- add golden tests in `rest_proxy`
- lock current desired retrieval behavior before moving code

### Phase 2. Extract Docs Retrieval Core

- move docs ranking/query-expansion/topic-family logic below `rest_proxy`
- leave only MCP wrappers and formatting in this repo

### Phase 3. Extract Code Retrieval Core

- move semantic/exact/winnowing/fallback policy below `rest_proxy`
- make MCP code search tools thin callers of that contract

### Phase 4. Strengthen Ingest Metadata

- reduce runtime heuristics by improving lower-level metadata
- keep semantic/index ownership in `ts-pack` whenever possible

### Phase 5. Delete Duplicate Tool-Layer Logic

- keep duplicate analysis and diversity policy in `memory/retrieval_duplicates.py`
- keep MCP duplicate tools as contract/presentation wrappers
- avoid reintroducing duplicate heuristics in tool-specific search code


## File-Level Direction

### Move Deeper

- remaining durable structural facts that retrieval still infers at runtime
- repeated path-only compatibility branches proven by live usage to represent
  missing lower-level metadata
- any new docs/code ranking behavior that two frontends should share

### Keep In `rest_proxy`

- `tools/brain/docs/index_jobs.py`
- `tools/brain/docs/research.py`
- `tools/brain/docs/admin.py`
- `tools/hands/indexing.py`

But only for orchestration and reporting, not retrieval semantics.


## Non-Goals

- do not move logic deeper if it clearly degrades answer quality
- do not recreate retrieval behavior in multiple wrappers
- do not let Python-side MCP refactors silently redefine retrieval semantics
- do not keep growing oversized files just because the code still technically works


## Working Standard

Use this decision rule going forward:

- if two frontends should get the same answer, move the logic below `rest_proxy`
- if the logic only changes how the answer is displayed, it may stay in `rest_proxy`

This is the boundary that should keep retrieval quality high without making the orchestrator repo the owner of core semantics.

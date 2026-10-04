# Duplicate-Aware Retrieval Reranker

## Purpose

This document explains:

- how the duplicate-aware retrieval reranker is used today
- what the best version of this system should look like
- where the current implementation still falls short of that target

This is a product-behavior document, not just an implementation note.

It should be read alongside:

- [Retrieval suppression policy](retrieval_suppression_policy.md)
- [Retrieval architecture](retrieval_architecture.md)


## What This System Is For

The duplicate-aware reranker exists to improve top-k usefulness, not just to remove duplicates.

Its job is to:

- reduce repeated results
- cluster near-duplicate candidates
- choose useful representatives
- preserve multiple related chunks when the query needs them
- keep the best answer while reducing crowding

Its job is not to be the primary mechanism for:

- deciding the best API entrypoint
- preferring a library definition over a CLI caller
- deciding whether a query is definition-oriented, implementation-oriented, or usage-oriented

Those are ranking and query-intent problems, not duplicate-suppression problems.


## Current Usage

### MCP Tool Surface

The current public MCP tools are:

- `rerank_retrieval_results`
- `analyze_duplicate_results`

These are the intended cross-repo integration surface for duplicate-aware selection logic.

### Search Flow In `search_codebase`

For code retrieval today, the effective flow is:

1. Retrieve candidates from Postgres hybrid search.
2. Apply implementation-intent ranking features in `rest_proxy`.
3. Optionally inject exact-definition rescue candidates for definition-oriented queries.
4. Pass candidates into the duplicate-aware reranker.
5. Apply duplicate-aware grouping / representative selection / suppression.
6. Re-apply final query-intent ranking after dedupe.
7. Cap per file / per directory and render the result set.

This sequencing is deliberate.

Duplicate handling and query-intent ranking are related, but they are not the same decision.

### Important Current Settings

The current environment example from active usage is:

```env
LM_PROXY_DUPLICATION_SAME_FILE_MIN_TOKENS=20
LM_PROXY_DUPLICATION_MAX_SAME_FILE_PAIRS_PER_FILE=8

LM_PROXY_CLONE_DEDUP=1
LM_PROXY_CLONE_NEAR_EDGE=0
LM_PROXY_CLONE_DEBUG=0
LM_PROXY_FILE_CLONE_SOURCE=hybrid
```

These affect candidate generation and clone-aware file dedupe, but they do not replace the higher-level reranking policy.

### Current Default Suppression Posture

The current default remains conservative:

- exact duplicates are always suppressible
- `stage2` is the default rollout
- broader non-exact suppression remains narrow and gated

That means the reranker should usually cluster and diversify non-exact near-duplicates before it suppresses them.


## What The Best Version Should Be

The best version of the system should have eight clear properties.

### 1. Duplicate Evidence Layer

The lower layer should compute evidence, not make broad product decisions.

It should identify:

- exact duplicates
- normalized duplicates
- lexical near-duplicates
- structural clones
- boilerplate/template variants
- similar but distinct results

And expose pair/group features such as:

- exact match
- normalized match
- token overlap
- shingle overlap
- fingerprint overlap
- structure overlap
- symbol overlap
- role match
- length ratio
- boilerplate score

### 2. Duplicate Groups

Near-duplicate candidates should be grouped into connected components or families.

The system should know:

- which results are interchangeable
- which are closely related
- which are similar but still query-distinct

### 3. Representative-Per-Group Selection

Within each duplicate group, representative choice should consider:

- query relevance
- role priority
- completeness
- source/path preference
- aspect coverage
- query distinction

More than one member should survive when the query benefits from it.

### 4. Exact-Only Hard Default Suppression

The hard default should still be:

- exact duplicate suppression

Non-exact evidence should primarily drive:

- grouping
- penalties
- diversification
- narrow gated suppression only when proven safe

### 5. Query-Aware Diversification

The objective should be:

> best relevant, non-repetitive, query-useful top-k

That means:

- start from base relevance
- apply redundancy penalties against already selected results
- add aspect-coverage bonuses
- prefer good representatives
- preserve multiple related chunks when they answer different parts of the query

### 6. Mode-Aware Behavior

The system should have separate policies for:

- code queries
- docs queries
- hybrid queries

For code it should weigh:

- symbol overlap
- structure
- role
- module/path context
- API vs helper distinction

For docs it should weigh:

- canonical source
- versioning
- mirrors
- headings/section role
- tutorial vs reference distinction

### 7. Explainability

For each candidate, it should be easy to inspect:

- base relevance
- duplicate group
- redundancy penalty
- aspect gain
- role bonus
- representative reason
- keep/suppress reason
- who beat it and why

### 8. Evaluation And Telemetry

The best version should have:

- goldens
- offline evaluation
- trace/debug mode
- telemetry for grouping, suppression, representative choice, repetition reduction, and answer retention


## Where The System Is Today

The current system is materially better than a naive dedupe pass.

### What Is Already In Place

- duplicate-aware reranker tools exist at the MCP surface
- exact duplicate suppression exists
- rollout staging exists (`stage1` / `stage2` / `stage3`)
- non-exact evidence is already used for grouping and representative selection
- trace/debug output exists
- telemetry exists
- goldens already exist in:
  - [retrieval duplicate goldens](../benchmarks/retrieval_duplicate_goldens.json)
- `search_codebase` now has definition-oriented rescue and post-dedupe final ranking
- implementation-intent ranking is now separated from duplicate suppression

### What Was Recently Fixed

Recent fixes made the current behavior more correct:

- implementation-intent queries are now classified better
- generated/parser payloads no longer dominate obvious code-intent queries
- binding-surface wrappers are demoted for internal implementation queries
- definition-oriented queries can rescue likely defining files
- the duplicate reranker now receives role-aware relevance via `rank_score`, not just raw `rrf`
- after dedupe, final ordering is re-applied using query-intent ranking instead of leaving duplicate-group order as the final answer

These changes fixed failures such as:

- retry-related queries preferring payload helpers over real writer code
- import-resolution queries preferring docs/generated files over resolver code
- definition-oriented queries failing to surface the library root definition


## Current Gaps Versus The Best Version

The current system still falls short of the best version in several important ways.

### 1. Too Much Query-Intent Logic Still Lives In `rest_proxy`

Today:

- implementation-intent classification
- definition-oriented rescue
- role-aware penalties/boosts

still live in Python MCP search code.

Target:

- more of this should move into a lower-level retrieval contract so different frontends get the same answer by default

Gap:

- retrieval semantics are still too tool-layer dependent

### 2. Query Classes Are Still Coarse

Today:

- `definition_oriented`
- `usage_oriented`
- generic implementation intent

Target:

- symbol lookup
- API-definition lookup
- implementation search
- conceptual “how does X work” explanation
- docs navigation
- versioned docs queries

Gap:

- some queries are still treated too generically
- “how does X work” and “where is X defined” overlap, but are not identical

### 3. Duplicate Groups Exist, But Product Semantics Are Not Fully Owned Below `rest_proxy`

Today:

- duplicate grouping and representative selection are available
- but the final answer quality still depends on Python-side role/rank adjustments

Target:

- duplicate-aware grouping below
- representative selection below
- stable ranking semantics below
- `rest_proxy` primarily orchestrates and formats

Gap:

- boundary drift remains

### 4. Role Hierarchy Is Still Partial

Today there is a practical code-role hierarchy:

- parser payload / binding surface / docs demotion
- definition hit boost
- library-root/API-entrypoint boost
- usage-heavy penalty for definition-oriented queries

Target hierarchy should be clearer and broader:

- public API definition / export
- canonical definition
- implementation entrypoint
- internal implementation
- usage/call site
- tests/examples
- generated/boilerplate

Gap:

- the current role model is still heuristic and incomplete
- it is not yet a first-class structured feature set

### 5. Rescue Paths Are Still Narrow And Tactical

Today:

- exact-definition grep rescue exists for definition-oriented queries

Target:

- structured symbol-definition retrieval should be part of normal candidate generation

Gap:

- rescue is useful, but it is still a patch over candidate recall weakness

### 6. Goldens Need Broader Query-Class Coverage

Today the goldens cover:

- duplicate suppression behavior
- docs canonicalization
- mirror handling
- some code duplicate cases
- newly added definition-oriented code ranking cases

Target:

- query-class breakout coverage across:
  - symbol lookup
  - API-definition lookup
  - implementation search
  - conceptual explanation
  - docs navigation
  - version-sensitive docs

Gap:

- evaluation is improving, but still not broad enough to fully protect query-intent behavior

### 7. Explainability Is Good, But Not Yet Complete

Today debug traces already show useful information:

- suppression policy
- kept/suppressed indices
- candidate group information
- query class from lower-layer trace

Target:

- every result should surface a fuller breakdown of:
  - base relevance
  - role boosts
  - redundancy penalties
  - aspect coverage gain
  - representative reason

Gap:

- current debugging is good for engineering work, but still not a full score-explanation surface


## Practical Interpretation

The current system should be understood like this:

- duplicate reranking is doing useful work already
- exact-only hard suppression remains correct as the default-safe posture
- the main remaining quality problems are increasingly ranking and retrieval-intent problems, not duplicate-threshold problems

That means future work should usually start with this question:

> Is this failure caused by duplicate collapse, or by query-intent/ranking?

If the answer is ranking, do not tune duplicate thresholds first.


## Recommended Next Improvements

In order:

1. Expand golden coverage by query class.
2. Promote role hierarchy into a more explicit feature model.
3. Move more ranking semantics below `rest_proxy`.
4. Replace tactical rescue paths with stronger symbol-definition candidate generation.
5. Add richer score breakdown/debug output.
6. Keep broader non-exact suppression gated until goldens and telemetry justify promotion.


## Current Bottom Line

The duplicate-aware reranker is already useful and materially better than exact-only dedupe.

But the best version is not just:

> better duplicate suppression

It is:

> a query-aware, explainable, mode-aware retrieval system that uses duplicate evidence as one feature family among several, while keeping answer quality and diversity ahead of simple collapse.

That is the direction the current implementation should keep moving toward.

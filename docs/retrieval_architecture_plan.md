# Retrieval Architecture Plan

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

- docs retrieval semantics still live in Python MCP code
- code retrieval contract is not strong enough to prevent fallback regressions

### Topic Governance

- topic taxonomy is organic rather than canonical
- related corpora can look missing because they live under sibling topics

### Regression Protection

- helper-level tests exist
- product-behavior tests are too sparse
- important retrieval guarantees are not pinned by golden tests


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

- remove transitional Python heuristics once parity is proven
- keep one retrieval policy, not several competing ones


## File-Level Direction

### Move Deeper

- `tools/brain/docs/search.py`
  Most retrieval semantics.
- parts of `tools/brain/docs/config.py`
  Topic families, ranking/source policy.
- code retrieval fallback logic currently spread around search layers.

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

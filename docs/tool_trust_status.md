# Tool Trust Status

This document records the current trust goal for the MCP/code-investigation
tools, where the repo stands now, and what work is still worth doing.

## Goal

The working standard is:

- every tool we would realistically reach for first should feel trustworthy
- the live MCP daemon should behave like direct invocation
- the standard gate should catch regressions that break real investigation
  workflows, not just unit-level correctness

“Trustworthy” here means:

- the tool usually picks the right first investigative surface
- the output is useful enough to keep moving without immediate double-checking
- transport/runtime drift does not silently invalidate the result
- obvious low-signal files do not outrank the real implementation surface

## Current Position

As of 2026-06-23, the repo is past blanket enterprise-hardening work and past
the biggest retrieval-boundary cleanup pass. The main investigation surfaces
are now on a stable, semantic-role-first baseline.

What is in good shape now:

- Tier 1 investigation tools have direct unit/script coverage and meaningful
  live or MCP-parity coverage where it matters most.
- Promoted Tier 2 tools have enough direct and live coverage to be trusted for
  normal investigation work.
- MCP daemon/runtime reliability is materially better:
  - stale-daemon fingerprint drift is detected
  - transport-runtime source drift is included in freshness identity, not only
    registered tool implementation drift
  - direct-run scripts self-heal into the expected `lmproxy` runtime
  - parity/live wrappers normalize interpreter selection
  - current/compat protocol negotiation, restart recovery, and opt-in MCP roots
    sync are exercised through the standard trust gate
- CI/install plumbing is stronger:
  - ts-pack wheel reuse is in place
  - dependency audit issues were fixed
  - job-state persistence races were addressed and regression-tested
- stale semantic indexes are no longer silently tolerated:
  - current semantic-contract coverage is required for healthy code retrieval
  - the validated benchmark repos have been refreshed onto the current contract
  - incremental indexing refreshes stale metadata for unchanged chunks without
    re-embedding their content
- semantic file-role metadata is now a real cross-layer contract:
  - `ts-pack` emits durable `file_roles` and focused dispatcher/routing facts
  - semantic ingest promotes those roles onto graph `File` nodes
  - retrieval, orientation, summaries, code-intel, and cross-project helpers
    now prefer role metadata before path heuristics
- dispatcher and routing retrieval are materially cleaner:
  - focused dispatcher-anchor contract and live telemetry exist
  - dispatcher rescue/reordering logic was largely deleted after ranking and
    lower-level metadata became strong enough
  - routing partition and additive routing bonuses were removed after
    role-aware ranking made them redundant
- telemetry/reporting is in better shape:
  - dispatcher and routing enterprise summaries are current-first
  - raw telemetry retention is bounded instead of append-only

What recent live-workflow hardening improved:

- `search_codebase`
  - better implementation/explanation ranking
  - better provider-wiring behavior
  - better gRPC-routing behavior
  - stronger dispatcher and routing contract reporting
- `get_project_overview`
  - better first-stop ranking for Java/Spring and Apple repos
  - lower promotion of generated files and shell/setup noise
- `get_directory_snapshot`
  - test files no longer outrank production files in code directories
  - cross-language headers and Apple support/resource files are demoted in code
    directory consumers/dependencies
- `get_related_files`
  - stronger structural preference
  - better Apple/workspace/project behavior
  - no fake “Inspect First” when Apple project-file context only yields
    low-signal scheme/resource neighbors
- `get_symbol_context`
  - better ambiguity handling
  - less cross-language graph noise
  - better call ordering and less same-file clutter
- `get_code_communities`
  - root-level test and benchmark basenames now receive lower-level ts-pack
    file roles instead of leaking into implementation clusters
  - live clustered-repo orientation starts from implementation files and is
    positioned as a follow-up to `get_project_overview`
  - clusters without a specialized API/UI/data/CLI role use stable dominant
    implementation areas instead of collapsing most output into `mixed`
- `get_app_flow_summary`
  - empty results now distinguish absent UI source surfaces from missing
    cross-layer graph edges and recommend the best available flow fallback
  - a positive indexed `rental` workflow now validates concrete UI-to-route/API
    paths through live regression and MCP parity
  - deeper service/database hops remain conditional on unambiguous graph
    attribution instead of presenting file-level joins as route-level truth

What this means in practice:

- the retrieval quality gate is green from the current baseline
- the live MCP daemon and direct invocation are aligned on the validated repo
  corpus
- most remaining low-signal demotion logic is now legacy-only fallback for
  rows that genuinely do not have current metadata
- semantic contract v4 distinguishes implementation-bearing files under
  support-oriented directories such as `tools/`; current semantic roles now
  override path fallback in ranking, remove false declaration anchors from
  return/throw statements, and forces older rows through incremental refresh
- role precedence is now consistent across semantic result classification,
  project overview, graph flow/utility helpers, symbol context/references,
  import summaries, and cross-project tracing: `implementation_surface`
  overrides coexisting docs/config/support roles, while explicit
  generated/test/example/benchmark roles remain low signal
- generated filename markers remain a deliberate conservative safeguard for
  generated artifacts even when a current record carries an empty role list;
  other docs/test/example path classification is legacy-only fallback
- retrieval QA tools now default to decision-sized output and expose their full
  forensic contracts only with `include_debug=true`
- every `search_codebase` retrieval branch now uses one role-first surface
  classifier for docs, parser/config data, generated bindings, support files,
  tests, and examples; path heuristics are reserved for legacy rows without
  semantic roles, including exact/path/definition/member-usage rescue results
- the registered MCP surface now has an explicit tool-choice catalog contract:
  every registered tool has a documented "reach for this when..." use case, and
  `get_mcp_tool_catalog` exposes that guidance inside the MCP surface

## What The Standard Gate Covers Well

The current standard trust gate is good at catching:

- tool-family selection regressions
- missing product-positioning metadata for newly registered MCP tools
- MCP transport/direct parity drift
- stale runtime/daemon behavior
- benchmark-repo workflow regressions for the promoted investigation tools
- Apple/Xcode orientation regressions
- ranking mistakes where low-signal files beat real implementation files

This means the main risk is no longer “we forgot to test the obvious tool
surface.” The main risk is now workflow usefulness on specific repo/query
shapes.

## What Still Needs Work

The remaining work should stay targeted. The next value is no longer in broad
hardening. It is in finishing smaller contract-boundary cleanup and only adding
new lower-level metadata when live usage proves the current contract is still
too weak.

Current remaining goals:

- continue MCP-only workflow passes across real repos and log the next actual
  trust hesitation instead of preemptive churn
- add deeper metadata only for the next proven weak family, rather than adding
  more top-layer ranking rules
- keep live goldens and enterprise artifacts aligned with honest behavior when
  tools become more precise or less overconfident

Latest workflow finding closed:

- `trace_symbol_cross_project` is now promoted into the live and MCP parity
  trust set; candidate retrieval ranks the full bounded exact/semantic window
  before truncation, symbol previews center on the requested definition, and a
  live tree-sitter-language-pack to rest_proxy case protects implementation-side
  consumer discovery
- `get_indexed_projects` no longer queries the retired Neo4j `Project.source_url`
  property, eliminating a DBMS warning on an otherwise healthy operator call
- `get_indexing_health(audit=True)` now measures the active import graph
  contract (`IMPORTS` and `IMPORTS_SYMBOL`) instead of the retired
  `RESOLVES_TO` relation, and STDIO startup no longer injects raw JSON-RPC
  notifications beside the SDK transport writer
- `find_code_duplication` now collapses overlapping sliding-window matches and
  explains candidates with declaration-level anchors, avoiding misleading
  `continue`/path-setup previews and generic registration-wrapper findings
- fresh MCP processes now inspect Neo4j constraint/index signatures before
  schema bootstrap, issue no DDL when the schema is current, and create only
  genuinely missing objects; equivalent legacy constraint names and Neo4j
  uniqueness type variants are accepted
- known-file and change-review tools now have MCP parity coverage for
  `describe_file`, `extract_function_body`, `extract_class_interface`,
  `find_symbol_usages`, `get_changed_symbols`, and `get_test_coverage_for`;
  `extract_function_body` also has a regression for its graph fallback path
- `get_mcp_tool_catalog` now handles natural intent wording such as "learn
  library docs", "remember repo fact", and "precommit test coverage" instead
  of requiring exact internal workflow taxonomy terms
- tool-choice/product-shape drift is now covered for overlapping discovery
  tools: generic repo onboarding must not start with `get_code_importance` or
  `get_code_communities`, while natural catalog intents for architecture
  onboarding, blast radius, clustered architecture, and symbol definitions route
  to the intended surfaces
- `get_mcp_tool_catalog` now also recognizes common action phrasing for support
  workflows, including jump/go-to-definition, ranking-debug, duplicate search
  results, and pre-commit changed-code review prompts
- flow-oriented catalog routing now distinguishes full-stack UI/API/service/DB
  wording from backend request-to-database wording, keeping the experimental
  app-flow tool behind explicit full-stack intent
- route/controller/handler catalog wording now routes framework-neutral and
  common framework prompts to `search_codebase`, matching the existing
  route-aware ranking contract instead of returning no catalog match
- `get_app_flow_summary` is now promoted into the secondary trust set with a
  positive `rental` live/MCP parity case; its honest product contract is
  UI-to-route/API tracing with deeper hops only when attribution is concrete
- documentation, memory, and git-state read tools are now part of MCP parity
  smoke coverage; the direct parity harness loads `.env` like the daemon and
  registers the same docs/memory tool families
- retrieval-QA support tools now have MCP parity coverage for their compact
  default contracts: caller-supplied reranking, duplicate analysis, and code
  ranking traces must stay direct/MCP equivalent and decision-sized
- durable-memory reads no longer reference optional Neo4j metadata properties
  directly, avoiding DBMS warnings on older memory nodes that predate tags,
  categories, or importance
- memory semantic retrieval now treats a missing optional
  `memory_embeddings_vector` Neo4j index as a clean fallback instead of logging
  an error before assembling working memory from durable instructions

Current likely targets:

- the next lower-level routing/handler metadata pass if live usage still finds
  repo families where controller/route surfaces are under-described
- additional workflow cases only when they protect a current trust boundary

## What Is Not Worth Doing Blindly

These are not the best next use of time unless live usage exposes a real issue:

- adding equal live/parity coverage to every admin or specialty tool
- more generic CI/plumbing churn without a concrete failure
- broad ranking rewrites without a repo-backed workflow failure
- reintroducing top-layer rescues that the lower-level contract already made
  unnecessary
- turning low-signal Apple/build metadata into fake “first stop” guidance just
  because the graph can return it

## Operating Guidance

When deciding what to do next:

1. Run a short real workflow through the actual MCP daemon.
2. Note the first place the tool feels hesitant, misleading, or overconfident.
3. Fix that exact failure.
4. Add the smallest regression that protects it.
5. Stop when the workflow feels trustworthy again.

That is the phase the repo is in now.

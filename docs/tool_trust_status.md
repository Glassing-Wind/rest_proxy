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

The repo is past blanket enterprise-hardening work and into workflow-driven
trust work.

What is in good shape now:

- Tier 1 investigation tools have direct unit/script coverage and meaningful
  live or MCP-parity coverage where it matters most.
- Promoted Tier 2 tools have enough direct and live coverage to be trusted for
  normal investigation work.
- MCP daemon/runtime reliability is materially better:
  - stale-daemon fingerprint drift is detected
  - direct-run scripts self-heal into the expected `lmproxy` runtime
  - parity/live wrappers normalize interpreter selection
- CI/install plumbing is stronger:
  - ts-pack wheel reuse is in place
  - dependency audit issues were fixed
  - job-state persistence races were addressed and regression-tested

What recent live-workflow hardening improved:

- `search_codebase`
  - better implementation/explanation ranking
  - better provider-wiring behavior
  - better gRPC-routing behavior
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

## What The Standard Gate Covers Well

The current standard trust gate is good at catching:

- tool-family selection regressions
- MCP transport/direct parity drift
- stale runtime/daemon behavior
- benchmark-repo workflow regressions for the promoted investigation tools
- Apple/Xcode orientation regressions
- ranking mistakes where low-signal files beat real implementation files

This means the main risk is no longer “we forgot to test the obvious tool
surface.” The main risk is now workflow usefulness on specific repo/query
shapes.

## What Still Needs Work

The remaining work should stay targeted. The next value is in fixing the next
real hesitation that shows up in live MCP use, not in trying to give every tool
the same level of investment.

Current remaining goals:

- continue MCP-only workflow passes across real repos and keep logging the next
  trust break
- improve search/result usefulness where the first 1-3 hits are still only
  “technically correct” instead of clearly helpful
- keep reducing overconfident output where the graph only supports weak context
- keep live goldens aligned with improved behavior when a change makes the tool
  more honest rather than merely different

Current likely targets:

- more repo-specific ranking polish for search and adjacency on query shapes not
  yet covered by the current workflow set
- additional workflow cases only when they protect a current trust boundary
- selective promotion of secondary tools if real usage proves they matter

## What Is Not Worth Doing Blindly

These are not the best next use of time unless live usage exposes a real issue:

- adding equal live/parity coverage to every admin or specialty tool
- more generic CI/plumbing churn without a concrete failure
- broad ranking rewrites without a repo-backed workflow failure
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

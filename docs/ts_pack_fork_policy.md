# ts-pack Fork Policy

`rest_proxy` currently depends on the forked `tree_sitter_language_pack` build,
not upstream `kreuzberg-dev/tree-sitter-language-pack`.

## Why the fork exists

- `rest_proxy` depends on GraphRAG-specific behavior that is not upstream:
  - Neo4j database selection for structural writes
  - native semantic ingestion controls
  - graph fidelity and write-stability fixes
  - declared API-route materialization, including Spring mapping annotations,
    even when no indexed client call references the route
- We intentionally pin an exact git commit in `requirements.txt` and
  `requirements-ci.txt` so production, local testing, and CI all resolve the
  same Rust/Python binding behavior.

## Update rules

1. Land the Rust/Python binding change in the fork first.
2. Push the fork commit.
3. Repin `requirements.txt` and `requirements-ci.txt` to that exact commit.
4. Rebuild local bindings with `maturin develop` when testing native behavior.
5. Run the targeted indexing and live-graph checks before widening rollout.

## CI strategy

- CI builds the pinned ts-pack wheel once per workflow run.
- Downstream jobs install that wheel artifact and then install the remainder of
  `requirements-ci.txt` with the ts-pack VCS line filtered out.
- This keeps the dependency pinned while avoiding three separate Rust builds in
  `python`, `enterprise-eval`, and `enterprise-live-graph-smoke`.

## What not to do

- Do not switch CI back to rebuilding the editable VCS dependency in every job.
- Do not point `requirements*.txt` at upstream unless the fork-only behavior is
  intentionally removed or upstreamed first.
- Do not assume a local editable install proves CI parity unless the pinned
  commit matches the requirement files.

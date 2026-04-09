# Retrieval Suppression Policy

## Current Default

The current retrieval policy is intentionally conservative.

- exact duplicates are the only hard default suppression
- non-exact duplicate evidence influences grouping and reranking
- non-exact duplicate evidence does not become global suppression by default

This is deliberate. Pairwise near-duplicate signals are useful evidence, but they are not reliable enough on their own to justify dropping results globally.

## Why Conservative

The system is optimized to avoid false suppression of query-distinct results.

That means:

- renamed clones should usually cluster before they suppress
- same-skeleton but different-semantics code should survive
- public API and definition chunks should be preserved more aggressively than helper-like chunks
- docs mirrors should prefer canonical sources, but version-distinct content should survive when the query implies a version

## Boundary Rule

Duplicate evidence, duplicate groups, and diverse selection belong below `rest_proxy`.

`rest_proxy` may call that lower-level contract and render the results, but it should not own suppression semantics.

## Rollout Rule

Broader suppression must stay behind tests.

No broader non-exact suppression should become default behavior until golden retrieval tests show all of the following:

- best-answer retention does not regress
- top-k becomes less repetitive
- query-distinct members of a duplicate group can still survive
- canonical docs sources outrank mirrors where expected
- version-distinct docs survive when the query names a version

Until those goldens pass, the default remains `exact_only`.

## Experimental Buckets

The current experimental buckets are intentionally narrow and independently gated:

- `boilerplate_variant_suppression`
- `canonical_docs_mirror_suppression`
- `helper_clone_suppression`

These buckets are rollout-only. They are not part of the default-safe retrieval contract.

## Release Gates

Any broader non-exact rollout must clear both offline evaluation and live telemetry.

Minimum expectations:

- no regression in best-answer retention / hit@k
- reduced top-k repetition
- no increase in false-collapse rate on golden cases
- canonical docs preference remains correct
- version-sensitive docs retention remains correct

Threshold churn without benchmark improvement is not a valid rollout reason.

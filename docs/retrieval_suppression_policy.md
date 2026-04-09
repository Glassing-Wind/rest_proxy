# Retrieval Suppression Policy

## Current Default

The current retrieval policy is intentionally conservative.

- exact duplicates are always suppressed
- `stage2` is the current default rollout
- boilerplate variants and canonical docs mirrors may be suppressed when the lower-level gates pass
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

Until those goldens pass, the default remains the currently approved rollout stage and broader relations stay gated.

## Experimental Buckets

The current experimental buckets are intentionally narrow and independently gated:

- `boilerplate_variant_suppression`
- `canonical_docs_mirror_suppression`
- `helper_clone_suppression`

These buckets are rollout-only. They are not part of the default-safe retrieval contract.

## Currently Suppressible Relations

The only always-on relation remains:

- `exact_duplicate`

The current default-safe promoted non-exact relations are:

- `boilerplate_variant`
- canonical docs mirrors when the canonical source is clear

The remaining experimental promoted candidate is:

- helper/helper renamed clones with strict gates

Only the default-safe relations are enabled by default. Broader helper-clone suppression remains stage-gated and reversible.

## Gating Criteria

Non-exact suppression is allowed only when all of the following are true:

- relation is in the currently allowed rollout set
- structure overlap clears the configured threshold
- lexical or fingerprint overlap clears the configured threshold
- role match clears the configured threshold
- length ratio stays within configured bounds
- query-distinction score stays below the configured threshold
- there is no role conflict unless explicitly allowed
- there is no aspect conflict

Query-aware logic still wins. If suppression would hide a query-distinct result, the result must survive.

## Release Gates

Any broader non-exact rollout must clear both offline evaluation and live telemetry.

Minimum expectations:

- no regression in best-answer retention / hit@k
- reduced top-k repetition
- no increase in false-collapse rate on golden cases
- canonical docs preference remains correct
- version-sensitive docs retention remains correct

Threshold churn without benchmark improvement is not a valid rollout reason.

## Rollout Stages

- `stage1`: boilerplate variant suppression only
- `stage2`: stage1 plus canonical docs mirror suppression
- `stage3`: stage2 plus helper/helper clone suppression under strict gates

Every stage must remain independently reversible through flags.

## Known Risks

- helper/helper clone suppression can still hide useful implementation variants if query-distinction is mis-scored
- docs canonicalization can regress if source classification is wrong
- aggressive threshold loosening can reduce diversity without improving answer quality

## Rollback Plan

If any rollout stage regresses best-answer retention, false-collapse rate, or canonical/version behavior:

- disable the stage flag immediately
- keep exact-only suppression active
- inspect trace and telemetry for the affected queries
- update gates only with benchmark improvement, not intuition alone

# Explicit expired worker recovery — October 7

24 offline checks pass: worker 11, registry 12, CLI 1. Focused Ruff and diff check
pass. No live provider/model call or task mutation in this milestone.

`scripts.task_run --recover-expired REASON --execute` replaces only an expired
read-only claim at the supplied revision. Supply all normal task/source/provider
arguments. Registry cap remains five total attempts. Fresh source is required;
this does not replay stale checkpoint evidence into the provider. Historical
checkpoint is copied into claim history; new checkpoint can then replace current.
Task payload size cap still applies transactionally and may reject large histories.

Fixture recovery at mocked expiry preserves old partial checkpoint, rereads changed
source and makes one injected generator call before review_pending. An active claim
rejects without generation/revision mutation. Existing registry tests cover stale
revision, cancellation, old-token rejection and attempt bounds. Earlier submissions,
reviews and advisory assessments are preserved by reclaim/update behavior.

Live Siri task remains unretried. A prior HTTP timeout does not attest that server
inference stopped. No cancellation/lifecycle request sent to LM Studio.

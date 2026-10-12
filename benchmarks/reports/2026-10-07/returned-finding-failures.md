# Returned-finding failures — October 7

30 offline checks pass: worker 15, registry 12, inspect 2, CLI 1; focused Ruff passes.
Wrong citation fixture retains finding_rejected/returned_finding, no submission or
rejected text. Source-change fixture fails freshness validation before publication
and retains rejection category. Inspect exposes the bounded event unchanged.

This stage includes returned-object/citation checks, generation receipt handling,
source freshness validation and submission persistence. It is deliberately coarse:
a storage or serialization failure is not evidence of inaccurate model reasoning.
Original exceptions propagate. Best-effort record failure must not hide original
failure; changed revision/cancellation/reclaim and size caps may prevent recording.
Successful submissions still require explicit review. No semantic auto-approval.

No live task mutation, provider call or lifecycle changes. Historical Siri timeout
remains unresolved; repeated unchanged calls should not consume remaining attempts.

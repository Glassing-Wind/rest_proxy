# Local review policy acceptance — October 16, 2026

Routine trusted-local read-only findings can now receive source-checked agent review.
Human review is an explicit escalation path. The synthetic two-line provider fixture
has a separate mechanical acceptance path with executable behavior assertions.

Implementation: local `codex/task-provider-adapter` at
`da67d5d201cd637b6f07b2acf88dbcd1ff3120e4` plus the uncommitted changes identified in
[implementation.json](implementation.json). No commit, push, merge or PR mutation was
performed. Existing unrelated working changes remain intact.

## Resulting behavior

- `prepare_review` exposes bounded current evidence and binds context to the latest
  submission, expected task revision and complete finding digest. It independently
  revalidates raw submissions and rereads all cited paths through the existing
  bounded, non-symlink source reader.
- `review_agent` requires a reasoned judgment for the entire answer and each limit,
  explicit supplied citation coverage and fresh source validation at decision time.
  All-supported judgments can accept routine findings. Demonstrated unsupported
  claims can request correction. Unresolved accept/correction combinations reject
  without mutation; explicit escalation retains the assessment and requires a human.
- `human_review_required=True` at creation or escalation prevents later agent,
  automated or unspecified decisions from accepting or requesting correction.
  Explicit caller-labelled human review remains possible. This flag grants no
  command, publication, deployment or external-action permissions.
- New records distinguish `agent`, `automated`, `human` and `unspecified` actor types.
  Existing no-kind review calls stay compatible and are not relabelled as human.
  Actor identity and independence remain trusted-local caller reports.
- `review_sum_fixture` accepts only the pinned source hash, exact task/answer/scope
  limit and four executable behavior cases. Caller-supplied code is never executed;
  the checker executes only its application-owned pinned literal after checking its
  hash. Unknown prose or unsupported limits fail mechanically.
- `scripts.check_task_provider_fixture` creates private disposable source/state,
  requires explicit endpoint/model and `--execute`, makes one existing worker CLI
  invocation, performs fixture review and verifies separate-process reopen. It
  retains a private receipt on success or failure and performs no automatic retry.

Complete contracts and JSON examples: [task-review-policy.md](../../../../docs/task-review-policy.md).

## Verification

[Exact commands](offline-commands.txt), [structured results](offline-summary.json),
and [individual output](offline-logs/) retain the evidence.

| Scope | Passed |
| --- | ---: |
| Existing local shared-task/gateway/helper offline checks | 71 |
| New agent-review cases | 23 |
| New pinned-fixture cases | 14 |
| **Total** | **108** |

Zero failures, errors or skips. Focused Ruff passed for all eight changed Python
modules/scripts/tests; shell syntax and changed tracked-file diff checks passed.
The two new suites are included in the existing offline CI loop. Full CI was not
run because it manages the MCP daemon and broader graph/protocol services.

New checks exercise current/deleted/changed/symlinked sources, malformed raw
submissions and provenance, huge timestamps, invalid identifiers, exact target and
citation coverage, stale revisions/submissions/digests, self-review, cancellation,
human escalation, typed actor retention and separate-process CLI reopen. HTTP fixture
tests start and stop only their own ephemeral loopback server. They prove one-request
success, semantic-mismatch retention without retry, invalid-budget rejection before
inference, and refusal without `--execute`.

No real model was called for this policy implementation. These are executable
workflow and fixture checks, not measured reviewer quality or agent-independence
verification. The earlier 76-test result and real-model receipt apply separately to
published PR head `da01b790737dc0341964bce3c8f78cf0c6e97b6e`; local source still diverges
from that head. The local baseline has nine dispatch and fifteen worker tests, versus
ten and nineteen on that published head. This change does not reconcile those branches
or erase the published preparation-failure fix.

## Limits

The raw local API and manual review remain trusted-operator routes, not authenticated
authorization. Different actor labels do not prove different agents or people. Agent
judgments can be wrong even with complete citation coverage; current-file hashes
establish which bytes were checked, and files can change afterward. Consequential
work needs explicit separate authority and fresh verification. No background worker,
remote task API, model lifecycle change or automatic correction loop was introduced.

The October 15 human correction receipt remains historical and unchanged. Its task
was not silently converted to automated acceptance. Neo4j onboarding requested later
was handled separately in [its setup receipt](../neo4j-cli-setup.json); it is not part
of these offline test results.

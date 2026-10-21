# October 21 local-work synchronization

The work was prepared in an isolated clone from merged main `126dbe29`.
The original branch, index, dirty files and 21 local-only commits were preserved.
Nine feature groups are published as ordered draft PRs. Review and merge in base order;
no follow-up PR was merged by this task.

## Current validation

49 of 54 test scripts pass after the task-cockpit integration fix; the additional
packaging/distribution script also passes (50 of 55 overall). Successful scripts
include optional dependency skips. Five scripts
remain blocked by sandbox restrictions on loopback listeners or process inspection:
`test_evidence_workspace_launch.py`, `test_repoanalyzer_adapter.py`,
`test_task_cockpit_launch.py`, `test_task_review_fixture.py`, `test_telemetry.py`.
Initial failures and skips remain in the validation record; no complete green gate is claimed.
Production Ruff, focused cockpit Ruff, shell syntax and 24 cockpit HTTP tests pass.
Full daemon-changing CI, browser UI and real-model checks were not rerun.

The reconciliation retains PR #4 preparation-error handling. Task-cockpit reads
previously rejected its updated qualifier and failure classification; two regressions
now verify bounded preparation-failure display and mismatched-stage rejection.

Historical receipts remain historical. Sanitized acceptance logs referenced by
hash manifests are included. Private state and source bodies are excluded.
Credential-pattern scanning found no matching patterns in the feature snapshots;
this is not a comprehensive security clearance.

Machine-specific settings and the unreviewed audit ZIP remain local.
The exact vendored Go patch retains its upstream context whitespace and recorded hash.
GitHub publication uses non-force branch creation through the connected API because
shell Git cannot resolve GitHub. Published tree identities are checked against local trees.

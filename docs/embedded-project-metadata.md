# Embedded project metadata — October 5, 2026

Published repository evidence already carries project identity, source hashes,
encoder details and run IDs. This slice adds **project annotations**: bounded JSON
for a display name, description, tags or other workspace notes. These are
user/agent-authored assertions, not verified repository facts. No annotation is
injected into retrieval or used to alter source citations.

## Contract and ownership

The shared embedded owner initializes and validates an additive `WorkspaceMetadata`
Ladybug node table. Each project has one JSON object, monotonic revision within its
lifetime, workspace root, update time in milliseconds and update publication ID.
Reads need no model. An absent record on a published project returns `{}` at
revision zero; unpublished projects return `not_published`.

Two opt-in MCP tools share STDIO/HTTP behavior:

- `get_embedded_project_metadata(project_id)` reads the current document, revision,
  publication ID and annotation provenance. It is available in the primary profile.
- `update_embedded_project_metadata(project_id, metadata, expected_revision,
  expected_run_id)` replaces the document. It is classified as an admin tool.
  Metadata must be an object with at most 50 top-level keys and 16,384 UTF-8 bytes,
  serialized deterministically without NaN/Infinity.

Read before writing. Both preconditions must match in the same write transaction;
otherwise `conflict` returns the current revision and run ID without a write.
Review current values before retrying. An empty object clears annotations and
advances the revision, preserving stale-writer protection. This is replacement,
not a partial merge. Values may be nested JSON within the size limit.

Same-root reindex preserves annotations and revision. `updated_run_id` records the
publication at the annotation's last update; `run_id` is the current publication.
A changed root returns `workspace_changed` and hides the previous document until
an explicit replacement using the current run and stored revision. Old annotation
bytes remain stored until replacement or deletion; hiding is not physical erasure.

Project deletion removes annotations in the same graph transaction as publication
visibility and repository evidence. Later vector cleanup failure cannot expose
annotations from a deleted project. Reusing a project ID starts at revision zero,
but old run-ID preconditions still fail. Rollback preserves prior metadata.

There is no revision history, automatic retention policy, author identity or
annotation signature. This is machine-local workspace metadata, not authenticated
multi-tenant storage or a FIRE checkpoint implementation. Session/watch/job state,
original task evidence, correction history and REST integration remain pending.
Existing global JSON registries are not automatically imported or redirected.

## Validation

Two offline validation tests reject malformed/oversized documents and absent write
preconditions before storage. Eight runtime/MCP tests pass, including exact
precondition dispatch. Twelve native repository tests pass, including competing
updates, persistence across reopen/reindex, root changes, clearing, rollback,
scoped deletion and project-ID reuse. Full local CI passed.

Real sandboxed STDIO and HTTP metadata reads match on the existing frozen 131-file
publication, alongside the investigation tools. This read acceptance does not
modify annotations on that corpus. Native disposable fixtures exercise writes.
No model connection or external storage service is required for metadata.

[Acceptance receipt](../benchmarks/reports/2026-10-05/embedded-project-metadata.json).
Private logs: `.runtime/embedded-metadata-acceptance/` (mode 700).

## GitHub CI review

Before this change, both hosted workflows for active PR #3 at `ee35c0b` passed:
[CI](https://github.com/Glassing-Wind/rest_proxy/actions/runs/37260584626) and
[Security](https://github.com/Glassing-Wind/rest_proxy/actions/runs/37260584633).
All seven check jobs were green.

The reported failures are present on the older `codex/enterprise-hardening` branch
(PR #1). Its [October 4 security run](https://github.com/Glassing-Wind/rest_proxy/actions/runs/37209829259)
failed secret scanning (one historical finding) and dependency audit (19 reported
vulnerabilities across seven packages). That branch was not changed here. Active
branch success does not prove the older findings are remediated everywhere; a
separate old-branch review would be needed before merging it.

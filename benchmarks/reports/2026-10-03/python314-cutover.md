# Python 3.14 runtime cutover

The normal local daemon on port 8001 now runs Python 3.14.4. Project launch,
verification and background-indexing selection prefer `.venv`; explicit interpreter
settings remain supported. On this host the venv is a link to the already tested
isolated environment. The working Python 3.11 environment is retained for rollback.
Apple system Python was not changed.

Completed after the changes:

- The new `scripts/setup_project_python.sh` rebuilt pinned ts-pack with release
  stripping disabled, bypassed cached native wheels, installed dependencies,
  passed `pip check`, and verified native parsing.
- Local CI passed through the default project selection path, including the new
  venv-versus-implicit-conda runtime regression and existing explicit overrides.
- Repository indexing completed through the new default runtime.
- The full live retrieval-quality gate passed on the ordinary daemon.
- A plain global Python invocation of the freshness checker successfully reentered
  the project environment, without confusing the shared base executable with
  venv dependency isolation.
- The optional Linux ARM64 image built successfully on Python 3.14.8 and passed
  application imports, native parser execution and the 12-tool primary catalog.
  This is a container smoke test, not another full storage lifecycle test.

CI workflows now select Python 3.14; remote CI was not dispatched. The local
`.python-version` records 3.14.4, while CI/container major-minor selectors may
resolve newer patches. The package retains its Python 3.11 compatibility floor.

The first setup-command attempt encountered a non-executable helper script;
calling the existing helpers through Bash corrected it. That failed log is
retained alongside the successful run. The earlier malformed default native build
and stale-index gate attempt are preserved in the initial compatibility report.

[Machine-readable results](python314-cutover.json),
[setup and rollback](../../../docs/python314-runtime.md),
[initial compatibility checks](python314-README.md).

Raw logs remain under `.runtime/python314-verify/`; hashes are recorded in the
machine-readable report. No operational data migration or inference-provider
change was made.

Final index health reports 319 files synchronized and structural/semantic runs
aligned. It also reports one global shadow namespace (582 nodes / 71 relationships).
A read-only cleanup inspection confirmed the counts; ownership and activity were
not established, so no deletion was performed. This warning is separate from the
completed runtime cutover.

## Follow-up inspection — October 4, 2026

A later read-only inspection confirmed 582 code-indexing nodes and 71 `IMPORTS`
relationships in the same residue namespace. The nodes represent functions,
imports, files, sections, classes and clone groups. The canonical project reported
`done`, while its IndexRun reported `struct_written`; legacy writer ownership is
still unknown and no cleanup was performed. See the
[lifecycle safety report and exact counts](../../../docs/indexing-lifecycle-safety.md#verification-and-observed-legacy-residue).
This follow-up does not change the original cutover measurements.

Further [run investigation](../2026-10-04/shadow-run-investigation.md) confirmed
authentication rate limiting blocked finalization and the failure-status write.
The 71 relationship count is namespace-property scoped; 1,639 relationships touch
the staging nodes when counted by endpoints. The source of the invalid-credential
attempts remains unresolved, and no legacy cleanup was performed.

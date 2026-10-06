# Release inventory and cold restore — October 6, 2026

The accepted minimal environment now has a CycloneDX 1.6 installed-distribution
SBOM, hashed application/parser artifacts, copied shipped license/notice files and
an explicit gap inventory. The SBOM passes the official 1.6 JSON Schema and declares
its composition incomplete. This selected schema version is an interchange format,
not a claim to use the latest CycloneDX revision.
[Format overview](https://cyclonedx.org/specification/overview/).

[SBOM](../benchmarks/reports/2026-10-06/minimal-embedded.bom.cdx.json) covers 48
installed Python distributions. `scripts/build_release_inventory.py` enumerates
installed metadata and actual installed-file hashes without fetching metadata URLs.
It copies 72 discoverable shipped notice files into a new private directory.
The exact application and modified parser wheel SHA-256 values are recorded;
installed file manifests are separate from original download artifact hashes.
The complete manifest, notice files and deterministic `third-party-notices.tar.gz`
remain under `.runtime/release-evidence/` with receipt hashes.

Run the builder with the environment being inventoried:

```bash
python -I /path/to/scripts/build_release_inventory.py \
  --output /private/new-inventory-directory \
  --artifact /path/to/application.whl --artifact /path/to/modified-parser.whl
```

Existing output directories are refused. Notice copy names use content hashes;
recorded files outside the environment or absent on disk are gaps. Output contains
relative component paths and artifact basenames, not configuration secrets or
credential-bearing metadata URLs. The SBOM has no resolved dependency graph or
vulnerability assessment. Its package inventory is not the complete bundled native
or grammar dependency closure and does not establish commercial legal approval.

## Explicit inventory gaps

- `lance-namespace` 0.13.0 and `lance-namespace-urllib3-client` 0.13.0 have no
  discoverable shipped license/notice files in this installed environment.
- LanceDB 0.39.0 and tree-sitter 0.26.0 have no concise machine-readable license
  metadata in the installed packages; their discoverable notice files were copied.
- Native bundled dependencies require exact binary/source reconciliation. The
  [earlier Cargo inventory](../benchmarks/reports/2026-10-03/ts-pack-license-inventory.json)
  remains historical supporting evidence, not automatic attestation of this wheel.
- [Observed grammar asset hashes](../benchmarks/reports/2026-10-06/observed-grammar-assets.json)
  identify ten cached macOS parser libraries and their bundle/manifest. The bundle
  hash matches the manifest, but individual grammar source commits and corresponding
  notices are not reconciled. Only Python is used by the current restore fixture.
- Model assets and runtime license terms are outside this package inventory.

These are release gates to resolve, rather than missing values to fill by guessing
from a project-level license. No GPL-free or enterprise legal-approval claim is made.

## Cold restore acceptance

`python -I /path/to/scripts/check_embedded_restore.py` with the minimal installed
profile creates only disposable state. A native owner indexes a Python file with
synthetic vectors, then closes. FIRE saves a correction, an already-deleted scope
and an already-expired scope. With every fixture owner/SQLite connection closed,
the drill hashes and copies all graph/vector/FIRE state into an archive directory.
It removes original live state and source files, copies the archive to a new root
and verifies the pre-open file hashes match exactly (16 fixture files).

The reopened owner returns identical published source, vectors and publication ID.
FIRE restores revision 2 and the original evidence, preserves deletion tombstones
and expected-revision conflicts, keeps expired data inaccessible, permits expiry
purge and preserves project scope isolation. Source text remains historical evidence;
no current-code validation is implied. Python socket connections are denied in
this drill; that audit guard is not an OS sandbox. The fixture archive is deleted
at the end; it is not an operational backup. Four focused tests and full local CI
pass; the native CI test explicitly skips when optional engines are absent.
[Acceptance receipt](../benchmarks/reports/2026-10-06/release-inventory-and-restore.json).

## Operator cold-copy procedure and remaining work

1. Stop all daemon owners, indexing jobs, watchers and FIRE writers. A filesystem
   copy of an actively changing graph/vector pair is not accepted by this drill.
2. Copy the complete embedded publication root and configured FIRE root to a private
   backup destination, preserving permissions. Include both data roots in one
   quiescent checkpoint and record hashes, schema/runtime versions, vector dimension
   and encoder/artifact identities. Preserve lock files; do not unlink live lock inodes.
3. Verify copies against their hash manifest. Retain sensitive evidence under an
   explicit encryption, retention and access policy. Expiry/deletion of live data does
   not erase an older backup; deletion reconciliation and backup retirement are required.
4. Restore into new private paths, verify hashes before opening, and use matching
   engine/schema/parser versions. Keep watchers disabled; restored leases and job
   records are not proof of current process liveness or permission to replay work.
5. Reopen one owner and check publication identity, source citations, vectors and FIRE
   revision/tombstone/expiry boundaries before enabling client access. Validate current
   source and model identity separately. Never overwrite a live operational state root
   as part of this fixture procedure.

Still open: automated coordinated or online backup, encrypted off-host retention and
post-backup deletion reconciliation, production-scale restore/RPO/RTO measurements,
fresh exact-pin native builds/platform installs, notice reconciliation, real model
acceptance from the installed profile and controlled paired coding outcomes.

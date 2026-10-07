# Namespace supplemental notices — October 6, 2026

Two missing-notice findings now have source-bound supplemental evidence:
`lance-namespace` 0.13.0 and `lance-namespace-urllib3-client` 0.13.0. The upstream
wheels and source archives still omit discoverable LICENSE/NOTICE files; their
original package findings remain recorded. Our separate notice collection now
includes the root license from the matching immutable release source.

The [exact-version PyPI metadata](https://pypi.org/pypi/lance-namespace/0.13.0/json)
and [client metadata](https://pypi.org/pypi/lance-namespace-urllib3-client/0.13.0/json)
identify the downloaded artifacts. Both wheel and source-archive SHA-256 values
were verified against those records. All 167 installed Python files match the
wheels. Including `py.typed`, all 169 package files match Git blob identities in
the upstream `v0.13.0` release tree: five namespace and 164 client files.

The annotated tag resolves to commit
`8528f9f250336e608d6d592287af6ad67a0ef30e`. The retrieved
[root LICENSE](https://github.com/lance-format/lance-namespace/blob/8528f9f250336e608d6d592287af6ad67a0ef30e/LICENSE)
also matches that tree's Git blob. A local
[notice copy](../benchmarks/reports/2026-10-06/namespace-release-LICENSE.txt) and
[artifact/source bindings](../benchmarks/reports/2026-10-06/namespace-notice-binding.json)
make this reviewable. This binds the specified package subtrees and root license;
it does not attest all repository assets or confer legal approval.

## Implemented collection support

`scripts/reconcile_python_notice.py` compares supplied exact wheel hashes,
package-subtree Git blobs, an optional installed tree and the root license blob.
It never extracts or executes packages. Wrong artifact/source/notice hashes and
truncated source trees fail. Online evidence was fetched separately from PyPI and
the upstream immutable GitHub release, then retained locally with receipt hashes.

`scripts/build_release_inventory.py --supplements /path/to/reviewed-supplements.json`
now validates supplied notice SHA-256 values and installed component IDs, copies
content-addressed supplemental notices, and adds their hashes to SBOM properties.
Original missing-file findings remain visible with a supplementation annotation;
the generator does not invent upstream wheel contents or infer legal approval.
Notice paths in the JSON are relative to its directory and are omitted from the
emitted public evidence. Supplement inputs require local provenance review; the
builder verifies content binding, not external source truth on its own.

The refreshed collection has 48 components, 72 discovered shipped notices and two
supplemental component associations sharing one source license file. Its
[supplemented SBOM](../benchmarks/reports/2026-10-06/minimal-embedded-supplemented.bom.cdx.json)
passes the official CycloneDX 1.6 schema and retains incomplete composition.
Seven focused tests and full local CI pass.
[Acceptance receipt](../benchmarks/reports/2026-10-06/namespace-notice-reconciliation.json).

LanceDB 0.39.0 and tree-sitter 0.26.0 still lack concise SPDX expression metadata
in their installed packages. Their existing license classifiers and shipped notice
files are evidence for review; the original metadata fields were not rewritten.
Native bundled dependencies, individual grammar source/notice closure, model terms,
exact-pin source builds/platform installs and controlled paired outcomes remain open.
No application dependency, operational service, model or parser cache was changed.

Subsequent [observed grammar reconciliation](grammar-notice-reconciliation.md) adds
ten binary/bundle bindings and nine declared-source notices. Complete bundle/native
closure and independently reproduced source builds remain open.

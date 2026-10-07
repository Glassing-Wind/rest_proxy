# Observed grammar notice reconciliation — October 6, 2026

The ten observed macOS ARM64 parser libraries now have verified bundle membership
and retained notices from their declared source revisions. Their cached bytes match
the corresponding members of the SHA-256-verified ts-pack 1.20.0 bundle. Nine source
repositories supply nine root license files; TypeScript and TSX share one source.
No cached binaries, parser configuration, dependency pins or models were changed.

[Asset/source/notice bindings](../benchmarks/reports/2026-10-06/grammar-notice-bindings.json)
record every binary hash, repository/revision and source notice hash. The
[ten-component observed-grammar SBOM](../benchmarks/reports/2026-10-06/observed-grammars.bom.cdx.json)
passes CycloneDX 1.6 schema validation and explicitly retains incomplete composition.
Source notices are retained under `benchmarks/reports/2026-10-06/grammar-notices/`.
This is separate from the installed Python-package SBOM because these binaries are
runtime cache assets, not installed Python distribution metadata.

## Evidence chain and utility

The upstream release source is pinned at
`f4b24ece538efed9ab2c4e6db9409fbfc4cc3a67`.
Its [language definitions](https://github.com/xberg-io/tree-sitter-language-pack/blob/f4b24ece538efed9ab2c4e6db9409fbfc4cc3a67/sources/language_definitions.json)
provide each grammar's declared repository and commit. Root LICENSE files were
retrieved at those immutable commits, checked against Git tree blob identities and
retained with SHA-256 hashes. This replaces relying on a mutable license cache or
assuming the parser pack's project license covers every grammar.

`scripts/reconcile_grammar_assets.py` verifies the retained bundle against its
cached release manifest, compares each selected library to an unambiguous root
archive member, validates pinned source declarations and checks notice hashes and
Git blob identities. Archives are read without extracting or loading libraries;
caches remain unchanged. Modified bundle/cache/notice/definition inputs are rejected.
Output files must be new. Current filename selection targets macOS `.dylib` assets;
other platforms remain a separate acceptance gate. Zstandard archive reading uses
Python 3.14's standard-library support.

```bash
python -m scripts.reconcile_grammar_assets \
  --cache /path/to/tree-sitter-language-pack/v1.20.0 \
  --bundle /path/to/retained-macos-arm64.tar.zst \
  --definitions /path/to/release-language-definitions.json \
  --notices /path/to/source-notices.json \
  --output /path/to/new-reconciliation.json
```

Notice input records declare the release definitions hash, source revision and
retained notice paths/hashes. They require source provenance review before use;
the offline utility verifies the supplied evidence chain rather than fetching or
endorsing arbitrary source claims. Three focused tests and full local CI pass.
[Receipt](../benchmarks/reports/2026-10-06/grammar-notice-reconciliation.json).

## Remaining boundaries

The retained bundle has 371 regular library files and no discoverable notice files.
This collection covers ten observed libraries, not the other 361 or every grammar
that could be downloaded later. Complete-bundle distribution needs matching notice
coverage for its full contents. Root source notices do not establish the complete
native linked-dependency closure.

Byte equality to a release bundle is not an independently reproduced source build.
Declared source revisions and notice blobs are verified; independently rebuilding
those binaries remains open. Native engine/parser dependency reconciliation, other
platforms, two installed-package SPDX metadata gaps, model asset terms, operational
backup/deletion policy and controlled paired coding outcomes remain release gates.
No commercial legal approval, GPL-free distribution or improved coding quality is
claimed by this inventory.

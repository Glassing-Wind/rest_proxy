# Distribution layout acceptance — October 6, 2026

The wheel now includes the `scripts` Python package. The committed baseline
`f443f6a` excluded both `scripts/run_struct_index.py` and
`scripts/index_workspace.py`, despite `tools/hands/indexing.py` launching them
relative to the installed module root. This could leave an installed distribution
without its subprocess workers. Both workers now ship in the wheel and source archive.

Run `.venv/bin/python test_distribution.py` for the offline artifact check. It copies
tracked source into a disposable build directory, builds wheel and source archive
with installed setuptools, verifies required runtime files and the wheel's MIT
license, and installs the wheel with `pip --no-deps --no-index --target` into a
second directory. An isolated `python -I -S` process imports the installed FIRE
store and context builder and compiles the required runtime files. No services,
model downloads, package-index resolution or operational indexes are used. The
check is included in the normal local CI gate.

Acceptance passed on CPython 3.14.4 / macOS ARM64, including the full local CI gate.
[Receipt](../benchmarks/reports/2026-10-06/distribution-acceptance.json).
The baseline artifact was independently built and confirmed missing both workers.

## Remaining release gates

This validates artifact layout and stdlib continuity imports. It does not prove a
clean dependency install, worker execution, installed-daemon startup or native
indexing. The existing broad dependency manifest remains in use; LadybugDB and
LanceDB are not declared there. A minimal embedded dependency profile, native
fork-wheel/grammar/model notices, exact artifact SBOM and supported-platform matrix
remain required. Shell service-management helpers are not included by this Python
package change. Source-tree service scripts remain the documented operations path.

Before calling an installed distribution supported, verify worker/runtime discovery
and writable state paths outside the source checkout, rebuild/install from the
source archive, start the owner daemon from that installation, index and investigate
a disposable repository with external storage disabled, and exercise restore.
Controlled paired coding outcomes and provider-template budgeting remain separate
gates; these artifact checks make no quality, token-savings or latency claim.

The subsequent [minimal installed profile](minimal-embedded-install.md) supersedes
the dependency-profile and installed-native-fixture gaps above. Real model, restore,
exact artifact notices and platform coverage remain release gates.

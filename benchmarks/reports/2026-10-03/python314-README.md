# Python 3.14 compatibility verification

Tested standard GIL-enabled CPython 3.14.4 on this macOS ARM64 host in
`.runtime/python314-verify/venv`. Apple Python, Homebrew Python and the working
Python 3.11 project environment were not upgraded or replaced.

## Results

- Full `requirements.txt` dependency resolution and installation succeeded.
- `pip check` passed with the complete project manifest plus embedded engines.
- Ladybug 0.21.2, LanceDB 0.39.0 and PyArrow 25.0.1 passed disposable native engine
  checks on 3.14.4, both before and after installing project dependency pins.
- The default pinned ts-pack source build produced a malformed macOS binary:
  `mis-aligned LINKEDIT string pool`. Three semantic-ranking CI tests then failed
  while the optional native import fell back. The same binary failed to load with
  Python 3.11, demonstrating that the loader failure is not specific to 3.14.
- Rebuilding the same pinned Git revision with
  `CARGO_PROFILE_RELEASE_STRIP=false` succeeded. Native import and parsing then
  worked, and the complete local CI suite passed.
- Dependency imports passed for Torch, Transformers, sentence-transformers,
  SciPy, scikit-learn, asyncpg, psycopg, Neo4j and Playwright. No model download,
  inference benchmark or browser-binary launch was performed by this import check.

The refreshed complete live retrieval-quality gate passed, including protocol
lifecycle, tool choice, investigation workflows, live graph regressions and MCP
tool parity.

## Installation workaround

The ts-pack source pin remains
`1ed4fa08816e051337e12123c1f4585ec8e6ac16`. No application dependency manifest or
external ts-pack source file was modified. In the isolated environment:

```sh
CARGO_PROFILE_RELEASE_STRIP=false python -m pip install --force-reinstall --no-deps --no-cache-dir \
  'tree_sitter_language_pack @ git+https://github.com/Zmaroo/tree-sitter-language-pack.git@1ed4fa08816e051337e12123c1f4585ec8e6ac16#subdirectory=crates/ts-pack-python'
```

This is a verified local workaround. Fix or document reproducible wheel packaging
before calling the default clean installation ready. The comparison supports a
build/strip-path diagnosis; it does not establish every toolchain's behavior.

## Integration validation and limits

CI and live integration use a separate daemon on port 8015 with both Python
selection variables pointing to the isolated environment. The ordinary daemon
on port 8001 was not replaced. Existing Neo4j/PostgreSQL services supplied the
integration data; embedded application integration is still incomplete.

The first live gate stopped at the trust check because planning work had added
five files and modified three without an index refresh. Indexing through the 3.14
environment refreshed 317 files; structural and semantic phases completed. The
initial failure and subsequent checks are retained separately.

Only this Mac and standard CPython were tested. No free-threaded Python,
cross-platform runtime, production migration, performance advantage, complete
browser/model integration or full embedded application readiness is established.
Retain Python 3.11 while packaging and supported-platform validation are completed.

## Evidence

[Compatibility status](python314-compatibility.json),
[native engines](python314-native-engines.json),
[dependency imports](python314-dependency-imports.json).
Raw logs, resolved requirements and resolver metadata are retained in
`/Users/michaelmarler/Projects/rest_proxy/.runtime/python314-verify/`:
`full-resolution.log`, `full-install.log`, `ts-rebuild-unstripped.log`, `ci.log`,
`ci-unstripped.log`, `index-refresh.log`, `retrieval-gate.log`, and
`retrieval-gate-refreshed.log`. Machine-readable status records the final live-gate
result separately from the initial stopped attempt.

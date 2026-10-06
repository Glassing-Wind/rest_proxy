# Minimal embedded distribution — October 6, 2026

The package now separates six core dependencies from `embedded`, `full` and `dev`
extras. `requirements-core.txt` is the core MCP/HTTP registration profile;
`requirements-embedded.txt` adds LadybugDB, LanceDB and the existing modified
parser fork at `6fcead43fc13b0049481ea5b5c491e02eab4ac68`. The broad existing
`requirements.txt` remains the `full` extra and the source-tree setup path.
`requirements-dev.txt` preserves the Ruff extra. Package metadata and source-archive
manifest coverage are checked by `test_distribution.py`.

The Neo4j Python client is still in core because registration imports its helpers.
It does not require a Neo4j server in embedded mode. Core installation does not
promise every documentation/crawler/provider feature; those need their corresponding
optional dependencies. Local tokenizer accounting additionally needs `tokenizers`.
Native parser modifications are required: an upstream wheel with the same version
number is not an interchangeable replacement.

For a source build, the intended dependency selection is:

```bash
CARGO_PROFILE_RELEASE_STRIP=false python -m pip install '.[embedded]'
```

That command requires Rust/build prerequisites and fetches the pinned fork. This
milestone tested installation with a retained modified native wheel instead of
rebuilding that remote SHA. Follow [native fork setup](python314-runtime.md) and
[parser acceptance](ts-pack-upgrade.md) for prior build evidence. The accepted wheel
hash is recorded in this milestone's receipt; its reuse is not a new source-build
or platform-matrix attestation. `.[full]` retains the broad dependency profile.

## Writable paths and service configuration

Installed packages should set these before startup, outside `site-packages`:

- `LM_PROXY_RUNTIME_DIR`: index jobs, project locks/manifests and proxy PID files.
  Blank preserves the existing source-tree `.runtime` default.
- `LM_PROXY_CONFIG_DIR`: machine-local registry/config paths.
- `LM_PROXY_EMBEDDED_STATE`: explicit graph/vector publication root.
- `LM_PROXY_FIRE_STATE`: explicit scoped continuity root, when enabled.

This runtime override does not relocate every legacy storage path. Keep legacy
external-memory paths disabled for this profile and configure proxy state separately
if using it. Workers resolve from installed modules; interpreter selection falls
back to the running environment when no explicit interpreter or source-tree venv
is configured. Shell service-management scripts remain source-tree operations.

Select `LM_PROXY_STORAGE_BACKEND=embedded`, `LM_PROXY_GRAPH_BACKEND=ladybug`, and
keep watchers disabled unless deliberately deployed. Enable
`LM_PROXY_EMBEDDED_REST_ENABLED=1` for REST reads and
`LM_PROXY_CONTEXT_ENABLED=1` for context tools. Disable legacy memory, Redis and
external persistence for the bounded embedded profile. Start the installed daemon
with `python -m uvicorn brain_server:app --host 127.0.0.1 --port 8001` from a
controlled configuration directory. Explicit repository indexing with real vectors
still requires a supported already-loaded embedding model and configured local
artifact identity; this profile does not load or download models.

## Installed acceptance

A fresh CPython 3.14.4 environment on macOS ARM64 installed core dependencies,
Ladybug 0.21.2, LanceDB 0.39.0, the retained modified parser wheel and the newly
built application wheel. Dependency checking passed for 48 installed packages.
The packaged acceptance command is:

```bash
python -I -m scripts.check_installed_embedded
```

It verifies application modules come from the installed environment, indexes two
fixture Python files with native engines and synthetic vectors, searches vectors,
closes the owner and starts a fresh installed HTTP daemon on a disposable listener.
REST source/text reads and source hash verification pass. Runtime jobs/locks are
created under explicit temporary paths. Python socket audit guards allow only that
disposable REST connection in both processes; they are not an OS network sandbox.
No operational services, repositories or model lifecycle were modified.
[Acceptance receipt](../benchmarks/reports/2026-10-06/minimal-embedded-install.json).

The first artifact-path assertion compared macOS's symlinked temporary path with
its resolved form and failed; normalizing the expected path corrected the test.
Failed logs are retained alongside final passing checks. This was a test assertion
fix, not a runtime path migration. Full local CI and final artifact contracts pass.

Remaining gates: fresh exact-pin native source builds and supported-platform installs,
exact dependency/grammar/model notices and SBOM, backup/restore drills, real embedding
acceptance from this installed profile and controlled paired coding outcomes.
Synthetic vectors establish wiring, not model quality or token/latency savings.

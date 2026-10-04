# Python 3.14 project runtime

The repository now prefers its `.venv` for daemon launches, verification scripts
and background indexing. Explicit `LM_PROXY_PYTHON` / `LM_PROXY_INDEX_PYTHON`
settings retain precedence; explicit conda selection remains supported by runtime
resolution. Apple-controlled system Python is unchanged.

## Native setup

Install a standard Python 3.14 interpreter and Rust build tooling for the pinned
native ts-pack build, then run:

```sh
./scripts/setup_project_python.sh
source .venv/bin/activate
```

The setup command creates `.venv` when missing, verifies Python 3.14, builds the
pinned ts-pack wheel without release stripping or cached-wheel reuse, installs
the remaining project requirements, runs `pip check`, and tests native parsing.
It does not install Ladybug/LanceDB automatically; embedded storage remains an
experimental optional track. An existing venv on another Python version is not
silently recreated.

`CARGO_PROFILE_RELEASE_STRIP=false` avoids the malformed Mach-O library reproduced
in the default macOS build. The wheel installer reinstalls the freshly built
artifact so a same-version broken wheel cannot be silently retained. CI uses
Python 3.14 and the same wheel builder. The optional container targets Python
3.14 with stripping disabled; Linux validation is recorded separately.

On this machine `.venv` points to the already-tested environment at
`.runtime/python314-verify/venv`. It was not moved, preserving installed script
paths. Keep that target while the link is used. New installations create a normal
project venv. `.python-version` records the tested local 3.14.4 patch; CI/container
3.14 tags can select a newer patch and require their own checks.

## Launch and checks

```sh
./scripts/restart_brain_server.sh
./scripts/run_ci_checks.sh
./scripts/run_retrieval_quality_gate.sh
```

The normal daemon uses port 8001. Indexing selects the project venv unless explicitly
overridden. The package keeps Python 3.11 as its compatibility floor; this change
sets the operational target to 3.14 rather than removing rollback compatibility.

## Local rollback

The previous Python 3.11.15 `lmproxy` environment is retained. Explicitly select
both interpreters when restarting:

```sh
LM_PROXY_PYTHON=/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python \
LM_PROXY_INDEX_PYTHON=/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python \
./scripts/restart_brain_server.sh
```

For a persistent rollback, set those overrides in private `.env`, then restart.
No application data migration is part of the Python runtime cutover.

Evidence: [initial compatibility verification](../benchmarks/reports/2026-10-03/python314-README.md).

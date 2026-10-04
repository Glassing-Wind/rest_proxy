#!/usr/bin/env bash
# Install the Python 3.14 project environment with a verified native ts-pack build.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
BOOTSTRAP_PYTHON="${LM_PROXY_BOOTSTRAP_PYTHON:-python3.14}"
if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  "$BOOTSTRAP_PYTHON" -m venv "$ROOT_DIR/.venv"
fi
export LM_PROXY_PYTHON="$ROOT_DIR/.venv/bin/python"
export LM_PROXY_INDEX_PYTHON="$LM_PROXY_PYTHON"
"$LM_PROXY_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 14), "Use a separate Python 3.14 venv; existing environment was not changed"'
"$LM_PROXY_PYTHON" -m pip install --upgrade pip
bash ./scripts/build_ts_pack_ci_wheel.sh .runtime/python314-wheels
LM_PROXY_CI_REQUIRE_TS_PACK_WHEEL=1 bash ./scripts/install_ci_requirements.sh requirements.txt .runtime/python314-wheels
"$LM_PROXY_PYTHON" -m pip check
"$LM_PROXY_PYTHON" -c 'import tree_sitter_language_pack as ts; ts.process("def ready():\n    return True\n", config=ts.ProcessConfig("python")); print("Python 3.14 native environment ready")'

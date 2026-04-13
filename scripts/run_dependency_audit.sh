#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"

PYTHON_BIN="${LM_PROXY_PYTHON:-${LM_PROXY_INDEX_PYTHON:-}}"
if [[ -z "$PYTHON_BIN" ]] && [[ -x "/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python" ]]; then
  PYTHON_BIN="/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python || command -v python3)"
fi

TMP_REQ="$(mktemp)"
trap 'rm -f "$TMP_REQ"' EXIT

grep -vE '^[[:space:]]*(-e[[:space:]]|[^#]+[[:space:]]+@[[:space:]]+file://)' requirements.txt > "$TMP_REQ"

"$PYTHON_BIN" -m pip_audit -r "$TMP_REQ" --skip-editable

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

REQ_FILE="${1:-requirements-ci.txt}"
WHEEL_DIR="${2:-.runtime/wheels}"
FILTERED_REQ=".runtime/requirements-ci.filtered.txt"
REQUIRE_WHEEL="${LM_PROXY_CI_REQUIRE_TS_PACK_WHEEL:-0}"

mkdir -p "$(dirname "$FILTERED_REQ")"

PYTHON_BIN="${LM_PROXY_PYTHON:-${LM_PROXY_INDEX_PYTHON:-}}"
if [[ -z "$PYTHON_BIN" ]] && [[ -x "/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python" ]]; then
  PYTHON_BIN="/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

REQ_FILE_ENV="$REQ_FILE" "$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path

req_path = Path(os.environ["REQ_FILE_ENV"])
filtered_path = Path(".runtime/requirements-ci.filtered.txt")

lines = []
for raw in req_path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if "tree_sitter_language_pack" in line and "subdirectory=crates/ts-pack-python" in line:
        continue
    lines.append(raw)

filtered_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

if [[ -d "$WHEEL_DIR" ]]; then
  shopt -s nullglob
  wheels=("$WHEEL_DIR"/tree_sitter_language_pack-*.whl)
  shopt -u nullglob
  if (( ${#wheels[@]} > 0 )); then
    echo "[ci-deps] Installing ts-pack wheel artifact: ${wheels[0]}"
    "$PYTHON_BIN" -m pip install --no-deps "${wheels[0]}"
  elif [[ "$REQUIRE_WHEEL" == "1" || "$REQUIRE_WHEEL" == "true" || "$REQUIRE_WHEEL" == "yes" ]]; then
    echo "[ci-deps] ERROR: ts-pack wheel artifact required but not found in $WHEEL_DIR" >&2
    exit 1
  fi
elif [[ "$REQUIRE_WHEEL" == "1" || "$REQUIRE_WHEEL" == "true" || "$REQUIRE_WHEEL" == "yes" ]]; then
  echo "[ci-deps] ERROR: ts-pack wheel directory required but not found: $WHEEL_DIR" >&2
  exit 1
fi

echo "[ci-deps] Installing remaining Python dependencies from $FILTERED_REQ"
"$PYTHON_BIN" -m pip install -r "$FILTERED_REQ"

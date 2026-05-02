#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

REQ_FILE="${1:-requirements-ci.txt}"
WHEEL_DIR="${2:-.runtime/wheels}"
FILTERED_REQ=".runtime/requirements-ci.filtered.txt"

mkdir -p "$(dirname "$FILTERED_REQ")"

REQ_FILE_ENV="$REQ_FILE" python - <<'PY'
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
    pip install --no-deps "${wheels[0]}"
  fi
fi

pip install -r "$FILTERED_REQ"

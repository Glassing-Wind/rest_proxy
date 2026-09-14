#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

WHEEL_DIR="${1:-.runtime/wheels}"
mkdir -p "$WHEEL_DIR"
rm -f "$WHEEL_DIR"/tree_sitter_language_pack-*.whl

PYTHON_BIN="${LM_PROXY_PYTHON:-${LM_PROXY_INDEX_PYTHON:-}}"
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

TS_PACK_SPEC="$(
  "$PYTHON_BIN" - <<'PY'
from pathlib import Path

for raw in Path("requirements-ci.txt").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if "tree_sitter_language_pack" in line and "subdirectory=crates/ts-pack-python" in line:
        if line.startswith("-e "):
            line = line[3:].strip()
        print(line)
        break
    if line.startswith("git+https://") and "tree_sitter_language_pack" in line:
        print(line)
        break
else:
    raise SystemExit("tree_sitter_language_pack requirement not found in requirements-ci.txt")
PY
)"

echo "[ci-wheel] Building pinned ts-pack wheel from: $TS_PACK_SPEC"
"$PYTHON_BIN" -m pip wheel --wheel-dir "$WHEEL_DIR" "$TS_PACK_SPEC"

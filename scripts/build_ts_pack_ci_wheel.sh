#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

WHEEL_DIR="${1:-.runtime/wheels}"
mkdir -p "$WHEEL_DIR"
rm -f "$WHEEL_DIR"/tree_sitter_language_pack-*.whl

TS_PACK_SPEC="$(
  python - <<'PY'
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
pip wheel --wheel-dir "$WHEEL_DIR" "$TS_PACK_SPEC"

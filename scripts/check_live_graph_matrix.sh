#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${LM_PROXY_PYTHON:-${LM_PROXY_INDEX_PYTHON:-}}"
if [[ -z "$PYTHON_BIN" ]] && [[ -x "/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python" ]]; then
  PYTHON_BIN="/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

MATRIX=(
  "/Users/michaelmarler/Projects/rental"
  "/Users/michaelmarler/Projects/tree-sitter-language-pack"
  "/Users/michaelmarler/Projects/FrameCreator"
  "/Users/michaelmarler/draw-things-community"
)

WORKSPACES=()
for workspace in "${MATRIX[@]}"; do
  if [[ -d "$workspace" ]]; then
    WORKSPACES+=("$workspace")
  else
    echo "[live-graph-matrix] skipping missing workspace: $workspace"
  fi
done

if [[ ${#WORKSPACES[@]} -eq 0 ]]; then
  echo "[live-graph-matrix] no workspaces available"
  exit 1
fi

echo "[live-graph-matrix] python=$PYTHON_BIN"
echo "[live-graph-matrix] workspaces=${WORKSPACES[*]}"

cd "$ROOT_DIR"
"$PYTHON_BIN" "$ROOT_DIR/test_live_graph_tools.py" "${WORKSPACES[@]}" "$@"

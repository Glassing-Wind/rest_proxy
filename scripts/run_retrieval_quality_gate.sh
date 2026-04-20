#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${LM_PROXY_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]] && [[ -x "/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python" ]]; then
  PYTHON_BIN="/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python || command -v python3)"
fi

echo "[retrieval-quality-gate] python=$PYTHON_BIN"

cd "$ROOT_DIR"

echo "[retrieval-quality-gate] running tool-choice eval suite..."
"$PYTHON_BIN" "$ROOT_DIR/scripts/run_tool_choice_eval_suite.py"

echo "[retrieval-quality-gate] running live graph regressions..."
"$ROOT_DIR/scripts/run_live_graph_regressions.sh" "$@"

echo "[retrieval-quality-gate] running MCP tool parity smoke..."
"$ROOT_DIR/scripts/run_mcp_tool_parity_smoke.sh"

echo "[retrieval-quality-gate] all retrieval quality checks passed."

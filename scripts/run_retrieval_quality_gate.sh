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

echo "[retrieval-quality-gate] python=$PYTHON_BIN"

cd "$ROOT_DIR"
"$PYTHON_BIN" "$ROOT_DIR/scripts/check_brain_server_freshness.py" --restart-if-stale --quiet

echo "[retrieval-quality-gate] running MCP protocol lifecycle checks..."
"$PYTHON_BIN" "$ROOT_DIR/scripts/check_mcp_stdio_lifecycle.py"
"$PYTHON_BIN" "$ROOT_DIR/scripts/check_mcp_protocol.py"
"$ROOT_DIR/scripts/check_mcp_stale_session_restart.sh"
"$PYTHON_BIN" "$ROOT_DIR/scripts/check_mcp_roots.py"

echo "[retrieval-quality-gate] running tool-choice eval suite..."
"$PYTHON_BIN" "$ROOT_DIR/scripts/run_tool_choice_eval_suite.py"

echo "[retrieval-quality-gate] running live graph regressions..."
"$ROOT_DIR/scripts/run_live_graph_regressions.sh" "$@"

echo "[retrieval-quality-gate] running MCP tool parity smoke..."
"$ROOT_DIR/scripts/run_mcp_tool_parity_smoke.sh"

echo "[retrieval-quality-gate] all retrieval quality checks passed."

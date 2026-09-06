#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${LM_PROXY_PYTHON:-${LM_PROXY_INDEX_PYTHON:-${PYTHON_BIN:-}}}"
if [[ -z "$PYTHON_BIN" ]] && [[ -x "/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python" ]]; then
  PYTHON_BIN="/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi
DEFAULT_FIXTURE_WORKSPACE="$ROOT_DIR/tests/fixtures/enterprise_live_graph_fixture"

WORKSPACES=()
if [[ -n "${LM_PROXY_ENTERPRISE_LIVE_GRAPH_WORKSPACES:-}" ]]; then
  while IFS= read -r workspace; do
    [[ -z "$workspace" ]] && continue
    if [[ -d "$workspace" ]]; then
      WORKSPACES+=("$workspace")
    else
      echo "[enterprise-live-graph] skipping missing workspace: $workspace"
    fi
  done < <(printf '%s\n' "${LM_PROXY_ENTERPRISE_LIVE_GRAPH_WORKSPACES}" | tr ',' '\n')
fi

if [[ ${#WORKSPACES[@]} -eq 0 ]]; then
  if [[ -d "$DEFAULT_FIXTURE_WORKSPACE" ]]; then
    WORKSPACES+=("$DEFAULT_FIXTURE_WORKSPACE")
  else
    WORKSPACES+=("${GITHUB_WORKSPACE:-$ROOT_DIR}")
  fi
fi

echo "[enterprise-live-graph] python=$PYTHON_BIN"
echo "[enterprise-live-graph] workspaces=${WORKSPACES[*]}"

cd "$ROOT_DIR"
"$PYTHON_BIN" "$ROOT_DIR/test_live_graph_tools.py" --reindex --mode incremental "${WORKSPACES[@]}"

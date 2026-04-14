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

echo "[graph-check] Running parser fact precision checks..."
"$PYTHON_BIN" test_ts_pack_facts.py

echo "[graph-check] Running semantic indexer helper checks..."
"$PYTHON_BIN" test_index_workspace.py

echo "[graph-check] Running semantic Postgres row checks..."
"$PYTHON_BIN" test_store_embeddings.py

echo "[graph-check] Running duplication helper checks..."
"$PYTHON_BIN" test_duplication_helpers.py

echo "[graph-check] Running duplication query checks..."
"$PYTHON_BIN" test_duplication_queries.py

echo "[graph-check] Running duplication report checks..."
"$PYTHON_BIN" test_duplication_report.py

echo "[graph-check] Running semantic helper checks..."
"$PYTHON_BIN" test_semantic_helpers.py

echo "[graph-check] Running search summary checks..."
"$PYTHON_BIN" test_search_summaries.py

echo "[graph-check] Running raw graph query checks..."
"$PYTHON_BIN" test_graph_query.py

echo "[graph-check] Running graph utility checks..."
"$PYTHON_BIN" test_graph_utility.py

echo "[graph-check] Running graph core inline checks..."
"$PYTHON_BIN" test_graph_core_inline.py

echo "[graph-check] Running manifest coverage checks..."
"$PYTHON_BIN" test_manifest.py

echo "[graph-check] Running flow summary checks..."
"$PYTHON_BIN" test_flow_summary.py

echo "[graph-check] Running graph tool overview checks..."
"$PYTHON_BIN" test_graph_tools.py

echo "[graph-check] Running code intel graph tool checks..."
"$PYTHON_BIN" test_code_intel_tools.py

echo "[graph-check] Running code intel helper checks..."
"$PYTHON_BIN" test_code_intel_helpers.py

echo "[graph-check] All graph pipeline checks passed."

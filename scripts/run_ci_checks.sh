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

echo "[ci] Running Ruff on the gated Python surface..."
"$PYTHON_BIN" -m ruff check \
  memory/skeleton_extractor.py \
  memory/store_embeddings.py \
  test_docs_chunking.py \
  test_index_workspace.py \
  test_semantic_helpers.py \
  test_store_embeddings.py \
  test_ts_pack_contract.py \
  test_ts_pack_facts.py \
  tools/brain/docs/chunking.py \
  tools/brain/search/semantic_helpers.py

echo "[ci] Running GraphRAG regression suite..."
./scripts/check_graph_pipeline.sh

echo "[ci] Running MCP protocol checks..."
"$PYTHON_BIN" ./scripts/check_mcp_protocol.py
./scripts/check_mcp_stale_session_restart.sh

echo "[ci] Running contract and service-surface tests..."
"$PYTHON_BIN" test_cross_project_tools.py
"$PYTHON_BIN" test_dev_tools.py
"$PYTHON_BIN" test_docs_admin_family.py
"$PYTHON_BIN" test_docs_search_runtime.py
"$PYTHON_BIN" test_graph_bootstrap_driver.py
"$PYTHON_BIN" test_handlers_persistence.py
"$PYTHON_BIN" test_index_job_orchestration.py
"$PYTHON_BIN" test_index_status_runs.py
"$PYTHON_BIN" test_indexing_health_alignment.py
"$PYTHON_BIN" test_memory_mode.py
"$PYTHON_BIN" test_search_graph_query.py
"$PYTHON_BIN" test_store_embeddings.py
"$PYTHON_BIN" test_store_graph_ops.py
"$PYTHON_BIN" test_swift_doc_lookup.py
"$PYTHON_BIN" test_ts_pack_contract.py

echo "[ci] All gated checks passed."

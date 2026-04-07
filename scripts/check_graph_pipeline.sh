#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"

echo "[graph-check] Running parser fact precision checks..."
python3 test_ts_pack_facts.py

echo "[graph-check] Running semantic indexer helper checks..."
python3 test_index_workspace.py

echo "[graph-check] Running manifest coverage checks..."
python3 test_manifest.py

echo "[graph-check] Running asset graph checks..."
python3 test_asset_graph.py

echo "[graph-check] Running flow summary checks..."
python3 test_flow_summary.py

echo "[graph-check] Running graph tool overview checks..."
python3 test_graph_tools.py

echo "[graph-check] Running code intel graph tool checks..."
python3 test_code_intel_tools.py

echo "[graph-check] Running integrated graph pipeline fixture..."
python3 test_graph_pipeline_fixture.py

echo "[graph-check] All graph pipeline checks passed."

#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"

echo "[graph-check] Running parser fact precision checks..."
python3 test_ts_pack_facts.py

echo "[graph-check] Running semantic indexer helper checks..."
python3 test_index_workspace.py

echo "[graph-check] Running SourceKitten Swift helper checks..."
python3 test_sourcekitten_swift.py

echo "[graph-check] Running duplication helper checks..."
python3 test_duplication_helpers.py

echo "[graph-check] Running duplication query checks..."
python3 test_duplication_queries.py

echo "[graph-check] Running duplication report checks..."
python3 test_duplication_report.py

echo "[graph-check] Running semantic helper checks..."
python3 test_semantic_helpers.py

echo "[graph-check] Running search summary checks..."
python3 test_search_summaries.py

echo "[graph-check] Running raw graph query checks..."
python3 test_graph_query.py

echo "[graph-check] Running graph utility checks..."
python3 test_graph_utility.py

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

echo "[graph-check] Running code intel helper checks..."
python3 test_code_intel_helpers.py

echo "[graph-check] Running integrated graph pipeline fixture..."
python3 test_graph_pipeline_fixture.py

echo "[graph-check] All graph pipeline checks passed."

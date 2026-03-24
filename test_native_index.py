import tree_sitter_language_pack as ts_pack
import time
import os

# Neo4j credentials
URI = "bolt://localhost:7687"
USER = "neo4j"
PASS = "nilBog1768@N"
PROJECT_ID = "ts-pack-native-test"
WORKSPACE_ROOT = "/Users/michaelmarler/Projects/rest_proxy"

print(f"Starting Rust-native indexing for {WORKSPACE_ROOT}...")
start_time = time.time()

try:
    ts_pack.index_workspace(
        path=WORKSPACE_ROOT,
        project_id=PROJECT_ID,
        neo4j_uri=URI,
        neo4j_user=USER,
        neo4j_pass=PASS
    )
    
    end_time = time.time()
    duration = end_time - start_time
    print(f"Indexing completed in {duration:.2f} seconds.")
    
except Exception as e:
    print(f"Indexing failed: {e}")

#!/usr/bin/env python3
"""run_struct_index.py – Thin wrapper that calls the Rust-native ts-pack structural indexer.

Called by mcp_server.py as a subprocess so it runs in its own process with
a clean import namespace. This avoids mixing torch/sentence-transformer state
with the Rust extension's tokio runtime in the same process.

Usage:
    python run_struct_index.py <project_path> <project_id> \
        --manifest-file <path> [--neo4j-uri ...] [--neo4j-user ...] [--neo4j-pass ...]
"""
import sys
import os
import argparse

from dotenv import load_dotenv

_base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_base_dir, ".env"))

import tree_sitter_language_pack as ts_pack


def main() -> int:
    parser = argparse.ArgumentParser(description="Structural (Rust-native) indexer")
    parser.add_argument("project_path")
    parser.add_argument("project_id")
    parser.add_argument("--manifest-file", required=True)
    parser.add_argument("--neo4j-uri",  default=os.getenv("LM_PROXY_NEO4J_URI",      "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.getenv("LM_PROXY_NEO4J_USER",     "neo4j"))
    parser.add_argument("--neo4j-pass", default=os.getenv("LM_PROXY_NEO4J_PASSWORD", "password"))
    args = parser.parse_args()

    if not os.path.exists(args.manifest_file):
        print(f"[ts-pack:struct] ERROR: manifest not found: {args.manifest_file}", file=sys.stderr)
        return 1

    print(
        f"[ts-pack:struct] Starting — project={args.project_id} "
        f"manifest={args.manifest_file}",
        file=sys.stderr, flush=True,
    )

    try:
        files = ts_pack.index_workspace(
            path=args.project_path,
            project_id=args.project_id,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
            manifest_file=args.manifest_file,
        )
        print(
            f"[ts-pack:struct] Done — {len(files)} files indexed.",
            file=sys.stderr, flush=True,
        )
        return 0
    except Exception as exc:
        print(f"[ts-pack:struct] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

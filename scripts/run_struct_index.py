#!/usr/bin/env python3
"""Thin wrapper around the Rust-owned ts-pack structural index and finalizer."""

import argparse
import os
import sys

from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO_ROOT, ".env"))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tree_sitter_language_pack as ts_pack


def main() -> int:
    parser = argparse.ArgumentParser(description="Structural (Rust-native) indexer")
    parser.add_argument("project_path")
    parser.add_argument("project_id")
    parser.add_argument("--manifest-file", required=True)
    parser.add_argument(
        "--neo4j-uri", default=os.getenv("LM_PROXY_NEO4J_URI", "bolt://localhost:7687")
    )
    parser.add_argument(
        "--neo4j-user", default=os.getenv("LM_PROXY_NEO4J_USER", "neo4j")
    )
    parser.add_argument(
        "--neo4j-pass", default=os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")
    )
    parser.add_argument("--neo4j-db", default=os.getenv("LM_PROXY_NEO4J_DB", "proxy"))
    args = parser.parse_args()

    print(
        "[ts-pack:struct] NOTE: For aligned indexing, run the MCP tool "
        "index_workspace() which generates a shared manifest for struct/semantic.",
        file=sys.stderr,
        flush=True,
    )

    if not os.path.exists(args.manifest_file):
        print(
            f"[ts-pack:struct] ERROR: manifest not found: {args.manifest_file}",
            file=sys.stderr,
        )
        return 1

    print(
        f"[ts-pack:struct] Starting — project={args.project_id} "
        f"manifest={args.manifest_file}",
        file=sys.stderr,
        flush=True,
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
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(f"[ts-pack:struct] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1

    try:
        finalize = ts_pack.finalize_struct_graph(
            project_path=args.project_path,
            project_id=args.project_id,
            manifest_file=args.manifest_file,
            indexed_files=[str(fp) for fp in files],
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
            neo4j_db=args.neo4j_db,
        )
        if finalize.get("manifest_added"):
            print(
                f"[ts-pack:struct] Added {finalize.get('manifest_added')} manifest-only File nodes.",
                file=sys.stderr,
                flush=True,
            )
        if finalize.get("parsed_marked"):
            print(
                f"[ts-pack:struct] Marked {finalize.get('parsed_marked')} File nodes as parsed.",
                file=sys.stderr,
                flush=True,
            )
        if finalize.get("file_path_aliased"):
            print(
                f"[ts-pack:struct] Synced file_path alias on {finalize.get('file_path_aliased')} File nodes.",
                file=sys.stderr,
                flush=True,
            )
        enrichment = finalize.get("swift_enrichment") or {}
        if enrichment.get("enabled") and enrichment.get("available", True):
            print(
                "[ts-pack:struct] SourceKitten Swift enrichment "
                f"matched {enrichment.get('symbols', 0)} symbol(s) "
                f"across {enrichment.get('files', 0)} file(s).",
                file=sys.stderr,
                flush=True,
            )
        elif enrichment.get("enabled") and not enrichment.get("available", True):
            print(
                "[ts-pack:struct] SourceKitten Swift enrichment skipped — binary not available.",
                file=sys.stderr,
                flush=True,
            )
        print(
            f"[ts-pack:pagerank] Done — pagerank written to {finalize.get('pagerank', 0)} File nodes.",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"[ts-pack:leiden] Done — community written to {finalize.get('louvain', 0)} File nodes.",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"[ts-pack:betweenness] Done — betweenness written to {finalize.get('betweenness', 0)} File nodes.",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"[ts-pack:wcc] Done — {finalize.get('isolated', 0)} isolated File nodes marked.",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(
            f"[ts-pack:struct] WARNING: Rust graph finalization failed: {exc}",
            file=sys.stderr,
            flush=True,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())

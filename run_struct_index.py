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
import json
import argparse
from collections import defaultdict

from dotenv import load_dotenv

_base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_base_dir, ".env"))

import tree_sitter_language_pack as ts_pack


# ─────────────────────────────────────────────────────────────────────────────
# Swift call extraction via raw tree-sitter AST walk
# ─────────────────────────────────────────────────────────────────────────────

def _extract_swift_calls(
    abs_path: str,
    rel_path: str,
    parser,
) -> list[tuple[str, str, str, int]]:
    """
    Parse a Swift file and return all (caller, callee_name, rel_path, line) tuples.
    Uses tree-sitter call_expression nodes — no compiler required.
    Returns an empty list on any failure.
    """
    try:
        with open(abs_path, "rb") as fh:
            code = fh.read()
    except OSError:
        return []

    try:
        tree = parser.parse(code)
    except Exception:
        return []

    results: list[tuple[str, str, str, int]] = []

    def _name_from_node(node) -> str | None:
        """Extract a clean callee name from a call_expression's first child."""
        for child in node.children:
            t = child.type
            if t == "simple_identifier":
                return code[child.start_byte:child.end_byte].decode(errors="ignore")
            if t == "navigation_expression":
                # self.foo() or obj.method() — take the last identifier
                idents = [
                    c for c in child.children
                    if c.type == "simple_identifier"
                ]
                if idents:
                    raw = code[idents[-1].start_byte:idents[-1].end_byte].decode(errors="ignore")
                    return raw
        return None

    def _walk(node, current_caller: str | None):
        """Recursively walk the AST, tracking the enclosing function name."""
        ntype = node.type

        # Enter a new function/method/init scope
        new_caller = current_caller
        if ntype in (
            "function_declaration",
            "protocol_function_declaration",
            "init_declaration",
        ):
            # Find the function name (simple_identifier child)
            for child in node.children:
                if child.type == "simple_identifier":
                    new_caller = code[child.start_byte:child.end_byte].decode(errors="ignore")
                    break

        elif ntype == "function_declaration" and current_caller is None:
            # Top-level unnamed — skip
            pass

        # Record call sites
        if ntype == "call_expression" and new_caller:
            callee = _name_from_node(node)
            if callee and callee != new_caller:  # skip trivial self-recursion
                line = node.start_point[0] + 1
                results.append((new_caller, callee, rel_path, line))

        for child in node.children:
            _walk(child, new_caller)

    _walk(tree.root_node, None)
    return results


def _build_swift_call_edges(
    project_path: str,
    project_id: str,
    manifest: list[dict],
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str = "proxy",
) -> int:
    """
    Run Swift call extraction on all .swift files in the manifest,
    then write [:CALLS] edges into Neo4j.
    Returns the number of edges written.
    """
    swift_files = [
        m for m in manifest
        if m.get("rel_path", "").endswith(".swift")
    ]
    if not swift_files:
        return 0

    parser = ts_pack.get_parser("swift")

    # ── 1. Collect all call sites from every Swift file ───────────────────────
    # all_calls[caller_name] = [(callee_name, rel_path, line), ...]
    all_calls: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    # Build a name→rel_path lookup from the manifest
    # (multiple files may define the same name — we handle ambiguity below)
    name_to_files: dict[str, list[str]] = defaultdict(list)

    for entry in swift_files:
        rel   = entry["rel_path"]
        abs_p = entry.get("abs_path") or os.path.join(project_path, rel)
        calls = _extract_swift_calls(abs_p, rel, parser)
        for caller, callee, fp, line in calls:
            all_calls[caller].append((callee, fp, line))
        name_to_files[os.path.splitext(os.path.basename(rel))[0]].append(rel)

    # Also index the declared function names from ts-pack for resolution
    # We need to know: for a callee name, which file declares it?
    # Use a second AST pass to gather all declaration names.
    declared: dict[str, list[str]] = defaultdict(list)  # fn_name → [rel_paths]
    for entry in swift_files:
        rel   = entry["rel_path"]
        abs_p = entry.get("abs_path") or os.path.join(project_path, rel)
        try:
            with open(abs_p, "rb") as fh:
                code = fh.read()
            tree = parser.parse(code)
        except Exception:
            continue

        def _collect_decls(node):
            if node.type in ("function_declaration", "protocol_function_declaration", "init_declaration"):
                for child in node.children:
                    if child.type == "simple_identifier":
                        name = code[child.start_byte:child.end_byte].decode(errors="ignore")
                        declared[name].append(rel)
                        break
            for child in node.children:
                _collect_decls(child)
        _collect_decls(tree.root_node)

    # ── 2. Resolve edges ──────────────────────────────────────────────────────
    # Build NEO4J node ID format: {project_id}:func:{rel_path}:{name}
    def node_id(name: str, rel: str) -> str:
        return f"{project_id}:func:{rel}:{name}"

    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for caller_name, callees in all_calls.items():
        # Find which file declared caller_name
        caller_files = declared.get(caller_name, [])
        if not caller_files:
            continue
        # If ambiguous use all (same caller name in multiple files)
        for caller_fp in caller_files:
            caller_id = node_id(caller_name, caller_fp)
            for callee_name, call_fp, _line in callees:
                callee_files = declared.get(callee_name, [])
                if not callee_files:
                    continue
                # Prefer callee in same file; otherwise take unambiguous result
                if call_fp in callee_files:
                    callee_fp = call_fp
                elif len(callee_files) == 1:
                    callee_fp = callee_files[0]
                else:
                    continue  # ambiguous — skip to avoid false edges
                callee_id = node_id(callee_name, callee_fp)
                key = (caller_id, callee_id)
                if key not in seen:
                    seen.add(key)
                    edges.append({
                        "caller_name": caller_name,
                        "caller_fp":   caller_fp,
                        "callee_name": callee_name,
                        "callee_fp":   callee_fp,
                        "project_id":  project_id,
                    })

    if not edges:
        return 0

    # ── 3. Write edges to Neo4j ───────────────────────────────────────────────
    import neo4j as _neo4j

    driver = _neo4j.GraphDatabase.driver(
        neo4j_uri,
        auth=(neo4j_user, neo4j_pass),
    )
    BATCH = 500
    written = 0
    with driver.session(database=neo4j_db) as session:
        for i in range(0, len(edges), BATCH):
            batch = edges[i:i + BATCH]
            session.run("""
                UNWIND $batch AS e
                MATCH (caller {name: e.caller_name, project_id: e.project_id})
                      WHERE caller:Function OR caller:Method OR caller:Class
                MATCH (callee {name: e.callee_name, project_id: e.project_id})
                      WHERE callee:Function OR callee:Method OR callee:Class
                MERGE (caller)-[:CALLS]->(callee)
            """, batch=batch)
            written += len(batch)
    driver.close()
    return written


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Structural (Rust-native) indexer")
    parser.add_argument("project_path")
    parser.add_argument("project_id")
    parser.add_argument("--manifest-file", required=True)
    parser.add_argument("--neo4j-uri",  default=os.getenv("LM_PROXY_NEO4J_URI",      "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.getenv("LM_PROXY_NEO4J_USER",     "neo4j"))
    parser.add_argument("--neo4j-pass", default=os.getenv("LM_PROXY_NEO4J_PASSWORD", "password"))
    parser.add_argument("--neo4j-db",   default=os.getenv("LM_PROXY_NEO4J_DB",       "proxy"))
    args = parser.parse_args()

    if not os.path.exists(args.manifest_file):
        print(f"[ts-pack:struct] ERROR: manifest not found: {args.manifest_file}", file=sys.stderr)
        return 1

    print(
        f"[ts-pack:struct] Starting — project={args.project_id} "
        f"manifest={args.manifest_file}",
        file=sys.stderr, flush=True,
    )

    # Read manifest once so we can pass it to Swift call extraction
    try:
        with open(args.manifest_file) as fh:
            manifest: list[dict] = json.load(fh)
    except Exception as exc:
        print(f"[ts-pack:struct] ERROR reading manifest: {exc}", file=sys.stderr, flush=True)
        return 1

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
    except Exception as exc:
        print(f"[ts-pack:struct] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1

    # ── Swift call extraction pass ────────────────────────────────────────────
    swift_count = sum(1 for m in manifest if m.get("rel_path", "").endswith(".swift"))
    if swift_count > 0:
        print(
            f"[ts-pack:swift-calls] Extracting call edges from {swift_count} Swift files…",
            file=sys.stderr, flush=True,
        )
        try:
            edges_written = _build_swift_call_edges(
                project_path=args.project_path,
                project_id=args.project_id,
                manifest=manifest,
                neo4j_uri=args.neo4j_uri,
                neo4j_user=args.neo4j_user,
                neo4j_pass=args.neo4j_pass,
                neo4j_db=args.neo4j_db,
            )
            print(
                f"[ts-pack:swift-calls] Done — {edges_written} CALLS edges written.",
                file=sys.stderr, flush=True,
            )
        except Exception as exc:
            # Non-fatal — structural index succeeded, call graph is just missing
            print(
                f"[ts-pack:swift-calls] WARNING: call extraction failed (non-fatal): {exc}",
                file=sys.stderr, flush=True,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())

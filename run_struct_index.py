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


def _run_gds_pagerank(
    project_id: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str = "proxy",
) -> int:
    """
    Project the CALLS graph for this project into GDS memory, run PageRank,
    then write scores back to each symbol node.  Finally, aggregate symbol
    pagerank onto owning File nodes as `pagerank` (max of contained symbols).

    Returns the number of File nodes updated.
    """
    import neo4j as _neo4j

    graph_name = f"calls-{project_id}"
    driver = _neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

    try:
        with driver.session(database=neo4j_db) as session:
            session.run(
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
            )

            session.run(
                """
                MATCH (n {project_id: $pid})
                WHERE n:Function OR n:Class OR n:Struct OR n:Trait OR n:Enum
                OPTIONAL MATCH (n)-[:CALLS]->(m {project_id: $pid})
                WHERE m:Function OR m:Class OR m:Struct OR m:Trait OR m:Enum
                WITH gds.graph.project($name, n, m) AS g
                RETURN g.graphName AS graph, g.nodeCount AS nodes, g.relationshipCount AS rels
            """,
                name=graph_name,
                pid=project_id,
            )

            session.run(
                """
                CALL gds.pageRank.write($name, {
                    writeProperty:  'pagerank',
                    dampingFactor:  0.85,
                    maxIterations:  20,
                    tolerance:      0.0000001
                })
            """,
                name=graph_name,
            )

            result = session.run(
                """
                MATCH (f:File {project_id: $pid})-[:CONTAINS]->(s)
                WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
                  AND s.pagerank IS NOT NULL
                WITH f, max(s.pagerank) AS top_pr, sum(s.pagerank) AS sum_pr,
                     count(s) AS sym_count
                SET f.pagerank     = top_pr,
                    f.pagerank_sum = sum_pr
                RETURN count(f) AS updated
            """,
                pid=project_id,
            )

            record = result.single()
            updated = record["updated"] if record else 0

            session.run(
                "CALL gds.graph.drop($name, false) YIELD graphName", name=graph_name
            )

            return updated
    finally:
        driver.close()


def _run_gds_louvain(
    project_id: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str = "proxy",
) -> int:
    """
    Run Leiden community detection on the File-level graph.
    (Uses gds.leiden which solves Louvain's resolution-limit problem and
    guarantees internally well-connected communities.)

    Strategy (tries in order):
      1. IMPORTS edges
      2. CALLS edges
    If neither has >= 2 edges, skips silently.

    Writes `louvainCommunity` property to File nodes (name kept for back-compat).
    Returns the number of File nodes updated.
    """
    import neo4j as _neo4j

    graph_name = f"louvain-{project_id}"
    driver = _neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

    try:
        with driver.session(database=neo4j_db) as session:
            imports_check = session.run(
                "MATCH (a:File {project_id: $pid})-[:IMPORTS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
            ).single()["n"]

            calls_check = session.run(
                "MATCH (a:File {project_id: $pid})-[:CALLS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
            ).single()["n"]

            if imports_check >= 2:
                rel_type = "IMPORTS"
            elif calls_check >= 2:
                rel_type = "CALLS"
            else:
                return 0

            session.run(
                "CALL gds.graph.drop($name, false) YIELD graphName", name=graph_name
            )

            session.run(
                f"""
                MATCH (f:File {{project_id: $pid}})
                OPTIONAL MATCH (f)-[:{rel_type}]-(g:File {{project_id: $pid}})
                WITH gds.graph.project($name, f, g, {{}}, {{undirectedRelationshipTypes: ['*']}}) AS proj
                RETURN proj.graphName AS graph, proj.nodeCount AS nodes
            """,
                name=graph_name,
                pid=project_id,
            )

            session.run(
                """
                CALL gds.leiden.write($name, {
                    writeProperty: 'louvainCommunity',
                    gamma:         1.0
                })
            """,
                name=graph_name,
            )

            result = session.run(
                "MATCH (f:File {project_id: $pid}) WHERE f.louvainCommunity IS NOT NULL "
                "RETURN count(f) AS updated",
                pid=project_id,
            )
            record = result.single()
            updated = record["updated"] if record else 0

            session.run(
                "CALL gds.graph.drop($name, false) YIELD graphName", name=graph_name
            )
            return updated
    finally:
        driver.close()


def _run_gds_betweenness(
    project_id: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str = "proxy",
) -> int:
    """
    Run Betweenness Centrality on the File-level CALLS graph.

    Files with high betweenness are architectural bridges — they sit on
    the shortest paths between many other files.

    Writes `betweenness` property to File nodes.
    Returns the number of File nodes updated.
    """
    import neo4j as _neo4j

    graph_name = f"betweenness-{project_id}"
    driver = _neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

    try:
        with driver.session(database=neo4j_db) as session:
            calls_check = session.run(
                "MATCH (a:File {project_id: $pid})-[:CALLS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
            ).single()["n"]
            imports_check = session.run(
                "MATCH (a:File {project_id: $pid})-[:IMPORTS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
            ).single()["n"]

            if calls_check >= 2:
                rel_type = "CALLS"
            elif imports_check >= 2:
                rel_type = "IMPORTS"
            else:
                return 0

            session.run(
                "CALL gds.graph.drop($name, false) YIELD graphName", name=graph_name
            )

            session.run(
                f"""
                MATCH (f:File {{project_id: $pid}})
                OPTIONAL MATCH (f)-[:{rel_type}]-(g:File {{project_id: $pid}})
                WITH gds.graph.project($name, f, g, {{}}, {{undirectedRelationshipTypes: ['*']}}) AS proj
                RETURN proj.nodeCount AS nodes
            """,
                name=graph_name,
                pid=project_id,
            )

            session.run(
                """
                CALL gds.betweenness.write($name, {
                    writeProperty: 'betweenness',
                    samplingSize:  null
                })
            """,
                name=graph_name,
            )

            result = session.run(
                "MATCH (f:File {project_id: $pid}) WHERE f.betweenness IS NOT NULL "
                "RETURN count(f) AS updated",
                pid=project_id,
            )
            record = result.single()
            updated = record["updated"] if record else 0

            session.run(
                "CALL gds.graph.drop($name, false) YIELD graphName", name=graph_name
            )
            return updated
    finally:
        driver.close()


def _run_gds_wcc(
    project_id: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str = "proxy",
) -> int:
    """
    Run Weakly Connected Components (WCC) on the File-level graph.

    Files in singleton components are candidates for dead code or missing edges.
    Writes `wccComponent` (integer ID) and `isolated` (bool) to File nodes.

    Returns the number of isolated File nodes found.
    """
    import neo4j as _neo4j

    graph_name = f"wcc-{project_id}"
    driver = _neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

    try:
        with driver.session(database=neo4j_db) as session:
            calls_check = session.run(
                "MATCH (a:File {project_id: $pid})-[:CALLS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
            ).single()["n"]
            imports_check = session.run(
                "MATCH (a:File {project_id: $pid})-[:IMPORTS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
            ).single()["n"]

            if calls_check >= 2:
                rel_type = "CALLS"
            elif imports_check >= 2:
                rel_type = "IMPORTS"
            else:
                return 0

            session.run(
                "CALL gds.graph.drop($name, false) YIELD graphName", name=graph_name
            )

            session.run(
                f"""
                MATCH (f:File {{project_id: $pid}})
                OPTIONAL MATCH (f)-[:{rel_type}]-(g:File {{project_id: $pid}})
                WITH gds.graph.project($name, f, g, {{}}, {{undirectedRelationshipTypes: ['*']}}) AS proj
                RETURN proj.nodeCount AS nodes
            """,
                name=graph_name,
                pid=project_id,
            )

            session.run(
                """
                CALL gds.wcc.write($name, {
                    writeProperty: 'wccComponent'
                })
            """,
                name=graph_name,
            )

            session.run(
                """
                MATCH (f:File {project_id: $pid})
                WHERE f.wccComponent IS NOT NULL
                WITH f.wccComponent AS comp, collect(f) AS members
                WITH members, size(members) AS sz
                FOREACH (f IN members |
                    SET f.isolated = (sz = 1)
                )
            """,
                pid=project_id,
            )

            result = session.run(
                "MATCH (f:File {project_id: $pid}) WHERE f.isolated = true "
                "RETURN count(f) AS isolated",
                pid=project_id,
            )
            record = result.single()
            isolated = record["isolated"] if record else 0

            session.run(
                "CALL gds.graph.drop($name, false) YIELD graphName", name=graph_name
            )
            return isolated
    finally:
        driver.close()


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
        pr_written = _run_gds_pagerank(
            project_id=args.project_id,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
            neo4j_db=args.neo4j_db,
        )
        print(
            f"[ts-pack:pagerank] Done — pagerank written to {pr_written} File nodes.",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(
            f"[ts-pack:pagerank] WARNING: PageRank failed (non-fatal): {exc}",
            file=sys.stderr,
            flush=True,
        )

    try:
        lv_written = _run_gds_louvain(
            project_id=args.project_id,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
            neo4j_db=args.neo4j_db,
        )
        print(
            f"[ts-pack:leiden] Done — community written to {lv_written} File nodes.",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(
            f"[ts-pack:leiden] WARNING: Leiden failed (non-fatal): {exc}",
            file=sys.stderr,
            flush=True,
        )

    try:
        bw_written = _run_gds_betweenness(
            project_id=args.project_id,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
            neo4j_db=args.neo4j_db,
        )
        print(
            f"[ts-pack:betweenness] Done — betweenness written to {bw_written} File nodes.",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(
            f"[ts-pack:betweenness] WARNING: Betweenness failed (non-fatal): {exc}",
            file=sys.stderr,
            flush=True,
        )

    try:
        isolated = _run_gds_wcc(
            project_id=args.project_id,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
            neo4j_db=args.neo4j_db,
        )
        print(
            f"[ts-pack:wcc] Done — {isolated} isolated File nodes marked.",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(
            f"[ts-pack:wcc] WARNING: WCC failed (non-fatal): {exc}",
            file=sys.stderr,
            flush=True,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())

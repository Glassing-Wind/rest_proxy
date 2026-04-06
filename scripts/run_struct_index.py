#!/usr/bin/env python3
"""scripts/run_struct_index.py – Thin wrapper that calls the Rust-native ts-pack structural indexer.

Called by mcp_server.py as a subprocess so it runs in its own process with
a clean import namespace. This avoids mixing torch/sentence-transformer state
with the Rust extension's tokio runtime in the same process.

Usage:
    python scripts/run_struct_index.py <project_path> <project_id> \
        --manifest-file <path> [--neo4j-uri ...] [--neo4j-user ...] [--neo4j-pass ...]
"""

import os
import sys
import argparse
import uuid
import json
from neo4j import unit_of_work

from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO_ROOT, ".env"))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tree_sitter_language_pack as ts_pack

_NEO4J_WRITE_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_WRITE_TIMEOUT", "15.0"))
_NEO4J_GDS_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_GDS_TIMEOUT", "120.0"))
_NEO4J_TX_TIMEOUT_S = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
_NEO4J_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_NEO4J_META_BASE = {"source": "lm_proxy", "tool": "run_struct_index"}


def _execute_write(
    session,
    cypher: str,
    timeout: float | None = None,
    op: str | None = None,
    **params,
) -> None:
    metadata = dict(_NEO4J_META_BASE)
    op_value = op or "write"
    if _NEO4J_OP_PREFIX:
        op_value = f"{_NEO4J_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    def _tx(tx):
        result = tx.run(cypher, **params, timeout=timeout)
        result.consume()

    if hasattr(session, "execute_write"):
        session.execute_write(
            unit_of_work(timeout=_NEO4J_TX_TIMEOUT_S, metadata=metadata)(_tx)
        )
    else:
        unit_of_work(timeout=_NEO4J_TX_TIMEOUT_S, metadata=metadata)(_tx)(session)


def _execute_write_fetch(
    session,
    cypher: str,
    timeout: float | None = None,
    op: str | None = None,
    **params,
):
    metadata = dict(_NEO4J_META_BASE)
    op_value = op or "write_fetch"
    if _NEO4J_OP_PREFIX:
        op_value = f"{_NEO4J_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    def _tx(tx):
        result = tx.run(cypher, **params, timeout=timeout)
        record = result.single()
        result.consume()
        return record

    if hasattr(session, "execute_write"):
        return session.execute_write(
            unit_of_work(timeout=_NEO4J_TX_TIMEOUT_S, metadata=metadata)(_tx)
        )
    return unit_of_work(timeout=_NEO4J_TX_TIMEOUT_S, metadata=metadata)(_tx)(session)


def _execute_read_fetch(
    session,
    cypher: str,
    timeout: float | None = None,
    op: str | None = None,
    **params,
):
    metadata = dict(_NEO4J_META_BASE)
    op_value = op or "read_fetch"
    if _NEO4J_OP_PREFIX:
        op_value = f"{_NEO4J_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    def _tx(tx):
        result = tx.run(cypher, **params, timeout=timeout)
        record = result.single()
        result.consume()
        return record

    if hasattr(session, "execute_read"):
        return session.execute_read(
            unit_of_work(timeout=_NEO4J_TX_TIMEOUT_S, metadata=metadata)(_tx)
        )
    return unit_of_work(timeout=_NEO4J_TX_TIMEOUT_S, metadata=metadata)(_tx)(session)


def _execute_read_all(
    session,
    cypher: str,
    timeout: float | None = None,
    op: str | None = None,
    **params,
):
    metadata = dict(_NEO4J_META_BASE)
    op_value = op or "read_all"
    if _NEO4J_OP_PREFIX:
        op_value = f"{_NEO4J_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    def _tx(tx):
        result = tx.run(cypher, **params, timeout=timeout)
        rows = result.data()
        result.consume()
        return rows

    if hasattr(session, "execute_read"):
        return session.execute_read(
            unit_of_work(timeout=_NEO4J_TX_TIMEOUT_S, metadata=metadata)(_tx)
        )
    return unit_of_work(timeout=_NEO4J_TX_TIMEOUT_S, metadata=metadata)(_tx)(session)


def _graph_name(prefix: str, project_id: str) -> str:
    return f"{prefix}-{project_id}-{uuid.uuid4().hex[:8]}"


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

    graph_name = _graph_name("calls", project_id)
    driver = _neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

    try:
        with driver.session(database=neo4j_db) as session:
            _execute_write(
                session,
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_drop_calls",
            )

            _execute_write(
                session,
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
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_project_calls",
            )

            _execute_write(
                session,
                """
                CALL gds.pageRank.write($name, {
                    writeProperty:  'pagerank',
                    dampingFactor:  0.85,
                    maxIterations:  20,
                    tolerance:      0.0000001
                })
                """,
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_pagerank_write",
            )

            record = _execute_write_fetch(
                session,
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
                timeout=_NEO4J_WRITE_TIMEOUT_S,
                op="pagerank_aggregate",
            )
            updated = record["updated"] if record else 0

            _execute_write(
                session,
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_drop_calls",
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

    graph_name = _graph_name("louvain", project_id)
    driver = _neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

    try:
        with driver.session(database=neo4j_db) as session:
            calls_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_calls",
            )["n"]
            imports_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:IMPORTS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_imports",
            )["n"]
            assets_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:ASSET_LINKS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_assets",
            )["n"]
            api_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS_API]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_api",
            )["n"]
            service_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS_SERVICE]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_service",
            )["n"]
            db_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS_DB]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_db",
            )["n"]

            rels = [
                ("CALLS", calls_check),
                ("IMPORTS", imports_check),
                ("ASSET_LINKS", assets_check),
                ("CALLS_API", api_check),
                ("CALLS_SERVICE", service_check),
                ("CALLS_DB", db_check),
            ]
            if sum(n for _, n in rels) < 2:
                return 0

            _execute_write(
                session,
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_drop_files",
            )

            _execute_write(
                session,
                """
                MATCH (f:File {project_id: $pid})
                OPTIONAL MATCH (f)-[r]-(g:File {project_id: $pid})
                WHERE type(r) IN $rels
                WITH gds.graph.project($name, f, g, {}, {undirectedRelationshipTypes: ['*']}) AS proj
                RETURN proj.graphName AS graph, proj.nodeCount AS nodes
                """,
                name=graph_name,
                pid=project_id,
                rels=[r for r, _ in rels],
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_project_files",
            )

            _execute_write(
                session,
                """
                CALL gds.leiden.write($name, {
                    writeProperty: 'louvainCommunity',
                    gamma:         1.0
                })
            """,
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_leiden_write",
            )

            record = _execute_write_fetch(
                session,
                "MATCH (f:File {project_id: $pid}) WHERE f.louvainCommunity IS NOT NULL "
                "RETURN count(f) AS updated",
                pid=project_id,
                timeout=_NEO4J_WRITE_TIMEOUT_S,
                op="leiden_count",
            )
            updated = record["updated"] if record else 0

            _execute_write(
                session,
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_drop_files",
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

    graph_name = _graph_name("betweenness", project_id)
    driver = _neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

    try:
        with driver.session(database=neo4j_db) as session:
            calls_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_calls",
            )["n"]
            imports_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:IMPORTS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_imports",
            )["n"]
            assets_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:ASSET_LINKS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_assets",
            )["n"]
            api_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS_API]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_api",
            )["n"]
            service_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS_SERVICE]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_service",
            )["n"]
            db_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS_DB]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_db",
            )["n"]

            rels = [
                ("CALLS", calls_check),
                ("IMPORTS", imports_check),
                ("ASSET_LINKS", assets_check),
                ("CALLS_API", api_check),
                ("CALLS_SERVICE", service_check),
                ("CALLS_DB", db_check),
            ]
            if sum(n for _, n in rels) < 2:
                return 0

            _execute_write(
                session,
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_drop_files",
            )

            _execute_write(
                session,
                """
                MATCH (f:File {project_id: $pid})
                OPTIONAL MATCH (f)-[r]-(g:File {project_id: $pid})
                WHERE type(r) IN $rels
                WITH gds.graph.project($name, f, g, {}, {undirectedRelationshipTypes: ['*']}) AS proj
                RETURN proj.nodeCount AS nodes
                """,
                name=graph_name,
                pid=project_id,
                rels=[r for r, _ in rels],
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_project_files",
            )

            _execute_write(
                session,
                """
                CALL gds.betweenness.write($name, {
                    writeProperty: 'betweenness',
                    samplingSize:  null
                })
            """,
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_betweenness_write",
            )

            record = _execute_write_fetch(
                session,
                "MATCH (f:File {project_id: $pid}) WHERE f.betweenness IS NOT NULL "
                "RETURN count(f) AS updated",
                pid=project_id,
                timeout=_NEO4J_WRITE_TIMEOUT_S,
                op="betweenness_count",
            )
            updated = record["updated"] if record else 0

            _execute_write(
                session,
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_drop_files",
            )
            return updated
    finally:
        driver.close()


def _ensure_manifest_file_nodes(
    project_id: str,
    manifest_file: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str = "proxy",
) -> int:
    import neo4j as _neo4j

    try:
        with open(manifest_file, "r") as fh:
            manifest = json.load(fh)
    except OSError:
        return 0

    if not manifest:
        return 0

    driver = _neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            rows = _execute_read_all(
                session,
                "MATCH (f:File {project_id:$pid}) RETURN f.filepath AS fp",
                pid=project_id,
                op="manifest_existing_paths",
            )
            existing_paths = {rec["fp"] for rec in rows if rec.get("fp")}

            missing = []
            for entry in manifest:
                fp = entry.get("rel_path")
                if not fp or fp in existing_paths:
                    continue
                missing.append(
                    {
                        "id": f"{project_id}:file:{fp}",
                        "filepath": fp,
                        "name": os.path.basename(fp),
                    }
                )

            total = 0
            if missing:
                BATCH = 200
                for i in range(0, len(missing), BATCH):
                    batch = missing[i : i + BATCH]
                    _execute_write(
                        session,
                        """
                        UNWIND $batch AS row
                        MERGE (f {id: row.id})
                        ON CREATE SET
                            f:Node,
                            f:File,
                            f.project_id = $pid,
                            f.filepath = row.filepath,
                            f.name = row.name,
                            f.indexed_at = timestamp(),
                            f.parsed = false
                        SET f:Node, f:File
                        """,
                        batch=batch,
                        pid=project_id,
                        timeout=_NEO4J_WRITE_TIMEOUT_S,
                        op="ensure_manifest_files",
                    )
                    total += len(batch)

            # Ensure all manifest File nodes have Node label (for CONTAINS match).
            _execute_write(
                session,
                """
                MATCH (f:File {project_id:$pid})
                WHERE f.filepath IN $paths
                SET f:Node
                """,
                pid=project_id,
                paths=[
                    entry.get("rel_path") for entry in manifest if entry.get("rel_path")
                ],
                timeout=_NEO4J_WRITE_TIMEOUT_S,
                op="ensure_manifest_node_label",
            )
            return total
    finally:
        driver.close()


def _mark_manifest_parsed(
    project_id: str,
    parsed_paths: list[str],
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str = "proxy",
) -> int:
    import neo4j as _neo4j

    if not parsed_paths:
        return 0

    driver = _neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            record = _execute_write_fetch(
                session,
                """
                MATCH (f:File {project_id:$pid})
                WHERE f.filepath IN $paths AND (f.parsed IS NULL OR f.parsed = false)
                SET f.parsed = true
                RETURN count(f) AS updated
                """,
                pid=project_id,
                paths=parsed_paths,
                timeout=_NEO4J_WRITE_TIMEOUT_S,
                op="mark_manifest_parsed",
            )
            return record["updated"] if record else 0
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

    graph_name = _graph_name("wcc", project_id)
    driver = _neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

    try:
        with driver.session(database=neo4j_db) as session:
            calls_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_calls",
            )["n"]
            imports_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:IMPORTS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_imports",
            )["n"]
            assets_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:ASSET_LINKS]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_assets",
            )["n"]
            api_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS_API]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_api",
            )["n"]
            service_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS_SERVICE]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_service",
            )["n"]
            db_check = _execute_read_fetch(
                session,
                "MATCH (a:File {project_id: $pid})-[:CALLS_DB]->(b:File {project_id: $pid}) "
                "RETURN count(*) AS n",
                pid=project_id,
                op="rels_count_db",
            )["n"]

            rels = [
                ("CALLS", calls_check),
                ("IMPORTS", imports_check),
                ("ASSET_LINKS", assets_check),
                ("CALLS_API", api_check),
                ("CALLS_SERVICE", service_check),
                ("CALLS_DB", db_check),
            ]
            if sum(n for _, n in rels) < 2:
                return 0

            _execute_write(
                session,
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
            )

            _execute_write(
                session,
                """
                MATCH (f:File {project_id: $pid})
                OPTIONAL MATCH (f)-[r]-(g:File {project_id: $pid})
                WHERE type(r) IN $rels
                WITH gds.graph.project($name, f, g, {}, {undirectedRelationshipTypes: ['*']}) AS proj
                RETURN proj.nodeCount AS nodes
                """,
                name=graph_name,
                pid=project_id,
                rels=[r for r, _ in rels],
                timeout=_NEO4J_GDS_TIMEOUT_S,
            )

            _execute_write(
                session,
                """
                CALL gds.wcc.write($name, {
                    writeProperty: 'wccComponent'
                })
            """,
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_wcc_write",
            )

            _execute_write(
                session,
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
                timeout=_NEO4J_WRITE_TIMEOUT_S,
                op="wcc_mark_isolated",
            )

            record = _execute_read_fetch(
                session,
                "MATCH (f:File {project_id: $pid}) WHERE f.isolated = true "
                "RETURN count(f) AS isolated",
                pid=project_id,
                op="wcc_isolated_count",
            )
            isolated = record["isolated"] if record else 0

            _execute_write(
                session,
                "CALL gds.graph.drop($name, false) YIELD graphName",
                name=graph_name,
                timeout=_NEO4J_GDS_TIMEOUT_S,
                op="gds_drop_files",
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
        added = _ensure_manifest_file_nodes(
            project_id=args.project_id,
            manifest_file=args.manifest_file,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
            neo4j_db=args.neo4j_db,
        )
        if added:
            print(
                f"[ts-pack:struct] Added {added} manifest-only File nodes.",
                file=sys.stderr,
                flush=True,
            )
        parsed_paths = []
        try:
            root = os.path.abspath(args.project_path)
            for fp in files:
                try:
                    rel = os.path.relpath(str(fp), root)
                    parsed_paths.append(rel.replace(os.sep, "/"))
                except Exception:
                    continue
        except Exception:
            parsed_paths = []

        updated = _mark_manifest_parsed(
            project_id=args.project_id,
            parsed_paths=parsed_paths,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
            neo4j_db=args.neo4j_db,
        )
        if updated:
            print(
                f"[ts-pack:struct] Marked {updated} File nodes as parsed.",
                file=sys.stderr,
                flush=True,
            )
    except Exception as exc:
        print(
            f"[ts-pack:struct] WARNING: manifest-only File nodes failed: {exc}",
            file=sys.stderr,
            flush=True,
        )

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

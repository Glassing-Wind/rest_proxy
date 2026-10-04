#!/usr/bin/env python3
"""Probe native engines in disposable files; requires ladybug, lancedb and pyarrow.

Does not import the application, connect to services, or certify backend adoption.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
import json
from pathlib import Path
import platform
import tempfile


def probe() -> dict:
    import ladybug as lb
    import lancedb
    from lancedb.rerankers import RRFReranker
    from lancedb.index import FTS

    checks = {}

    def check(name, operation):
        try:
            details = operation()
            checks[name] = {"passed": bool(details), "details": details}
        except Exception as exc:
            checks[name] = {"passed": False, "error": str(exc)[:800]}

    with tempfile.TemporaryDirectory(prefix="native-embedded-") as temp:
        graph_path = str(Path(temp) / "graph.lbdb")
        db = lb.Database(graph_path, buffer_pool_size=64 * 1024 * 1024, max_num_threads=2)
        conn = lb.Connection(db)
        other = lb.Connection(db)
        try:
            conn.execute("CREATE NODE TABLE File(id STRING PRIMARY KEY, project_id STRING)")
            conn.execute("CREATE NODE TABLE Function(id STRING PRIMARY KEY, name STRING, "
                         "start_line INT64, end_line INT64, signature STRING)")
            conn.execute("CREATE REL TABLE CONTAINS(FROM File TO Function)")
            conn.execute("CREATE REL TABLE CALLS(FROM Function TO Function)")
            conn.execute("CREATE (:File {id:'f', project_id:'p'})")
            conn.execute("CREATE (:Function {id:'a', name:'authenticate', start_line:1, "
                         "end_line:2, signature:'authenticate()'})")
            conn.execute("CREATE (:Function {id:'b', name:'validate'})")
            conn.execute("MATCH (f:File), (s:Function {id:'a'}) CREATE (f)-[:CONTAINS]->(s)")
            conn.execute("MATCH (a:Function {id:'a'}),(b:Function {id:'b'}) CREATE (a)-[:CALLS]->(b)")
            check("graph_traversal", lambda: conn.execute(
                "MATCH (:File {id:'f'})-[:CONTAINS]->(:Function)-[:CALLS]->(b) RETURN b.name"
            ).get_all() == [["validate"]])

            def rollback():
                conn.execute("BEGIN TRANSACTION")
                try:
                    conn.execute("CREATE (:File {id:'rollback', project_id:'p'})")
                    raise RuntimeError("deliberate application failure")
                except RuntimeError:
                    conn.execute("ROLLBACK")
                return other.execute("MATCH (f:File {id:'rollback'}) RETURN f.id").get_all() == []

            check("explicit_transaction_rollback", rollback)

            def readers():
                def read(connection):
                    return connection.execute("MATCH (f:File) RETURN count(f)").get_all()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    return all(r == [[1]] for r in pool.map(read, [conn, other]))

            check("separate_connection_concurrent_reads", readers)
            source = Path(__file__).resolve().parents[1] / "tools/brain/code_intel/file_describe.py"
            tree = ast.parse(source.read_text())
            query = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                         and isinstance(n.value, str) and "RETURN head([label" in n.value)
            check("unmodified_describe_file_query", lambda: bool(
                conn.execute(query, {"fid": "f"}).get_all()))
            check("explicit_label_query", lambda: conn.execute(
                "MATCH (f:File {id:$fid})-[:CONTAINS]->(s:Function) "
                "RETURN s.name, s.start_line, s.end_line, s.signature", {"fid": "f"}
            ).get_all() == [["authenticate", 1, 2, "authenticate()"]])
        finally:
            other.close()
            conn.close()
            db.close()
        reopened = lb.Database(graph_path, buffer_pool_size=64 * 1024 * 1024)
        conn = lb.Connection(reopened)
        try:
            check("graph_reopen_persistence", lambda: conn.execute(
                "MATCH (f:File) RETURN count(f)").get_all() == [[1]])
        finally:
            conn.close()
            reopened.close()

        vector_path = str(Path(temp) / "vectors")
        store = lancedb.connect(vector_path)
        table = store.create_table("chunks", data=[
            {"id": "a", "project_id": "p", "content": "authenticate token", "vector": [1., 0., 0.]},
            {"id": "b", "project_id": "p", "content": "validate password", "vector": [0., 1., 0.]},
            {"id": "c", "project_id": "other", "content": "authenticate secret", "vector": [1., 0., 0.]},
        ])
        table.create_index("content", config=FTS(), replace=True)
        check("native_full_text", lambda: table.search("authenticate", query_type="fts")
              .where("project_id = 'p'", prefilter=True).to_list()[0]["id"] == "a")
        check("hybrid_rrf_project_filter", lambda: table.search(query_type="hybrid")
              .vector([1., 0., 0.]).text("authenticate").where("project_id = 'p'", prefilter=True)
              .rerank(RRFReranker()).limit(2).to_list()[0]["id"] == "a")
        table.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute([
            {"id": "a", "project_id": "p", "content": "updated authenticate token", "vector": [1., 0., 0.]}
        ])
        check("vector_upsert_idempotence", lambda: table.count_rows() == 3)
        table.delete("project_id = 'other'")
        check("project_deletion", lambda: table.count_rows() == 2)
        reopened = lancedb.connect(vector_path).open_table("chunks")
        check("vector_reopen_persistence", lambda: reopened.count_rows() == 2)

    return {
        "platform": {"system": platform.system(), "machine": platform.machine(),
                     "python": platform.python_version()},
        "versions": {name: version(name) for name in ("ladybug", "lancedb", "pyarrow")},
        "checks": checks,
        "native_engine_smoke_passed": all(v["passed"] for k, v in checks.items()
                                          if k != "unmodified_describe_file_query"),
        "unmodified_query_compatible": checks["unmodified_describe_file_query"]["passed"],
        "application_embedded_ready": False,
        "limits": ["Disposable synthetic fixtures; no external services used by this probe.",
                   "Does not exercise full application indexing, native ts-pack or MCP routing.",
                   "Concurrent read smoke only; no concurrent writer/crash or load validation.",
                   "No cross-platform execution or complete license audit."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))

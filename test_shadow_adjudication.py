"""Legacy cleanup and redaction checks; LM_PROXY_TEST_SHADOW_LIVE=1 enables unique live fixtures."""

import json
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from graphrag_core.indexing.failure_evidence import record_index_failure
from graphrag_core.indexing.shadow_admin import (
    adjudicate_and_delete, snapshot_digest, snapshot_shadow,
)


class FailureEvidenceTests(unittest.TestCase):
    def test_failure_status_outage_still_preserves_local_diagnostics(self):
        spec = importlib.util.spec_from_file_location("shadow_failure_status", "scripts/run_struct_index.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        args = SimpleNamespace(project_path="/tmp", project_id="fixture", neo4j_uri="unused",
                               neo4j_user="unused", neo4j_pass="never-log-this", neo4j_db="unused",
                               manifest_file="unused")
        with (mock.patch.object(module.ts_pack, "index_workspace", return_value=[]),
              mock.patch.object(module.ts_pack, "finalize_struct_graph", side_effect=RuntimeError("finalizer")),
              mock.patch.object(module, "_set_struct_run_status", side_effect=RuntimeError("status outage")),
              mock.patch.object(module, "record_index_failure") as record):
            with self.assertRaisesRegex(RuntimeError, "did not complete"):
                module._run_struct_index(args, "run", "fixture::shadow::run")
            self.assertEqual([call.args[2] for call in record.call_args_list],
                             ["finalization_or_publication", "failure_status_write"])

    def test_diagnostics_exclude_exception_text_and_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            exc = RuntimeError("incorrect authentication details too many password=secret-credential")
            path = record_index_failure("project", "run", "finalizer", exc, Path(root))
            payload = json.loads(path.read_text())
            self.assertEqual(payload["category"], "authentication_rate_limit")
            self.assertNotIn("secret-credential", path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_local_write_failure_is_best_effort(self):
        with tempfile.NamedTemporaryFile() as file:
            self.assertIsNone(record_index_failure("p", "r", "f", RuntimeError(), Path(file.name)))

    def test_snapshot_digest_detects_content_change(self):
        first = {"nodes": [{"properties": {"name": "a"}}]}
        second = {"nodes": [{"properties": {"name": "b"}}]}
        self.assertNotEqual(snapshot_digest(first), snapshot_digest(second))


@unittest.skipUnless(os.getenv("LM_PROXY_TEST_SHADOW_LIVE") == "1", "explicit live fixture opt-in")
class LegacyAdjudicationTests(unittest.TestCase):
    def setUp(self):
        from dotenv import load_dotenv
        from neo4j import GraphDatabase
        load_dotenv(Path(__file__).parent / ".env")
        self.driver = GraphDatabase.driver(os.getenv("LM_PROXY_NEO4J_URI", "bolt://127.0.0.1:7687"),
                                          auth=(os.getenv("LM_PROXY_NEO4J_USER", "neo4j"),
                                                os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")))
        self.project = "legacy-shadow-test-" + uuid.uuid4().hex
        self.old = self.project + ":old"
        self.new = self.project + ":new"
        self.ns = self.project + "::shadow::" + self.old
        self.session = self.driver.session(database=os.getenv("LM_PROXY_NEO4J_DB", "proxy"))
        self.params = dict(pid=self.project, old=self.old, new=self.new, ns=self.ns)
        self.evidence = {"failed": {"started_at": 1, "finished_at": 2},
                         "superseding": {"started_at": 3, "finished_at": 4}}
        self.query(
            "CREATE (:Project {id:$pid,project_id:$pid,struct_index_status:'done',struct_active_run_id:$new}) "
            "CREATE (:IndexRun {id:$old,project_id:$pid,phase:'struct',status:'struct_written',started_at:1000}) "
            "CREATE (:IndexRun {id:$new,project_id:$pid,phase:'struct',status:'done',started_at:3000,promoted_at:4000}) "
            "CREATE (a:Node {project_id:$ns,id:$ns,last_seen_run:$old}) "
            "CREATE (b:Node {project_id:$pid,id:$pid,name:'preserved'}) CREATE (a)-[:CALLS]->(b)"
        )
        self.snapshot = self.session.execute_read(lambda tx: snapshot_shadow(tx, self.ns))

    def query(self, query):
        return self.session.execute_write(lambda tx: tx.run(query, **self.params).data())

    def delete(self):
        return self.session.execute_write(lambda tx: adjudicate_and_delete(
            tx, namespace=self.ns, failed_run=self.old, superseding_run=self.new,
            snapshot=self.snapshot, evidence=self.evidence, decision_id=self.project))

    def tearDown(self):
        self.query("MATCH (n) WHERE n.project_id IN [$pid,$ns] DETACH DELETE n")
        self.session.close()
        self.driver.close()

    def test_deletes_only_staging_and_preserves_history_and_boundary_node(self):
        self.assertEqual(len(self.snapshot["relationships"]), 1)
        result = self.delete()
        self.assertEqual(result["nodes_deleted"], 1)
        self.assertEqual(result["relationships_deleted"], 1)
        self.assertEqual(self.query("MATCH (n:Node {project_id:$pid}) RETURN n.name AS name"),
                         [{"name": "preserved"}])
        self.assertEqual(self.query("MATCH (r:IndexRun {id:$old}) RETURN r.status AS status"),
                         [{"status": "struct_written"}])
        self.assertEqual(self.query("MATCH (a:ShadowAdjudication {project_id:$pid}) RETURN count(a) AS n"),
                         [{"n": 1}])

    def test_changed_preview_rolls_back_without_deletion(self):
        self.query("MATCH (n:Node {project_id:$ns}) SET n.name='changed'")
        with self.assertRaisesRegex(RuntimeError, "contents changed"):
            self.delete()
        self.assertEqual(self.query("MATCH (n:Node {project_id:$ns}) RETURN count(n) AS n"), [{"n": 1}])

    def test_active_writer_refuses_adjudication(self):
        self.query("CREATE (:ShadowRun {project_id:$pid,namespace:$ns,status:'running'})")
        with self.assertRaisesRegex(RuntimeError, "ownership, activity"):
            self.delete()
        self.assertEqual(self.query("MATCH (n:Node {project_id:$ns}) RETURN count(n) AS n"), [{"n": 1}])

    def test_mixed_node_provenance_refuses_adjudication(self):
        self.query("MATCH (n:Node {project_id:$ns}) SET n.last_seen_run='another-run'")
        self.snapshot = self.session.execute_read(lambda tx: snapshot_shadow(tx, self.ns))
        with self.assertRaisesRegex(RuntimeError, "provenance"):
            self.delete()


if __name__ == "__main__":
    unittest.main()

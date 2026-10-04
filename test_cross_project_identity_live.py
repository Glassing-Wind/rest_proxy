"""Opt-in Neo4j identity regression using production cross-project tracing queries.

Run: LM_PROXY_TEST_LIVE_NEO4J=1 python test_cross_project_identity_live.py
Requires Python 3.11+, neo4j and python-dotenv. Reads LM_PROXY_NEO4J_URI,
LM_PROXY_NEO4J_USER, LM_PROXY_NEO4J_PASSWORD and LM_PROXY_NEO4J_DB from the
process environment or repository .env. No embedding/Postgres service is needed.
All fixtures use random project IDs and live in one explicitly rolled-back
transaction; no existing nodes are changed and no fixture data is committed.
"""

import asyncio
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parent


class EmptyContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def connection(self):
        return self

    def cursor(self):
        return self

    async def execute(self, *args):
        pass

    async def fetchall(self):
        return []


@unittest.skipUnless(os.getenv("LM_PROXY_TEST_LIVE_NEO4J") == "1", "opt-in live Neo4j test")
class CrossProjectIdentityLiveTests(unittest.TestCase):
    def test_source_and_consumer_identity_with_export_alias(self):
        from dotenv import load_dotenv
        from neo4j import GraphDatabase

        load_dotenv(ROOT / ".env", override=False)
        prefix = "identity_regression_" + uuid4().hex
        ids = {key: prefix + "_" + key for key in ("source", "unrelated", "consumer", "other")}
        ids["shadow"] = ids["source"] + "::shadow::fixture"
        symbol = "shared_" + uuid4().hex
        alias = "public_" + uuid4().hex
        password = os.getenv("LM_PROXY_NEO4J_PASSWORD")
        self.assertTrue(password, "LM_PROXY_NEO4J_PASSWORD must be configured")
        driver = GraphDatabase.driver(
            os.getenv("LM_PROXY_NEO4J_URI", "bolt://127.0.0.1:7687"),
            auth=(os.getenv("LM_PROXY_NEO4J_USER", "neo4j"), password),
        )
        database = os.getenv("LM_PROXY_NEO4J_DB", "proxy")
        with driver, driver.session(database=database) as session:
            tx = session.begin_transaction(timeout=30, metadata={"test": "cross_project_identity"})
            try:
                self._create_fixtures(tx, ids, symbol, alias)
                asyncio.run(self._check_production_core(tx, ids, symbol, alias))
            finally:
                tx.rollback()
            remaining = session.run(
                "MATCH (n) WHERE n.project_id IN $ids RETURN count(n) AS count",
                ids=list(ids.values()),
            ).single()["count"]
            self.assertEqual(remaining, 0, "Fixture nodes must disappear after rollback")

    def _create_fixtures(self, tx, ids, symbol, alias):
        for namespace in ("source", "unrelated", "consumer", "shadow"):
            tx.run(
                "CREATE (:Function {project_id: $pid, name: $name, filepath: $path, start_line: 1})",
                pid=ids[namespace], name=symbol, path=namespace + "/definition.py",
            ).consume()
        tx.run(
            "MATCH (target:Function {project_id: $pid, name: $name}) "
            "CREATE (f:File {project_id: $pid, filepath: 'src/public.py'}) "
            "CREATE (f)-[:EXPORTS_SYMBOL_AS {name: $alias, line: 2}]->(target)",
            pid=ids["source"], name=symbol, alias=alias,
        ).consume()
        cases = [
            ("good_direct", "consumer", "source", "CALLS"),
            ("good_inferred", "consumer", "source", "CALLS_INFERRED"),
            ("wrong_source", "consumer", "unrelated", "CALLS"),
            ("wrong_local", "consumer", "consumer", "CALLS"),
            ("wrong_shadow", "consumer", "shadow", "CALLS"),
            ("wrong_consumer", "other", "source", "CALLS"),
        ]
        for name, caller_namespace, target_namespace, edge in cases:
            tx.run(
                "MATCH (target:Function {project_id: $target, name: $symbol}) "
                "CREATE (caller:Function {project_id: $caller, name: $name, "
                "filepath: $path, start_line: 10}) "
                f"CREATE (caller)-[:{edge}]->(target)",
                target=ids[target_namespace], caller=ids[caller_namespace],
                symbol=symbol, name=name, path="src/" + name + ".py",
            ).consume()

    async def _check_production_core(self, tx, ids, symbol, alias):
        helpers = types.ModuleType("_helpers")
        helpers.WorkspaceRegistry = types.SimpleNamespace(resolve_id=lambda value: ids[value])
        helpers.get_project_id = lambda value: ids[value]
        helpers.get_workspace_path = lambda value: value
        store = types.SimpleNamespace(open_pool=AsyncMock(), _pg_pool=EmptyContext())
        helpers.get_memory_modules = lambda: (store, None, None, None, None)
        bootstrap = types.ModuleType("graph_bootstrap")
        bootstrap._NEO4J_DB = "unused"
        bootstrap.require_driver = AsyncMock(return_value=types.SimpleNamespace(session=lambda **kw: EmptyContext()))
        embeddings = types.ModuleType("embedding_service")
        embeddings.get_embedding_service = lambda: types.SimpleNamespace(
            embed_batch_async=AsyncMock(return_value=[[0.0]])
        )
        captured = []

        async def execute_read(session, query, **params):
            operation = params.pop("op")
            rows = tx.run(query, **params).data()
            if operation == "trace_symbol_graph_usages":
                captured.append((query, params, rows))
            return rows

        with patch.dict(sys.modules, {
            "_helpers": helpers, "graph_bootstrap": bootstrap, "embedding_service": embeddings,
        }):
            spec = importlib.util.spec_from_file_location(
                "identity_test_core", ROOT / "memory" / "cross_project_trace.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for requested in (symbol, alias):
                with self.subTest(requested="direct" if requested == symbol else "export_alias"):
                    output = await module.trace_symbol_cross_project_core(
                        symbol_name=requested, source_workspace="source", target_workspace="consumer",
                        execute_read=execute_read,
                    )
                    query, params, rows = captured[-1]
                    self.assertEqual({row["caller_name"] for row in rows}, {"good_direct", "good_inferred"})
                    self.assertIn("good_direct", output)
                    self.assertNotIn("wrong_", output)
                    if requested == alias:
                        self.assertIn("ExportAlias", output)
                        self.assertIn(symbol, params["names"])
                    # Sensitivity control: reproducing the original unscoped query
                    # must reveal the misleading same-name source/local/shadow hits.
                    unscoped = query.replace("MATCH (target {project_id: $spid})", "MATCH (target)")
                    self.assertNotEqual(query, unscoped)
                    contaminated = {row["caller_name"] for row in tx.run(unscoped, **params).data()}
                    self.assertEqual(contaminated, {
                        "good_direct", "good_inferred", "wrong_source", "wrong_local", "wrong_shadow",
                    })


if __name__ == "__main__":
    unittest.main()

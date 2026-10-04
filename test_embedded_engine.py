"""Standalone embedded adapter smoke tests; not end-to-end backend parity."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from memory.embedded_kuzu import KuzuGraphDriver
from memory.embedded_lancedb import LanceVectorStore
import graph_bootstrap


class EmbeddedKuzuTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_kuzu.db")
        self.driver = KuzuGraphDriver(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_node_and_relationship_lifecycle(self):
        async def run_test():
            session = self.driver.session()
            async with session:
                # 1. Create nodes
                await session.run("CREATE (f:File {id: 'f1', path: 'auth/login.py', project_id: 'p1'})")
                await session.run("CREATE (fn1:Function {id: 'fn1', name: 'login', signature: '(user, pwd)', start_line: 10, end_line: 25, project_id: 'p1'})")
                await session.run("CREATE (fn2:Function {id: 'fn2', name: 'verify_password', signature: '(pwd)', start_line: 30, end_line: 45, project_id: 'p1'})")

                # 2. Create relationships
                await session.run("MATCH (f:File {id: 'f1'}), (fn:Function {id: 'fn1'}) CREATE (f)-[:CONTAINS]->(fn)")
                await session.run("MATCH (a:Function {id: 'fn1'}), (b:Function {id: 'fn2'}) CREATE (a)-[:CALLS]->(b)")

                # 3. Query CONTAINS
                res = await session.run("MATCH (f:File)-[:CONTAINS]->(fn:Function) RETURN f.path AS path, fn.name AS name")
                rows = await res.data()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["path"], "auth/login.py")
                self.assertEqual(rows[0]["name"], "login")

                # 4. Multi-hop call chain traversal
                call_res = await session.run(
                    "MATCH (caller:Function {name: $caller_name})-[:CALLS*1..2]->(target:Function) RETURN target.name AS callee",
                    caller_name="login",
                )
                call_rows = await call_res.data()
                self.assertEqual(len(call_rows), 1)
                self.assertEqual(call_rows[0]["callee"], "verify_password")

        asyncio.run(run_test())


class EmbeddedLanceDBTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = LanceVectorStore(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_vector_and_keyword_search(self):
        async def run_test():
            # 1. Upsert chunks
            chunks = [
                {
                    "id": "c1",
                    "project_id": "proj_a",
                    "file_path": "security/auth.py",
                    "chunk_index": 0,
                    "content": "def authenticate_user(token): return jwt.verify(token)",
                    "vector": [0.1] * 768,
                    "metadata": {"roles": ["security", "auth"]},
                },
                {
                    "id": "c2",
                    "project_id": "proj_a",
                    "file_path": "db/postgres.py",
                    "chunk_index": 0,
                    "content": "def connect_database(dsn): return asyncpg.connect(dsn)",
                    "vector": [0.9] * 768,
                    "metadata": {"roles": ["database"]},
                },
            ]
            count = await self.store.upsert_chunks(chunks)
            self.assertEqual(count, 2)

            # 2. Vector search (close to 0.1)
            results = await self.store.search_vector([0.11] * 768, project_id="proj_a", limit=1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["id"], "c1")
            self.assertIn("authenticate_user", results[0]["content"])

            # 3. Keyword / text search
            text_results = await self.store.search_text("connect_database", project_id="proj_a", limit=1)
            self.assertEqual(len(text_results), 1)
            self.assertEqual(text_results[0]["id"], "c2")

            # 4. Get file chunks
            file_chunks = await self.store.get_chunks_for_file("proj_a", "security/auth.py")
            self.assertEqual(len(file_chunks), 1)
            self.assertEqual(file_chunks[0]["file_path"], "security/auth.py")

            # 5. Delete project
            await self.store.delete_project("proj_a")
            remaining = await self.store.count("proj_a")
            self.assertEqual(remaining, 0)

        asyncio.run(run_test())


class GraphBootstrapEmbeddedSwitchTests(unittest.TestCase):
    def test_require_driver_embedded_backend(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as tmpdir:
                driver = KuzuGraphDriver(os.path.join(tmpdir, 'graph.db'))
                try:
                    with mock.patch.dict(os.environ, {'LM_PROXY_STORAGE_BACKEND': 'embedded'}), \
                         mock.patch.object(graph_bootstrap, '_driver', None), \
                         mock.patch('memory.embedded_kuzu.get_embedded_kuzu_driver', return_value=driver):
                        selected = await graph_bootstrap.require_driver()
                        self.assertIs(selected, driver)
                finally:
                    await driver.close()
        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()

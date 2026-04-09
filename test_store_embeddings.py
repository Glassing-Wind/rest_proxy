import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/memory/store_embeddings.py"


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def __init__(self):
        self.session_calls = []

    def session(self, **kwargs):
        self.session_calls.append(kwargs)
        return FakeSession()


def load_store_embeddings_module(*, pool_available=False, driver=None, neo4j_calls=None):
    spec = importlib.util.spec_from_file_location("store_embeddings_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")
    graph_bootstrap_mod.get_driver = lambda: driver
    graph_bootstrap_mod._NEO4J_DB = "proxy"

    memory_pkg = types.ModuleType("memory")
    store_core_mod = types.ModuleType("memory.store_core")
    store_core_mod._ENABLE_EMBEDDINGS = True
    store_core_mod._EXPECTED_EMBEDDING_DIM = 2
    store_core_mod._pg_pool_available = lambda: True
    store_core_mod._pool_available = lambda: pool_available
    store_core_mod._debug = lambda *args, **kwargs: None
    store_core_mod._pg_pool = None

    async def _neo4j_write(session, cypher, op, **params):
        if neo4j_calls is not None:
            neo4j_calls.append((session, cypher, op, params))

    store_core_mod._neo4j_write = _neo4j_write

    stub_modules = {
        "graph_bootstrap": graph_bootstrap_mod,
        "memory": memory_pkg,
        "memory.store_core": store_core_mod,
    }

    with mock.patch.dict(sys.modules, stub_modules):
        spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self):
        self.executemany_calls = []
        self.execute_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def executemany(self, query, rows):
        self.executemany_calls.append((query, rows))

    async def execute(self, query, params):
        self.execute_calls.append((query, params))


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor


class FakePool:
    def __init__(self, cursor):
        self._cursor = cursor

    def connection(self):
        return FakeConnection(self._cursor)


class StoreEmbeddingsTests(unittest.TestCase):
    def setUp(self):
        self.module = load_store_embeddings_module()

    def test_insert_embeddings_batch_uses_package_executor(self):
        cursor = FakeCursor()
        self.module.store_core._pg_pool = FakePool(cursor)

        fake_ts_pack = types.SimpleNamespace()
        captured = {}

        async def _execute_upsert(cursor_obj, batch, project_id, *, expected_dim=None, created_at=None):
            captured["project_id"] = project_id
            captured["expected_dim"] = expected_dim
            captured["created_at"] = created_at
            captured["batch"] = batch
            captured["cursor"] = cursor_obj
            return 1

        fake_ts_pack.execute_codebase_embedding_upsert = _execute_upsert
        self.module.ts_pack = fake_ts_pack

        batch = [
            {
                "ref_id": "chunk-1",
                "text": "hello",
                "vector": [0.1, 0.2],
                "metadata": {"file": "src/a.ts", "start_line": 14},
            }
        ]

        written = asyncio.run(
            self.module.insert_embeddings_batch("proj123", "proj123", batch, project_path=None)
        )

        self.assertEqual(written, 1)
        self.assertEqual(captured["project_id"], "proj123")
        self.assertEqual(captured["expected_dim"], 2)
        self.assertIs(captured["cursor"], cursor)
        self.assertEqual(captured["batch"][0]["metadata"]["start_line"], 14)
        self.assertEqual(len(cursor.executemany_calls), 0)

    def test_insert_codebase_embedding_uses_package_executor(self):
        cursor = FakeCursor()
        self.module.store_core._pg_pool = FakePool(cursor)

        fake_ts_pack = types.SimpleNamespace()
        captured = {}

        async def _execute_upsert(cursor_obj, batch, project_id, *, expected_dim=None, created_at=None):
            captured["cursor"] = cursor_obj
            captured["batch"] = batch
            captured["project_id"] = project_id
            captured["expected_dim"] = expected_dim
            return 1

        fake_ts_pack.execute_codebase_embedding_upsert = _execute_upsert
        self.module.ts_pack = fake_ts_pack

        ok = asyncio.run(
            self.module.insert_codebase_embedding(
                "chunk-1",
                "proj123",
                "src/a.ts",
                9,
                "hello",
                [0.1, 0.2],
                metadata={"language": "typescript"},
            )
        )

        self.assertTrue(ok)
        self.assertIs(captured["cursor"], cursor)
        self.assertEqual(captured["project_id"], "proj123")
        self.assertEqual(captured["expected_dim"], 2)
        self.assertEqual(captured["batch"][0]["metadata"]["file"], "src/a.ts")
        self.assertEqual(captured["batch"][0]["metadata"]["chunk_index"], 9)

    def test_insert_embeddings_batch_uses_single_managed_neo4j_link_write(self):
        neo4j_calls = []
        driver = FakeDriver()
        module = load_store_embeddings_module(
            pool_available=True,
            driver=driver,
            neo4j_calls=neo4j_calls,
        )
        cursor = FakeCursor()
        module.store_core._pg_pool = FakePool(cursor)

        fake_ts_pack = types.SimpleNamespace()

        async def _execute_upsert(cursor_obj, batch, project_id, *, expected_dim=None, created_at=None):
            return len(batch)

        fake_ts_pack.execute_codebase_embedding_upsert = _execute_upsert
        module.ts_pack = fake_ts_pack

        written = asyncio.run(
            module.insert_embeddings_batch(
                "sess1",
                "proj123",
                [
                    {"ref_id": "chunk-1", "text": "hello", "vector": [0.1, 0.2], "metadata": {}},
                    {"ref_id": "chunk-2", "text": "world", "vector": [0.3, 0.4], "metadata": {}},
                ],
            )
        )

        self.assertEqual(written, 2)
        self.assertEqual(len(driver.session_calls), 1)
        self.assertEqual(len(neo4j_calls), 1)
        _, cypher, op, params = neo4j_calls[0]
        self.assertEqual(op, "link_embedding_refs_batch")
        self.assertIn("UNWIND $items AS item", cypher)
        self.assertEqual(params["session_id"], "sess1")
        self.assertEqual(len(params["items"]), 2)


if __name__ == "__main__":
    unittest.main()

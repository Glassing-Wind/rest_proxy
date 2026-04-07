import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/memory/store_embeddings.py"


def load_store_embeddings_module():
    spec = importlib.util.spec_from_file_location("store_embeddings_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")
    graph_bootstrap_mod.get_driver = lambda: None
    graph_bootstrap_mod._NEO4J_DB = "proxy"

    memory_pkg = types.ModuleType("memory")
    store_core_mod = types.ModuleType("memory.store_core")
    store_core_mod._ENABLE_EMBEDDINGS = True
    store_core_mod._EXPECTED_EMBEDDING_DIM = 2
    store_core_mod._pg_pool_available = lambda: True
    store_core_mod._pool_available = lambda: False
    store_core_mod._debug = lambda *args, **kwargs: None
    store_core_mod._pg_pool = None

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

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def executemany(self, query, rows):
        self.executemany_calls.append((query, rows))


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

    def test_insert_embeddings_batch_uses_package_row_builder(self):
        cursor = FakeCursor()
        self.module.store_core._pg_pool = FakePool(cursor)

        fake_ts_pack = types.SimpleNamespace()
        captured = {}

        def _build_rows(batch, project_id, *, expected_dim=None, created_at=None):
            captured["project_id"] = project_id
            captured["expected_dim"] = expected_dim
            captured["created_at"] = created_at
            return [
                (
                    "chunk-1",
                    project_id,
                    "src/a.ts",
                    "code_chunk",
                    14,
                    "hello",
                    "[0.1,0.2]",
                    '{"file":"src/a.ts","start_line":14}',
                    created_at,
                )
            ]

        fake_ts_pack.build_codebase_embedding_rows = _build_rows
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
        self.assertEqual(len(cursor.executemany_calls), 1)
        _, rows = cursor.executemany_calls[0]
        self.assertEqual(rows[0][4], 14)


if __name__ == "__main__":
    unittest.main()

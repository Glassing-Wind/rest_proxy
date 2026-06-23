import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "memory" / "store_search.py"


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_module(debug_events, read_exc=None):
    spec = importlib.util.spec_from_file_location("store_search_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")
    graph_bootstrap_mod.get_driver = lambda: FakeDriver()
    graph_bootstrap_mod._NEO4J_DB = "proxy"

    store_core_mod = types.ModuleType("memory.store_core")
    store_core_mod._ENABLE_EMBEDDINGS = True
    store_core_mod._ENABLE_RETRIEVAL = True
    store_core_mod._pool_available = lambda: True
    store_core_mod._pg_pool_available = lambda: False
    store_core_mod._debug = lambda message, **fields: debug_events.append(
        (message, fields)
    )

    async def _neo4j_read(session, cypher, op, **params):
        if read_exc:
            raise read_exc
        return []

    store_core_mod._neo4j_read = _neo4j_read
    memory_pkg = types.ModuleType("memory")

    with mock.patch.dict(
        sys.modules,
        {
            "graph_bootstrap": graph_bootstrap_mod,
            "memory": memory_pkg,
            "memory.store_core": store_core_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


class StoreSearchTests(unittest.TestCase):
    def test_missing_memory_vector_index_is_non_error_fallback(self):
        debug_events = []
        module = load_module(
            debug_events,
            RuntimeError("There is no such vector schema index: memory_embeddings_vector"),
        )

        results = asyncio.run(
            module.search_similar_memory("repo", [0.1, 0.2], k=3)
        )

        self.assertEqual(results, [])
        self.assertIn(
            (
                "graph_search_similar_skipped",
                {
                    "session_id": "repo",
                    "reason": "memory_embeddings_vector_missing",
                },
            ),
            debug_events,
        )
        event_names = [event[0] for event in debug_events]
        self.assertNotIn("graph_search_similar_error", event_names)


if __name__ == "__main__":
    unittest.main()

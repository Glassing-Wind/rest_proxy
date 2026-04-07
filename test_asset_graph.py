import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/asset_graph.py"


def load_asset_graph_module():
    spec = importlib.util.spec_from_file_location("asset_graph_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")
    graph_bootstrap_mod._NEO4J_DB = "neo4j"

    async def _require_driver():
        return FakeDriver()

    graph_bootstrap_mod.require_driver = _require_driver

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (FakeMemoryStore, None, None, None, None)
    helpers_mod.get_project_id = lambda path: "proj123"

    with mock.patch.dict(
        sys.modules,
        {
            "graph_bootstrap": graph_bootstrap_mod,
            "_helpers": helpers_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


class FakeCursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class FakeConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor()


class FakePool:
    def connection(self):
        return FakeConnection()


class FakeMemoryStore:
    _pg_pool = FakePool()

    @staticmethod
    async def open_pool():
        return None

    @staticmethod
    def _pg_pool_available():
        return True


class AssetGraphTests(unittest.TestCase):
    def setUp(self):
        self.module = load_asset_graph_module()

    def test_routes_link_callers_to_handlers_without_self_call_edges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            caller = project_path / "src/public/assets/properties.js"
            handler = project_path / "src/api/leaseRoutes.ts"
            caller.parent.mkdir(parents=True, exist_ok=True)
            handler.parent.mkdir(parents=True, exist_ok=True)

            caller.write_text(
                'async function load(){ return fetch("/api/leases", { method: "POST" }); }\n',
                encoding="utf-8",
            )
            handler.write_text(
                'router.post("/api/leases", handler)\n',
                encoding="utf-8",
            )

            files = [
                {"fp": "src/public/assets/properties.js", "fid": "caller-file"},
                {"fp": "src/api/leaseRoutes.ts", "fid": "handler-file"},
            ]
            writes = []

            async def fake_execute_read(session, query, **kwargs):
                self.assertIn("MATCH (f:File", query)
                return files

            async def fake_execute_write(session, query, **kwargs):
                writes.append((query, kwargs))

            with mock.patch.object(self.module, "get_project_id", return_value="proj123"):
                with mock.patch.object(
                    self.module.graph_bootstrap, "require_driver", return_value=FakeDriver()
                ):
                    with mock.patch.object(
                        self.module, "get_memory_modules", return_value=(FakeMemoryStore, None, None, None, None)
                    ):
                        result = asyncio.run(
                            self.module.build_asset_graph(
                                str(project_path),
                                fake_execute_read,
                                fake_execute_write,
                                lambda *args, **kwargs: None,
                                asyncio.Semaphore(10),
                                batch_size=100,
                                write_timeout_s=5.0,
                            )
                        )

        self.assertIn("CALLS_API_ROUTE edges", result)

        route_batches = [
            kwargs["batch"]
            for query, kwargs in writes
            if "CALLS_API_ROUTE" in query and "UNWIND $batch" in query
        ]
        self.assertEqual(len(route_batches), 1)
        self.assertEqual(
            route_batches[0],
            [{"src": "caller-file", "path": "/api/leases", "method": "POST", "project_id": "proj123"}],
        )

        handler_batches = [
            kwargs["batch"]
            for query, kwargs in writes
            if "HANDLED_BY" in query and "UNWIND $batch" in query
        ]
        self.assertEqual(len(handler_batches), 1)
        self.assertEqual(
            handler_batches[0],
            [{"path": "/api/leases", "method": "POST", "tgt": "handler-file", "project_id": "proj123"}],
        )

        broad_batches = [
            kwargs["batch"]
            for query, kwargs in writes
            if "CALLS_API" in query and "CALLS_API_ROUTE" not in query and "UNWIND $batch" in query
        ]
        self.assertEqual(len(broad_batches), 1)
        self.assertEqual(broad_batches[0], [{"src": "caller-file", "tgt": "handler-file"}])


if __name__ == "__main__":
    unittest.main()

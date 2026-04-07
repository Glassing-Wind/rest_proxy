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

    def test_builds_external_api_and_swift_resource_edges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            js_file = project_path / "src/public/assets/client.js"
            swift_file = project_path / "ios/App/View.swift"
            asset_dir = project_path / "ios/App/Assets.xcassets/hero.imageset"
            color_dir = project_path / "ios/App/Assets.xcassets/brand.colorset"
            storyboard = project_path / "ios/App/Main.storyboard"
            xib = project_path / "ios/App/HeroView.xib"
            js_file.parent.mkdir(parents=True, exist_ok=True)
            swift_file.parent.mkdir(parents=True, exist_ok=True)
            asset_dir.mkdir(parents=True, exist_ok=True)
            color_dir.mkdir(parents=True, exist_ok=True)
            js_file.write_text('await fetch("https://api.example.com/v1/users")\n', encoding="utf-8")
            swift_file.write_text('let image = Image("hero")\n', encoding="utf-8")
            (asset_dir / "Contents.json").write_text("{}", encoding="utf-8")
            (color_dir / "Contents.json").write_text("{}", encoding="utf-8")
            storyboard.write_text("<storyboard></storyboard>\n", encoding="utf-8")
            xib.write_text("<xib></xib>\n", encoding="utf-8")

            files = [
                {"fp": "src/public/assets/client.js", "fid": "js-file"},
                {"fp": "ios/App/View.swift", "fid": "swift-file"},
            ]
            writes = []

            async def fake_execute_read(session, query, **kwargs):
                return files

            async def fake_execute_write(session, query, **kwargs):
                writes.append((query, kwargs))

            with mock.patch.object(self.module, "get_project_id", return_value="proj123"):
                with mock.patch.object(
                    self.module.graph_bootstrap, "require_driver", return_value=FakeDriver()
                ):
                    with mock.patch.object(
                        self.module,
                        "_load_file_facts",
                        return_value={
                            "src/public/assets/client.js": {},
                            "ios/App/View.swift": {
                                "resource_refs": [
                                    {"kind": "image", "name": "hero", "callee": "Image"},
                                    {"kind": "color", "name": "brand", "callee": "Color"},
                                    {"kind": "storyboard", "name": "Main", "callee": "UIStoryboard"},
                                    {"kind": "nib", "name": "HeroView", "callee": "UINib"},
                                ]
                            },
                        },
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

        self.assertIn("CALLS_API_EXTERNAL edges", result)
        self.assertIn("resource edges", result)

        external_batches = [
            kwargs["batch"]
            for query, kwargs in writes
            if "CALLS_API_EXTERNAL" in query and "UNWIND $batch" in query
        ]
        self.assertEqual(external_batches, [[{"src": "js-file", "url": "https://api.example.com/v1/users", "project_id": "proj123"}]])

        resource_batches = [
            (query, kwargs["batch"])
            for query, kwargs in writes
            if "MERGE (res:Resource" in query and "UNWIND $batch" in query
        ]
        self.assertEqual(len(resource_batches), 4)
        by_rel = {}
        for query, batch in resource_batches:
            if "USES_ASSET" in query:
                by_rel["USES_ASSET"] = batch
            elif "USES_COLOR_ASSET" in query:
                by_rel["USES_COLOR_ASSET"] = batch
            elif "USES_STORYBOARD" in query:
                by_rel["USES_STORYBOARD"] = batch
            elif "USES_XIB" in query:
                by_rel["USES_XIB"] = batch

        self.assertEqual(
            by_rel["USES_ASSET"],
            [{
                "src": "swift-file",
                "name": "hero",
                "filepath": "ios/App/Assets.xcassets/hero.imageset/Contents.json",
                "kind": "image",
                "project_id": "proj123",
            }],
        )
        self.assertEqual(
            by_rel["USES_COLOR_ASSET"],
            [{
                "src": "swift-file",
                "name": "brand",
                "filepath": "ios/App/Assets.xcassets/brand.colorset/Contents.json",
                "kind": "color",
                "project_id": "proj123",
            }],
        )
        self.assertEqual(
            by_rel["USES_STORYBOARD"],
            [{
                "src": "swift-file",
                "name": "Main",
                "filepath": "ios/App/Main.storyboard",
                "kind": "storyboard",
                "project_id": "proj123",
            }],
        )

        self.assertEqual(
            by_rel["USES_XIB"],
            [{
                "src": "swift-file",
                "name": "HeroView",
                "filepath": "ios/App/HeroView.xib",
                "kind": "nib",
                "project_id": "proj123",
            }],
        )

        backing_batches = [
            kwargs["batch"]
            for query, kwargs in writes
            if "BACKED_BY_FILE" in query and "UNWIND $batch" in query
        ]
        self.assertEqual(len(backing_batches), 1)
        self.assertEqual(
            {
                tuple(sorted(item.items()))
                for item in backing_batches[0]
            },
            {
                tuple(
                    sorted(
                        {
                            "name": "hero",
                            "kind": "image",
                            "filepath": "ios/App/Assets.xcassets/hero.imageset/Contents.json",
                            "project_id": "proj123",
                        }.items()
                    )
                ),
                tuple(
                    sorted(
                        {
                            "name": "brand",
                            "kind": "color",
                            "filepath": "ios/App/Assets.xcassets/brand.colorset/Contents.json",
                            "project_id": "proj123",
                        }.items()
                    )
                ),
                tuple(
                    sorted(
                        {
                            "name": "Main",
                            "kind": "storyboard",
                            "filepath": "ios/App/Main.storyboard",
                            "project_id": "proj123",
                        }.items()
                    )
                ),
                tuple(
                    sorted(
                        {
                            "name": "HeroView",
                            "kind": "nib",
                            "filepath": "ios/App/HeroView.xib",
                            "project_id": "proj123",
                        }.items()
                    )
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()

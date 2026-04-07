import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/flow_summary.py"
APPLE_MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/flow_summary_apple.py"


def load_flow_summary_module():
    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: "proj123"

    graph_pkg = types.ModuleType("tools")
    brain_pkg = types.ModuleType("tools.brain")
    graph_subpkg = types.ModuleType("tools.brain.graph")
    core_mod = types.ModuleType("tools.brain.graph.core")

    async def _execute_read(*args, **kwargs):
        return []

    core_mod._execute_read = _execute_read

    apple_spec = importlib.util.spec_from_file_location(
        "tools.brain.graph.flow_summary_apple", APPLE_MODULE_PATH
    )
    apple_module = importlib.util.module_from_spec(apple_spec)
    assert apple_spec.loader is not None

    spec = importlib.util.spec_from_file_location("flow_summary_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "tools": graph_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.graph": graph_subpkg,
            "tools.brain.graph.core": core_mod,
            "tools.brain.graph.flow_summary_apple": apple_module,
        },
    ):
        apple_spec.loader.exec_module(apple_module)
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


class FlowSummaryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_flow_summary_module()

    def test_prefer_concrete_app_rows_drops_broad_duplicates(self):
        rows = [
            (
                "src/public/properties.html",
                "src/public/assets/properties.js",
                "POST /api/leases",
                "src/api/leaseRoutes.ts",
                "src/services/leaseService.ts",
                None,
                None,
                None,
            ),
            (
                "src/public/properties.html",
                "src/public/assets/properties.js",
                None,
                "src/api/leaseRoutes.ts",
                "src/services/leaseService.ts",
                None,
                None,
                None,
            ),
        ]
        filtered = self.module._prefer_concrete_app_rows(rows)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0][2], "POST /api/leases")

    def test_get_app_flow_summary_prefers_route_rows_in_output(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_app_flow_summary":
                return [
                    {
                        "ui": "src/public/properties.html",
                        "js": "src/public/assets/properties.js",
                        "route": "POST /api/leases",
                        "api": "src/api/leaseRoutes.ts",
                        "svc": "src/services/leaseService.ts",
                        "model": None,
                        "schema": None,
                        "external": None,
                    },
                    {
                        "ui": "src/public/properties.html",
                        "js": "src/public/assets/properties.js",
                        "route": None,
                        "api": "src/api/leaseRoutes.ts",
                        "svc": "src/services/leaseService.ts",
                        "model": None,
                        "schema": None,
                        "external": None,
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_app_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/rental",
                    include_coverage=False,
                    limit=20,
                    group_by_ui=True,
                )
            )

        self.assertIn("POST /api/leases", output)
        self.assertEqual(output.count("src/api/leaseRoutes.ts"), 1)
        self.assertNotIn(
            "src/public/properties.html -> src/public/assets/properties.js -> src/api/leaseRoutes.ts -> src/services/leaseService.ts",
            output,
        )

    def test_get_apple_build_summary_formats_target_aware_paths(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "apple_build_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "get_apple_build_summary":
                return [
                    {
                        "src": "ios/Widget/View.swift",
                        "rel": "USES_ASSET",
                        "resource": "hero",
                        "kind": "image",
                        "backing": "ios/App/Assets.xcassets/hero.imageset/Contents.json",
                        "target": "WidgetExtension",
                        "project_file": "ios/Widget.xcodeproj/project.pbxproj",
                        "scheme": "Widget",
                        "scheme_file": "ios/Widget.xcodeproj/xcshareddata/xcschemes/Widget.xcscheme",
                        "workspace": "ios/App.xcworkspace/contents.xcworkspacedata",
                    },
                    {
                        "src": "ios/App/View.swift",
                        "rel": "USES_ASSET",
                        "resource": "hero",
                        "kind": "image",
                        "backing": "ios/App/Assets.xcassets/hero.imageset/Contents.json",
                        "target": "App",
                        "project_file": "ios/App.xcodeproj/project.pbxproj",
                        "scheme": "App",
                        "scheme_file": "ios/App.xcodeproj/xcshareddata/xcschemes/App.xcscheme",
                        "workspace": "ios/App.xcworkspace/contents.xcworkspacedata",
                    }
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_apple_build_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/framecreator",
                    target_contains="App",
                    limit=20,
                    as_table=False,
                    group_by="target",
                )
            )

        self.assertIn("Target: App", output)
        self.assertIn("ios/App/View.swift", output)
        self.assertIn("USES_ASSET:hero", output)
        self.assertIn("target=App", output)
        self.assertIn("scheme=App", output)
        self.assertIn("workspace=ios/App.xcworkspace/contents.xcworkspacedata", output)
        self.assertNotIn("WidgetExtension", output)

    def test_get_apple_build_summary_groups_by_scheme(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "apple_build_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "get_apple_build_summary":
                return [
                    {
                        "src": "ios/App/View.swift",
                        "rel": "USES_ASSET",
                        "resource": "hero",
                        "kind": "image",
                        "backing": "ios/App/Assets.xcassets/hero.imageset/Contents.json",
                        "target": "App",
                        "project_file": "ios/App.xcodeproj/project.pbxproj",
                        "scheme": "App",
                        "scheme_file": "ios/App.xcodeproj/xcshareddata/xcschemes/App.xcscheme",
                        "workspace": "ios/App.xcworkspace/contents.xcworkspacedata",
                    },
                    {
                        "src": "ios/App/Settings.swift",
                        "rel": "USES_COLOR_ASSET",
                        "resource": "brand",
                        "kind": "color",
                        "backing": "ios/App/Assets.xcassets/brand.colorset/Contents.json",
                        "target": "App",
                        "project_file": "ios/App.xcodeproj/project.pbxproj",
                        "scheme": "App",
                        "scheme_file": "ios/App.xcodeproj/xcshareddata/xcschemes/App.xcscheme",
                        "workspace": "ios/App.xcworkspace/contents.xcworkspacedata",
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_apple_build_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/framecreator",
                    limit=20,
                    as_table=False,
                    group_by="scheme",
                )
            )

        self.assertIn("Scheme: App", output)
        self.assertIn("ios/App/View.swift", output)
        self.assertIn("ios/App/Settings.swift", output)

    def test_get_apple_build_summary_skips_non_apple_repo(self):
        seen_ops = []

        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            seen_ops.append(op)
            if op == "apple_build_presence":
                return [{"n": 0}]
            if op == "get_apple_build_summary":
                raise AssertionError("unexpected Apple build query on non-Apple repo")
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_apple_build_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/rental",
                    limit=20,
                    as_table=False,
                    group_by="target",
                )
            )

        self.assertEqual("No Apple build graph paths found.", output)
        self.assertEqual(["apple_build_presence"], seen_ops)


if __name__ == "__main__":
    unittest.main()

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
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/repo"

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

    def test_format_app_flow_row_dedupes_direct_js_entry(self):
        formatted = self.module._format_app_flow_row(
            "src/public/assets/financial-summary.js",
            "src/public/assets/financial-summary.js",
            "GET /api/financials/tax-package",
            "src/api/routes/financeAdminRoutes.ts",
            None,
            None,
            None,
            None,
        )
        self.assertEqual(
            "src/public/assets/financial-summary.js -> GET /api/financials/tax-package -> src/api/routes/financeAdminRoutes.ts",
            formatted,
        )

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

    def test_get_app_flow_summary_dedupes_same_ui_and_js_path(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_app_flow_summary":
                return [
                    {
                        "ui": "src/public/assets/financial-summary.js",
                        "js": "src/public/assets/financial-summary.js",
                        "route": "GET /api/financials/tax-package",
                        "api": "src/api/routes/financeAdminRoutes.ts",
                        "svc": None,
                        "model": None,
                        "schema": None,
                        "external": None,
                    }
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

        self.assertIn(
            "src/public/assets/financial-summary.js -> GET /api/financials/tax-package -> src/api/routes/financeAdminRoutes.ts",
            output,
        )
        self.assertNotIn(
            "src/public/assets/financial-summary.js -> src/public/assets/financial-summary.js -> GET /api/financials/tax-package",
            output,
        )

    def test_get_app_flow_summary_filters_test_like_rows_even_if_query_leaks_them(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_app_flow_summary":
                return [
                    {
                        "ui": "tests/routes.test.ts",
                        "js": "tests/routes.test.ts",
                        "route": "GET /api/applications",
                        "api": "src/api/routes/applicationOpsRoutes.ts",
                        "svc": None,
                        "model": None,
                        "schema": None,
                        "external": None,
                    },
                    {
                        "ui": "src/public/financial-summary.html",
                        "js": "src/public/assets/financial-summary.js",
                        "route": "GET /api/financials/tax-package",
                        "api": "src/api/routes/financeAdminRoutes.ts",
                        "svc": None,
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

        self.assertIn("src/public/financial-summary.html", output)

    def test_collapse_ambiguous_app_rows_summarizes_cross_product_joins(self):
        rows = [
            (
                "src/public/financial-summary.html",
                "src/public/assets/financial-summary.js",
                "GET /api/financials/tax-package",
                "src/api/routes/financeAdminRoutes.ts",
                "src/services/AccountingSyncBatchService.ts",
                "entries",
                "prisma/schema.prisma",
                None,
            ),
            (
                "src/public/financial-summary.html",
                "src/public/assets/financial-summary.js",
                "GET /api/financials/tax-package",
                "src/api/routes/financeAdminRoutes.ts",
                "src/services/TaxPackageService.ts",
                "sync",
                "prisma/schema.prisma",
                None,
            ),
        ]
        collapsed = self.module._collapse_ambiguous_app_rows(rows)
        self.assertEqual(len(collapsed), 1)
        ui, js, route, api, svc, model, schema, external = collapsed[0]
        self.assertEqual(ui, "src/public/financial-summary.html")
        self.assertEqual(route, "GET /api/financials/tax-package")
        self.assertEqual(api, "src/api/routes/financeAdminRoutes.ts")
        self.assertEqual(svc, "2 services")
        self.assertEqual(model, "2 models")
        self.assertEqual(schema, "prisma/schema.prisma")

    def test_get_app_flow_summary_falls_back_to_literal_api_paths_in_js(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_app_flow_summary":
                return []
            if op == "get_app_flow_summary_asset_pairs":
                return [
                    {
                        "ui": "src/public/financial-summary.html",
                        "js": "src/public/assets/financial-summary.js",
                    }
                ]
            if op == "get_app_flow_summary_route_catalog":
                return [
                    {
                        "path": "/api/financials/tax-package",
                        "method": "GET",
                        "api": "src/api/routes/financeAdminRoutes.ts",
                    }
                ]
            return []

        source_text = """
        async function api(path) {
          return fetch(path, { headers: { Authorization: `Bearer ${token}` } });
        }
        async function loadSummary() {
          return api(`/api/financials/tax-package?year=${year}`);
        }
        """

        with (
            mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read),
            mock.patch("builtins.open", mock.mock_open(read_data=source_text)),
        ):
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

        self.assertIn("src/public/financial-summary.html", output)
        self.assertIn("GET /api/financials/tax-package", output)
        self.assertIn("src/api/routes/financeAdminRoutes.ts", output)

    def test_get_apple_build_summary_formats_target_aware_paths(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "apple_build_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "graph_schema_labels":
                return [{"labels": ["Resource", "XcodeWorkspace"]}]
            if kwargs.get("op") == "graph_schema_relationship_types":
                return [{"rels": ["BUNDLED_IN_TARGET", "REFERENCES_PROJECT"]}]
            if kwargs.get("op") == "apple_resource_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "apple_workspace_presence":
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

    def test_apple_build_query_omits_missing_resource_relationships(self):
        query = self.module.flow_summary_apple._apple_build_query(
            include_resources=True,
            include_workspaces=False,
            resource_rel_types=[],
        )
        self.assertNotIn("USES_ASSET", query)
        self.assertNotIn("USES_COLOR_ASSET", query)
        self.assertNotIn("USES_XIB", query)
        self.assertNotIn("USES_STORYBOARD", query)
        self.assertIn("null AS src, null AS rel", query)

    def test_get_apple_build_summary_groups_by_scheme(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "apple_build_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "graph_schema_labels":
                return [{"labels": ["Resource", "XcodeWorkspace"]}]
            if kwargs.get("op") == "graph_schema_relationship_types":
                return [{"rels": ["BUNDLED_IN_TARGET", "REFERENCES_PROJECT"]}]
            if kwargs.get("op") == "apple_resource_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "apple_workspace_presence":
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
        self.assertNotIn("ios/App/Assets.xcassets/brand.colorset/Contents.json -> target=App -> scheme=App\nios/App/View.swift", output)

    def test_get_apple_build_summary_does_not_cross_join_resources_and_bundles(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "apple_build_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "graph_schema_labels":
                return [{"labels": ["Resource"]}]
            if kwargs.get("op") == "graph_schema_relationship_types":
                return [{"rels": ["BUNDLED_IN_TARGET"]}]
            if kwargs.get("op") == "apple_resource_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "apple_workspace_presence":
                return [{"n": 0}]
            if kwargs.get("op") == "get_apple_build_summary":
                return [
                    {
                        "src": None,
                        "rel": None,
                        "resource": "AccentColor",
                        "kind": "color",
                        "backing": "FrameCreator/Assets.xcassets/AccentColor.colorset/Contents.json",
                        "target": "FrameCreator",
                        "project_file": "FrameCreator.xcodeproj/project.pbxproj",
                        "scheme": "FrameCreator",
                        "scheme_file": "FrameCreator.xcodeproj/xcshareddata/xcschemes/FrameCreator.xcscheme",
                        "workspace": None,
                    },
                    {
                        "src": None,
                        "rel": None,
                        "resource": "PlaceholderFrame",
                        "kind": "image",
                        "backing": "FrameCreator/Assets.xcassets/PlaceholderFrame.imageset/Contents.json",
                        "target": "FrameCreator",
                        "project_file": "FrameCreator.xcodeproj/project.pbxproj",
                        "scheme": "FrameCreator",
                        "scheme_file": "FrameCreator.xcodeproj/xcshareddata/xcschemes/FrameCreator.xcscheme",
                        "workspace": None,
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
                    group_by="target",
                )
            )

        self.assertIn("AccentColor -> FrameCreator/Assets.xcassets/AccentColor.colorset/Contents.json", output)
        self.assertIn("PlaceholderFrame -> FrameCreator/Assets.xcassets/PlaceholderFrame.imageset/Contents.json", output)
        self.assertNotIn("AppIcon.appiconset", output)

    def test_get_apple_build_summary_falls_back_to_bundled_files(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "apple_build_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "graph_schema_labels":
                return [{"labels": []}]
            if kwargs.get("op") == "graph_schema_relationship_types":
                return [{"rels": []}]
            if kwargs.get("op") == "apple_resource_presence":
                return [{"n": 0}]
            if kwargs.get("op") == "apple_workspace_presence":
                return [{"n": 0}]
            if kwargs.get("op") == "get_apple_build_summary":
                return [
                    {
                        "src": None,
                        "rel": None,
                        "resource": None,
                        "kind": None,
                        "backing": "FrameCreator/Assets.xcassets/AppIcon.appiconset/Contents.json",
                        "target": "FrameCreator",
                        "project_file": "FrameCreator.xcodeproj/project.pbxproj",
                        "scheme": "FrameCreator",
                        "scheme_file": "FrameCreator.xcodeproj/xcshareddata/xcschemes/FrameCreator.xcscheme",
                        "workspace": "FrameCreator.xcodeproj/project.xcworkspace/contents.xcworkspacedata",
                    }
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
                    group_by="target",
                )
            )

        self.assertIn("Target: FrameCreator", output)
        self.assertIn(
            "BUNDLES_FILE:FrameCreator/Assets.xcassets/AppIcon.appiconset/Contents.json",
            output,
        )
        self.assertIn("target=FrameCreator", output)

    def test_get_apple_build_summary_skips_non_apple_repo(self):
        seen_ops = []

        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            seen_ops.append(op)
            if op == "apple_build_presence":
                return [{"n": 0}]
            if op in {"apple_resource_presence", "apple_workspace_presence", "get_apple_build_summary"}:
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

    def test_get_backend_flow_summary_includes_cargo_crate_context(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_backend_flow_summary":
                return [
                    {
                        "api": "crates/api/src/routes.rs",
                        "svc": "crates/core/src/service.rs",
                        "model": "User",
                        "schema": None,
                        "external": None,
                    }
                ]
            if op == "backend_flow_cargo_schema_labels":
                return [{"labels": ["CargoCrate"]}]
            if op == "backend_flow_cargo_crates":
                return [
                    {"crate": "api", "crate_name": "api", "manifest_path": "crates/api/Cargo.toml"},
                    {"crate": "core", "crate_name": "core", "manifest_path": "crates/core/Cargo.toml"},
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_backend_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/rustws",
                    limit=20,
                    as_table=False,
                )
            )

        self.assertIn("Crate: api", output)
        self.assertIn("[api_crate=api, service_crate=core]", output)
        self.assertIn("crates/api/src/routes.rs -> crates/core/src/service.rs -> User", output)

    def test_get_backend_flow_summary_filters_by_cargo_crate(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_backend_flow_summary":
                return [
                    {
                        "api": "crates/api/src/routes.rs",
                        "svc": "crates/core/src/service.rs",
                        "model": "User",
                        "schema": None,
                        "external": None,
                    },
                    {
                        "api": "crates/admin/src/routes.rs",
                        "svc": "crates/core/src/admin_service.rs",
                        "model": "Admin",
                        "schema": None,
                        "external": None,
                    },
                ]
            if op == "backend_flow_cargo_schema_labels":
                return [{"labels": ["CargoCrate"]}]
            if op == "backend_flow_cargo_crates":
                return [
                    {"crate": "api", "crate_name": "api", "manifest_path": "crates/api/Cargo.toml"},
                    {"crate": "admin", "crate_name": "admin", "manifest_path": "crates/admin/Cargo.toml"},
                    {"crate": "core", "crate_name": "core", "manifest_path": "crates/core/Cargo.toml"},
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_backend_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/rustws",
                    crate_contains="admin",
                    limit=20,
                    as_table=False,
                )
            )

        self.assertIn("crates/admin/src/routes.rs", output)
        self.assertNotIn("crates/api/src/routes.rs", output)

    def test_get_backend_flow_summary_falls_back_for_rust_service_repos(self):
        seen_ops = []

        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            seen_ops.append(op)
            if op == "get_backend_flow_summary":
                return []
            if op == "get_backend_flow_summary_fallback":
                return [
                    {
                        "api": "crates/ts-pack-index/src/write_phase.rs",
                        "svc": "crates/ts-pack-index/src/writers.rs",
                        "model": None,
                        "schema": None,
                        "external": "neo4j://local",
                    }
                ]
            if op == "backend_flow_cargo_schema_labels":
                return [{"labels": ["CargoCrate"]}]
            if op == "backend_flow_cargo_crates":
                return [
                    {
                        "crate": "ts-pack-index",
                        "crate_name": "ts-pack-index",
                        "manifest_path": "crates/ts-pack-index/Cargo.toml",
                    }
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_backend_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/tree-sitter-language-pack",
                    limit=20,
                    as_table=False,
                )
            )

        self.assertEqual(
            [
                "get_backend_flow_summary",
                "get_backend_flow_summary_fallback",
                "backend_flow_cargo_schema_labels",
                "backend_flow_cargo_crates",
            ],
            seen_ops,
        )
        self.assertIn("Crate: ts-pack-index", output)
        self.assertIn("crates/ts-pack-index/src/write_phase.rs", output)
        self.assertIn("neo4j://local", output)

    def test_get_backend_flow_summary_explains_empty_cargo_library_workspace(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_backend_flow_summary":
                return []
            if op == "get_backend_flow_summary_fallback":
                return []
            if op == "backend_flow_cargo_schema_labels":
                return [{"labels": ["CargoCrate"]}]
            if op == "backend_flow_cargo_crates":
                return [
                    {
                        "crate": "ts-pack-index",
                        "crate_name": "ts-pack-index",
                        "manifest_path": "crates/ts-pack-index/Cargo.toml",
                    },
                    {
                        "crate": "ts-pack-core",
                        "crate_name": "tree-sitter-language-pack",
                        "manifest_path": "crates/ts-pack-core/Cargo.toml",
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_backend_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/tree-sitter-language-pack",
                    limit=20,
                    as_table=False,
                )
            )

        self.assertIn("crate/library-oriented", output)
        self.assertIn("ts-pack-index", output)
        self.assertIn("project overview", output)


if __name__ == "__main__":
    unittest.main()

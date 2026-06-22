import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "graph" / "flow_summary.py"
APPLE_MODULE_PATH = REPO_ROOT / "tools" / "brain" / "graph" / "flow_summary_apple.py"


def load_flow_summary_module():
    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/repo"

    graph_pkg = types.ModuleType("tools")
    brain_pkg = types.ModuleType("tools.brain")
    graph_subpkg = types.ModuleType("tools.brain.graph")
    graph_contract_mod = types.ModuleType("tools.brain.graph_contract")
    graph_contract_mod.node_label = lambda name: {
        "file": "File",
        "model": "Model",
        "external_api": "ExternalApi",
        "resource": "Resource",
        "xcode_target": "XcodeTarget",
        "xcode_workspace": "XcodeWorkspace",
        "xcode_scheme": "XcodeScheme",
        "api_route": "ApiRoute",
        "cargo_crate": "CargoCrate",
    }.get(name, name)
    graph_contract_mod.rel_type = lambda name: {
        "asset_links": "ASSET_LINKS",
        "calls_api_route": "CALLS_API_ROUTE",
        "handled_by": "HANDLED_BY",
        "calls_service": "CALLS_SERVICE",
        "calls_db_model": "CALLS_DB_MODEL",
        "calls_db": "CALLS_DB",
        "calls_api_external": "CALLS_API_EXTERNAL",
        "calls_api": "CALLS_API",
        "imports": "IMPORTS",
        "defined_in_file": "DEFINED_IN_FILE",
        "file_graph_link": "FILE_GRAPH_LINK",
        "backed_by_file": "BACKED_BY_FILE",
        "bundled_in_target": "BUNDLED_IN_TARGET",
        "bundles_file": "BUNDLES_FILE",
        "references_project": "REFERENCES_PROJECT",
        "builds_target": "BUILDS_TARGET",
    }.get(name, name.upper())
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
            "tools.brain.graph_contract": graph_contract_mod,
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
        self.assertIn("Use this to decide which UI entrypoints reach real APIs or services", output)
        self.assertIn("Inspect First:", output)
        self.assertNotIn(
            "src/public/properties.html -> src/public/assets/properties.js -> src/api/leaseRoutes.ts -> src/services/leaseService.ts",
            output,
        )

    def test_get_app_flow_summary_as_table_diagnoses_empty_graph(self):
        async def fake_execute_read(session, query, **kwargs):
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_app_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/rentallaw",
                    include_coverage=False,
                    limit=20,
                    as_table=True,
                )
            )

        self.assertTrue(output.startswith("No UI → API → Service → DB paths found."))
        self.assertIn("Diagnosis:", output)
        self.assertIn("ui_files=0 js_files=0", output)
        self.assertIn("Missing evidence: HTML/Astro UI entry files", output)
        self.assertIn("get_flow_summary('/tmp/rentallaw', mode='auto')", output)

    def test_get_app_flow_summary_diagnoses_missing_edges_with_source_files_present(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_app_flow_summary_coverage_files":
                return [{"ui_files": 3, "js_files": 8}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_app_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/fullstack",
                    include_coverage=True,
                    limit=20,
                )
            )

        self.assertIn("ui_files=3 js_files=8", output)
        self.assertNotIn("HTML/Astro UI entry files", output)
        self.assertNotIn("JavaScript/TypeScript client files", output)
        self.assertIn("ASSET_LINKS UI-to-client edges", output)
        self.assertIn("CALLS_API or CALLS_API_ROUTE edges", output)

    def test_coverage_lines_include_file_graph_links(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_app_flow_summary_coverage_files":
                return [{"ui_files": 2, "js_files": 3}]
            if op == "get_app_flow_summary_coverage_assets":
                return [{"asset_links": 4}]
            if op == "get_app_flow_summary_coverage_api":
                return [{"api_links": 5}]
            if op == "get_app_flow_summary_coverage_service":
                return [{"service_links": 6}]
            if op == "get_app_flow_summary_coverage_db":
                return [{"db_links": 7}]
            if op == "get_app_flow_summary_coverage_api_routes":
                return [{"api_route_links": 8}]
            if op == "get_app_flow_summary_coverage_file_graph":
                return [{"file_graph_links": 9}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            lines = asyncio.run(self.module._coverage_lines(FakeSession(), "proj123"))

        self.assertEqual(
            lines,
            [
                "Coverage: ui_files=2 js_files=3 asset_links=4 api_links=5 api_route_links=8 service_links=6 db_links=7 file_graph_links=9"
            ],
        )

    def test_suppress_coarse_route_service_rows_drops_file_level_service_for_multi_route_api(self):
        rows = [
            (
                "src/public/financials.html",
                "src/public/assets/financials.js",
                "POST /api/financials/accounting-sync/quickbooks/export-batch",
                "src/api/routes/financeAdminRoutes.ts",
                "src/services/AccountingSyncBatchService.ts",
                "JournalEntry",
                "prisma/schema.prisma",
                None,
            )
        ]
        adjusted = self.module._suppress_coarse_route_service_rows(
            rows,
            {"src/api/routes/financeAdminRoutes.ts": 27},
        )
        self.assertEqual(
            adjusted[0],
            (
                "src/public/financials.html",
                "src/public/assets/financials.js",
                "POST /api/financials/accounting-sync/quickbooks/export-batch",
                "src/api/routes/financeAdminRoutes.ts",
                None,
                None,
                None,
                None,
            ),
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
                        "ui_roles": None,
                        "js_roles": None,
                        "route": "GET /api/applications",
                        "api": "src/api/routes/applicationOpsRoutes.ts",
                        "api_roles": [],
                        "svc": None,
                        "svc_roles": None,
                        "model": None,
                        "schema": None,
                        "external": None,
                    },
                    {
                        "ui": "src/public/financial-summary.html",
                        "js": "src/public/assets/financial-summary.js",
                        "ui_roles": [],
                        "js_roles": [],
                        "route": "GET /api/financials/tax-package",
                        "api": "src/api/routes/financeAdminRoutes.ts",
                        "api_roles": [],
                        "svc": None,
                        "svc_roles": None,
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
        self.assertNotIn("tests/routes.test.ts", output)

    def test_get_app_flow_summary_keeps_test_like_rows_when_file_roles_are_present(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_app_flow_summary":
                return [
                    {
                        "ui": "tests/routes.test.ts",
                        "js": "tests/routes.test.ts",
                        "ui_roles": [],
                        "js_roles": [],
                        "route": "GET /api/applications",
                        "api": "src/api/routes/applicationOpsRoutes.ts",
                        "api_roles": [],
                        "svc": None,
                        "svc_roles": None,
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

        self.assertIn("tests/routes.test.ts", output)

    def test_get_app_flow_summary_filters_e2e_and_spec_rows_even_if_query_leaks_them(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_app_flow_summary":
                return [
                    {
                        "ui": "packages/console/app/src/routes/enterprise/index.tsx",
                        "js": "packages/app/e2e/actions.ts",
                        "ui_roles": [],
                        "js_roles": None,
                        "route": None,
                        "api": None,
                        "api_roles": None,
                        "svc": None,
                        "svc_roles": None,
                        "model": None,
                        "schema": None,
                        "external": None,
                    },
                    {
                        "ui": "packages/console/app/src/routes/enterprise/index.tsx",
                        "js": "packages/app/e2e/app/home.spec.ts",
                        "ui_roles": [],
                        "js_roles": None,
                        "route": None,
                        "api": None,
                        "api_roles": None,
                        "svc": None,
                        "svc_roles": None,
                        "model": None,
                        "schema": None,
                        "external": None,
                    },
                    {
                        "ui": "packages/opencode/src/index.ts",
                        "js": "packages/opencode/src/client.ts",
                        "ui_roles": [],
                        "js_roles": [],
                        "route": "GET /config",
                        "api": "packages/opencode/src/server/config.ts",
                        "api_roles": [],
                        "svc": None,
                        "svc_roles": None,
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
                    workspace_id="/tmp/opencode",
                    include_coverage=False,
                    limit=20,
                    group_by_ui=True,
                )
            )

        self.assertIn("packages/opencode/src/index.ts", output)
        self.assertIn("GET /config", output)
        self.assertNotIn("packages/app/e2e/actions.ts", output)
        self.assertNotIn("home.spec.ts", output)

    def test_is_low_signal_flow_path_uses_roles_before_path_fallback(self):
        self.assertFalse(self.module._is_low_signal_flow_path("tests/routes.test.ts", []))
        self.assertTrue(self.module._is_low_signal_flow_path("tests/routes.test.ts", None))
        self.assertTrue(self.module._is_low_signal_flow_path("src/app.ts", ["test_surface"]))
        self.assertTrue(self.module._is_low_signal_flow_path("benchmarks/routes_benchmark.ts", None))
        self.assertTrue(self.module._is_low_signal_flow_path("docs/architecture.md", None))
        self.assertTrue(self.module._is_low_signal_flow_path("src/app.ts", ["docs_surface"]))
        self.assertFalse(
            self.module._is_low_signal_flow_path(
                "tools/docs/app.py",
                ["implementation_surface", "support_surface"],
            )
        )
        self.assertTrue(
            self.module._is_low_signal_flow_path(
                "src/app.py",
                ["implementation_surface", "test_surface"],
            )
        )

    def test_build_app_flow_literal_fallback_keeps_test_like_asset_pair_when_roles_are_present(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_app_flow_summary_asset_pairs":
                return [
                    {
                        "ui": "tests/routes.test.ts",
                        "ui_roles": [],
                        "js": "tests/routes.test.ts",
                        "js_roles": [],
                    }
                ]
            if op == "get_app_flow_summary_route_catalog":
                return [{"path": "/api/applications", "method": "GET", "api": "src/api/routes/applicationOpsRoutes.ts"}]
            return []

        with (
            mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read),
            mock.patch.object(self.module, "get_workspace_path", return_value="/tmp/repo"),
            mock.patch("builtins.open", mock.mock_open(read_data="fetch('/api/applications')")),
        ):
            rows = asyncio.run(
                self.module._build_app_flow_literal_fallback(
                    FakeSession(),
                    "proj123",
                    "/tmp/repo",
                    [],
                )
            )

        self.assertEqual(
            rows,
            [("tests/routes.test.ts", "tests/routes.test.ts", "GET /api/applications", "src/api/routes/applicationOpsRoutes.ts", None, None, None, None)],
        )

    def test_build_app_flow_literal_fallback_filters_test_like_asset_pair_when_roles_missing(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_app_flow_summary_asset_pairs":
                return [
                    {
                        "ui": "tests/routes.test.ts",
                        "ui_roles": None,
                        "js": "tests/routes.test.ts",
                        "js_roles": None,
                    }
                ]
            if op == "get_app_flow_summary_route_catalog":
                return [{"path": "/api/applications", "method": "GET", "api": "src/api/routes/applicationOpsRoutes.ts"}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            rows = asyncio.run(
                self.module._build_app_flow_literal_fallback(
                    FakeSession(),
                    "proj123",
                    "/tmp/repo",
                    [],
                )
            )

        self.assertEqual(rows, [])

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

    def test_apple_build_query_uses_coalesced_workspace_project_path(self):
        query = self.module.flow_summary_apple._apple_build_query(
            include_resources=False,
            include_workspaces=True,
            resource_rel_types=[],
        )
        self.assertIn(
            "coalesce(project_file.filepath, project_file.file_path) = target.project_file",
            query,
        )
        self.assertIn(
            "coalesce(workspace.filepath, workspace.file_path) AS workspace",
            query,
        )

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

    def test_get_apple_build_summary_prefers_scheme_matching_target_when_grouped_by_target(self):
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
                        "backing": "Shared/Resources/Assets.xcassets/Colors/AccentColor.colorset/Contents.json",
                        "target": "Fruta iOS All",
                        "project_file": "Fruta.xcodeproj/project.pbxproj",
                        "scheme": "Fruta iOS Widgets",
                        "scheme_file": "Fruta.xcodeproj/xcshareddata/xcschemes/Fruta iOS Widgets.xcscheme",
                        "workspace": None,
                    },
                    {
                        "src": None,
                        "rel": None,
                        "resource": "AccentColor",
                        "kind": "color",
                        "backing": "Shared/Resources/Assets.xcassets/Colors/AccentColor.colorset/Contents.json",
                        "target": "Fruta iOS All",
                        "project_file": "Fruta.xcodeproj/project.pbxproj",
                        "scheme": "Fruta iOS All",
                        "scheme_file": "Fruta.xcodeproj/xcshareddata/xcschemes/Fruta iOS All.xcscheme",
                        "workspace": None,
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_apple_build_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/fruta",
                    limit=20,
                    as_table=False,
                    group_by="target",
                )
            )

        self.assertIn("scheme=Fruta iOS All", output)
        self.assertNotIn("scheme=Fruta iOS Widgets", output)

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

    def test_get_apple_build_summary_reports_partial_graph_coverage(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_build_presence":
                return [{"n": 1}]
            if op == "graph_schema_labels":
                return [{"labels": ["XcodeTarget"]}]
            if op == "graph_schema_relationship_types":
                return [{"rels": []}]
            if op == "apple_resource_presence":
                return [{"n": 0}]
            if op == "apple_workspace_presence":
                return [{"n": 0}]
            if op == "get_apple_build_summary":
                return []
            if op == "apple_graph_coverage":
                return [
                    {
                        "project_files": 1,
                        "workspace_files": 1,
                        "scheme_files": 1,
                        "resource_files": 4,
                        "targets": 2,
                        "schemes": 0,
                        "workspaces": 0,
                        "resources": 0,
                        "bundles_file_edges": 0,
                        "builds_target_edges": 0,
                        "references_project_edges": 0,
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

        self.assertIn("Apple build files detected, but graph coverage is partial.", output)
        self.assertIn("Graph: targets=2 schemes=0 workspaces=0 resources=0", output)
        self.assertIn("scheme files exist, but no XcodeScheme nodes were materialized", output)
        self.assertIn("workspace files exist, but no XcodeWorkspace nodes were materialized", output)

    def test_get_apple_build_summary_surfaces_multi_project_workspace_overview(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "apple_build_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "graph_schema_labels":
                return [{"labels": ["Resource", "XcodeWorkspace"]}]
            if kwargs.get("op") == "graph_schema_relationship_types":
                return [{"rels": ["BUNDLED_IN_TARGET", "REFERENCES_PROJECT", "BUILDS_TARGET"]}]
            if kwargs.get("op") == "apple_resource_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "apple_workspace_presence":
                return [{"n": 1}]
            if kwargs.get("op") == "apple_graph_coverage":
                return [{
                    "project_files": 2,
                    "workspace_files": 1,
                    "scheme_files": 2,
                    "resource_files": 2,
                    "targets": 2,
                    "schemes": 2,
                    "workspaces": 1,
                    "resources": 2,
                    "bundles_file_edges": 2,
                    "builds_target_edges": 2,
                    "references_project_edges": 2,
                }]
            if kwargs.get("op") == "apple_workspace_projects":
                return [
                    {
                        "workspace": "BGM.xcworkspace/contents.xcworkspacedata",
                        "project_file": "BGMApp/BGMApp.xcodeproj/project.pbxproj",
                    },
                    {
                        "workspace": "BGM.xcworkspace/contents.xcworkspacedata",
                        "project_file": "BGMDriver/BGMDriver.xcodeproj/project.pbxproj",
                    },
                ]
            if kwargs.get("op") == "get_apple_build_summary":
                return [
                    {
                        "src": None,
                        "rel": None,
                        "resource": "AirPlayIcon",
                        "kind": "image",
                        "backing": "BGMApp/BGMApp/Images.xcassets/AirPlayIcon.imageset/Contents.json",
                        "target": "Background Music",
                        "project_file": "BGMApp/BGMApp.xcodeproj/project.pbxproj",
                        "scheme": "Background Music",
                        "scheme_file": "BGMApp/BGMApp.xcodeproj/xcshareddata/xcschemes/Background Music.xcscheme",
                        "workspace": "BGM.xcworkspace/contents.xcworkspacedata",
                    },
                    {
                        "src": None,
                        "rel": None,
                        "resource": "DriverIcon",
                        "kind": "image",
                        "backing": "BGMDriver/Assets.xcassets/DriverIcon.imageset/Contents.json",
                        "target": "Background Music Device",
                        "project_file": "BGMDriver/BGMDriver.xcodeproj/project.pbxproj",
                        "scheme": "Background Music Device",
                        "scheme_file": "BGMDriver/BGMDriver.xcodeproj/xcshareddata/xcschemes/Background Music Device.xcscheme",
                        "workspace": "BGM.xcworkspace/contents.xcworkspacedata",
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_apple_build_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/loom",
                    limit=20,
                    as_table=False,
                    group_by="target",
                )
            )

        self.assertIn("Workspace Overview:", output)
        self.assertIn("- BGM.xcworkspace/contents.xcworkspacedata", output)
        self.assertIn("-> BGMApp/BGMApp.xcodeproj/project.pbxproj", output)
        self.assertIn("-> BGMDriver/BGMDriver.xcodeproj/project.pbxproj", output)
        self.assertIn("Target: Background Music", output)
        self.assertIn("Target: Background Music Device", output)

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
        self.assertIn("Use this to decide which API entrypoints reach real services", output)
        self.assertIn("Inspect First:", output)
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
                "get_backend_flow_summary_routes",
                "backend_flow_cargo_schema_labels",
                "backend_flow_cargo_crates",
            ],
            seen_ops,
        )
        self.assertIn("Crate: ts-pack-index", output)
        self.assertIn("Inspect First:", output)
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

    def test_get_backend_flow_summary_expands_multi_route_api_without_fake_service_binding(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_backend_flow_summary":
                return [
                    {
                        "api": "src/api/routes/financeAdminRoutes.ts",
                        "svc": "src/services/AccountingSyncBatchService.ts",
                        "model": None,
                        "schema": None,
                        "external": None,
                    }
                ]
            if op == "get_backend_flow_summary_routes":
                return [
                    {
                        "api": "src/api/routes/financeAdminRoutes.ts",
                        "routes": [
                            "GET /api/charges",
                            "POST /api/financials/accounting-sync/quickbooks/export-batch",
                        ],
                    }
                ]
            if op == "backend_flow_cargo_schema_labels":
                return [{"labels": []}]
            if op == "backend_flow_cargo_crates":
                return []
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_backend_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/rental",
                    api_contains="financeAdminRoutes",
                    limit=20,
                    as_table=True,
                )
            )

        self.assertIn("GET /api/charges", output)
        self.assertIn("POST /api/financials/accounting-sync/quickbooks/export-batch", output)
        self.assertNotIn("src/services/AccountingSyncBatchService.ts", output)

    def test_get_backend_flow_summary_falls_back_for_fastapi_import_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api_dir = os.path.join(tmpdir, "app", "api")
            os.makedirs(api_dir, exist_ok=True)
            api_file = os.path.join(api_dir, "endpoints.py")
            with open(api_file, "w", encoding="utf-8") as fh:
                fh.write(
                    "from fastapi import APIRouter, Depends\n"
                    "from sqlalchemy.orm import Session\n"
                    "from app.db.session import get_db\n"
                    "from app.retrieval.hybrid_search import HybridRetriever\n"
                    "from app.ingestion.processor import IngestionProcessor\n"
                    "from app.models.legal_source import LegalSource\n"
                    "router = APIRouter()\n\n"
                    "@router.post('/search')\n"
                    "async def search_legal_content(db: Session = Depends(get_db)):\n"
                    "    retriever = HybridRetriever(db)\n"
                    "    return await retriever.search(query='x')\n\n"
                    "@router.get('/sources')\n"
                    "def list_sources(db: Session = Depends(get_db)):\n"
                    "    return db.query(LegalSource).all()\n\n"
                    "@router.post('/ingest')\n"
                    "async def trigger_ingestion(db: Session = Depends(get_db)):\n"
                    "    processor = IngestionProcessor(db)\n"
                    "    return await processor.process_document(source_id='1')\n"
                )

            async def fake_execute_read(session, query, **kwargs):
                op = kwargs.get("op")
                if op == "get_backend_flow_summary":
                    return []
                if op == "get_backend_flow_summary_fallback":
                    return []
                if op == "get_backend_flow_summary_import_fallback":
                    return [
                        {"api": "app/api/endpoints.py", "dep": "app/db/session.py"},
                        {"api": "app/api/endpoints.py", "dep": "app/retrieval/hybrid_search.py"},
                        {"api": "app/api/endpoints.py", "dep": "app/ingestion/processor.py"},
                        {"api": "app/api/endpoints.py", "dep": "app/models/legal_source.py"},
                    ]
                if op == "get_backend_flow_summary_routes":
                    return []
                if op == "backend_flow_cargo_schema_labels":
                    return [{"labels": []}]
                if op == "backend_flow_cargo_crates":
                    return []
                return []

            with (
                mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read),
                mock.patch.object(self.module, "get_workspace_path", return_value=tmpdir),
            ):
                output = asyncio.run(
                    self.module.get_backend_flow_summary_impl(
                        driver=FakeDriver(),
                        neo4j_db="neo4j",
                        workspace_id=tmpdir,
                        api_contains="endpoints.py",
                        limit=20,
                        as_table=False,
                    )
                )

        self.assertIn("POST /search", output)
        self.assertIn("Use this to decide which API entrypoints reach real services", output)
        self.assertIn("app/retrieval/hybrid_search.py", output)
        self.assertIn("GET /sources", output)
        self.assertIn("app/models/legal_source.py", output)
        self.assertIn("app/db/session.py", output)

    def test_get_backend_flow_summary_falls_back_for_fastapi_app_module_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proxy_dir = os.path.join(tmpdir, "proxy")
            os.makedirs(proxy_dir, exist_ok=True)
            api_file = os.path.join(proxy_dir, "app.py")
            with open(api_file, "w", encoding="utf-8") as fh:
                fh.write(
                    "from fastapi import FastAPI, Request\n"
                    "from proxy.handlers import route_request\n"
                    "from proxy.models import resolve_model_name\n"
                    "app = FastAPI()\n\n"
                    "@app.get('/health')\n"
                    "async def health():\n"
                    "    return {'ok': True}\n\n"
                    "@app.post('/v1/chat/completions')\n"
                    "async def chat(request: Request):\n"
                    "    model = resolve_model_name('x', [])\n"
                    "    return await route_request(request, model)\n"
                )

            async def fake_execute_read(session, query, **kwargs):
                op = kwargs.get("op")
                if op == "get_backend_flow_summary":
                    return []
                if op == "get_backend_flow_summary_fallback":
                    return []
                if op == "get_backend_flow_summary_import_fallback":
                    return [
                        {"api": "proxy/app.py", "dep": "proxy/handlers.py"},
                        {"api": "proxy/app.py", "dep": "proxy/models.py"},
                    ]
                if op == "get_backend_flow_summary_routes":
                    return []
                if op == "backend_flow_cargo_schema_labels":
                    return [{"labels": []}]
                if op == "backend_flow_cargo_crates":
                    return []
                return []

            with (
                mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read),
                mock.patch.object(self.module, "get_workspace_path", return_value=tmpdir),
            ):
                output = asyncio.run(
                    self.module.get_backend_flow_summary_impl(
                        driver=FakeDriver(),
                        neo4j_db="neo4j",
                        workspace_id=tmpdir,
                        api_contains="proxy/app.py",
                        limit=20,
                        as_table=False,
                    )
                )

        self.assertIn("POST /v1/chat/completions", output)
        self.assertIn("proxy/handlers.py", output)
        self.assertIn("proxy/models.py", output)

    def test_get_backend_flow_summary_keeps_test_like_fastapi_fallback_when_roles_are_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tests_dir = os.path.join(tmpdir, "tests")
            os.makedirs(tests_dir, exist_ok=True)
            api_file = os.path.join(tests_dir, "app.py")
            with open(api_file, "w", encoding="utf-8") as fh:
                fh.write(
                    "from fastapi import FastAPI\n"
                    "app = FastAPI()\n\n"
                    "@app.get('/health')\n"
                    "async def health():\n"
                    "    return {'ok': True}\n"
                )

            async def fake_execute_read(session, query, **kwargs):
                op = kwargs.get("op")
                if op == "get_backend_flow_summary":
                    return []
                if op == "get_backend_flow_summary_fallback":
                    return []
                if op == "get_backend_flow_summary_import_fallback":
                    return [{"api": "tests/app.py", "api_roles": [], "dep": "app/db/session.py", "dep_roles": []}]
                if op == "get_backend_flow_summary_routes":
                    return []
                if op == "backend_flow_cargo_schema_labels":
                    return [{"labels": []}]
                if op == "backend_flow_cargo_crates":
                    return []
                return []

            with (
                mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read),
                mock.patch.object(self.module, "get_workspace_path", return_value=tmpdir),
            ):
                output = asyncio.run(
                    self.module.get_backend_flow_summary_impl(
                        driver=FakeDriver(),
                        neo4j_db="neo4j",
                        workspace_id=tmpdir,
                        api_contains="tests/app.py",
                        limit=20,
                        as_table=False,
                    )
                )

        self.assertIn("tests/app.py", output)
        self.assertIn("GET /health", output)

    def test_get_backend_flow_summary_filters_test_like_fastapi_fallback_when_roles_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tests_dir = os.path.join(tmpdir, "tests")
            os.makedirs(tests_dir, exist_ok=True)
            api_file = os.path.join(tests_dir, "app.py")
            with open(api_file, "w", encoding="utf-8") as fh:
                fh.write(
                    "from fastapi import FastAPI\n"
                    "app = FastAPI()\n\n"
                    "@app.get('/health')\n"
                    "async def health():\n"
                    "    return {'ok': True}\n"
                )

            async def fake_execute_read(session, query, **kwargs):
                op = kwargs.get("op")
                if op == "get_backend_flow_summary":
                    return []
                if op == "get_backend_flow_summary_fallback":
                    return []
                if op == "get_backend_flow_summary_import_fallback":
                    return [{"api": "tests/app.py", "api_roles": None, "dep": "app/db/session.py", "dep_roles": None}]
                if op == "get_backend_flow_summary_routes":
                    return []
                if op == "backend_flow_cargo_schema_labels":
                    return [{"labels": []}]
                if op == "backend_flow_cargo_crates":
                    return []
                return []

            with (
                mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read),
                mock.patch.object(self.module, "get_workspace_path", return_value=tmpdir),
            ):
                output = asyncio.run(
                    self.module.get_backend_flow_summary_impl(
                        driver=FakeDriver(),
                        neo4j_db="neo4j",
                        workspace_id=tmpdir,
                        api_contains="tests/app.py",
                        limit=20,
                        as_table=False,
                    )
                )

        self.assertEqual("No API → Service → DB paths found.", output)

    def test_extract_python_import_map_supports_parenthesized_imports(self):
        source_text = (
            "from proxy.models import (\n"
            "    resolve_model_name,\n"
            "    build_local_llm_models,\n"
            ")\n"
            "from proxy.handlers import (\n"
            "    forward_responses_api_completion,\n"
            ")\n"
        )

        result = self.module._extract_python_import_map(source_text)

        self.assertEqual("proxy.models", result["resolve_model_name"])
        self.assertEqual("proxy.models", result["build_local_llm_models"])
        self.assertEqual(
            "proxy.handlers",
            result["forward_responses_api_completion"],
        )


if __name__ == "__main__":
    unittest.main()

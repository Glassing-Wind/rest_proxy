import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/tools.py"
OVERVIEW_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/overview.py"
UTILITY_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/utility.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeCursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        return None

    async def fetchone(self):
        return (42,)


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


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_tools_module():
    overview_spec = importlib.util.spec_from_file_location("tools.brain.graph.overview", OVERVIEW_PATH)
    overview_module = importlib.util.module_from_spec(overview_spec)
    assert overview_spec.loader is not None

    utility_spec = importlib.util.spec_from_file_location("tools.brain.graph.utility", UTILITY_PATH)
    utility_module = importlib.util.module_from_spec(utility_spec)
    assert utility_spec.loader is not None

    spec = importlib.util.spec_from_file_location("tools.brain.graph.tools", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (FakeMemoryStore, None, None, None, None)
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/framecreator"

    graph_core_mod = types.ModuleType("tools.brain.graph.core")
    graph_core_mod._SYMBOL_FILTER_CYPHER = "true"

    async def _execute_read(*args, **kwargs):
        return []

    graph_core_mod._execute_read = _execute_read
    graph_core_mod._build_import_graph_impl = mock.AsyncMock(return_value="ok")
    graph_core_mod._build_symbol_import_export_graph_impl = mock.AsyncMock(return_value="ok")
    graph_core_mod._build_asset_graph_impl = mock.AsyncMock(return_value="ok")
    graph_core_mod._get_cli_flow_summary = mock.AsyncMock(return_value="No CLI flows found.")
    graph_core_mod._summarize_batches = lambda *args, **kwargs: (0, 0, 0)
    graph_core_mod.get_last_graph_build_metric = lambda: None

    flow_summary_mod = types.ModuleType("tools.brain.graph.flow_summary")
    flow_summary_mod.get_app_flow_summary_impl = mock.AsyncMock(return_value="No UI → API → Service → DB paths found.")
    flow_summary_mod.get_backend_flow_summary_impl = mock.AsyncMock(return_value="No API → Service → DB paths found.")
    flow_summary_mod.get_apple_build_summary_impl = mock.AsyncMock(return_value="No Apple build graph paths found.")

    runtime_mod = types.ModuleType("tools.brain.graph.runtime")
    runtime_mod.record_metric = lambda *args, **kwargs: None
    runtime_mod.get_recent_metrics = lambda *args, **kwargs: []

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

    async def _require_driver():
        return FakeDriver()

    graph_bootstrap_mod.require_driver = _require_driver
    graph_bootstrap_mod._NEO4J_DB = "neo4j"

    graph_pkg = types.ModuleType("tools")
    graph_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    graph_subpkg = types.ModuleType("tools.brain.graph")
    graph_subpkg.__path__ = []
    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "graph_bootstrap": graph_bootstrap_mod,
            "mcp.server.fastmcp": mcp_mod,
            "tools": graph_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.graph": graph_subpkg,
            "tools.brain.graph.core": graph_core_mod,
            "tools.brain.graph.flow_summary": flow_summary_mod,
            "tools.brain.graph.utility": utility_module,
            "tools.brain.graph.runtime": runtime_mod,
        },
    ):
        overview_spec.loader.exec_module(overview_module)
        sys.modules["tools.brain.graph.overview"] = overview_module
        utility_spec.loader.exec_module(utility_module)
        sys.modules["tools.brain.graph.utility"] = utility_module
        spec.loader.exec_module(module)
    return module


class GraphToolsTests(unittest.TestCase):
    def setUp(self):
        self.module = load_tools_module()
        self.mcp = FakeMCP()
        self.module.register(self.mcp)
        self.graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        self.graph_bootstrap_mod.require_driver = _require_driver
        self.graph_bootstrap_mod._NEO4J_DB = "neo4j"

    def test_directory_snapshot_includes_apple_build_context(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 1}]
            if op == "get_directory_snapshot_files":
                return [{"fp": "ios/App/View.swift", "sym_count": 3, "samples": ["body", "preview"]}]
            if op == "get_directory_snapshot_inbound":
                return []
            if op == "get_directory_snapshot_outbound":
                return []
            if op == "get_directory_snapshot_assets":
                return []
            if op == "apple_context_targets":
                return [{"target": "App", "project_file": "ios/App.xcodeproj/project.pbxproj", "bundled_files": 4}]
            if op == "apple_context_schemes":
                return [{"scheme": "App", "targets": ["App"]}]
            if op == "apple_context_schema_labels":
                return [{"labels": []}]
            if op == "apple_context_schema_relationship_types":
                return [{"rels": []}]
            if op == "apple_context_workspaces":
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_directory_snapshot"]("/tmp/framecreator", "ios", 5))

        self.assertIn("Apple Build Context", output)
        self.assertIn("target `App` bundles 4 file(s)", output)
        self.assertIn("scheme `App` builds App", output)

    def test_project_overview_includes_apple_build_context(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 12}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 20}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "ios", "files": 8, "syms": 14}]
            if op == "get_project_overview_key_files":
                return [{"fp": "ios/App/View.swift", "n": 4, "ex": ["body", "preview"]}]
            if op == "apple_context_presence":
                return [{"n": 1}]
            if op == "apple_context_targets":
                return [{"target": "App", "project_file": "ios/App.xcodeproj/project.pbxproj", "bundled_files": 4}]
            if op == "apple_context_schemes":
                return [{"scheme": "App", "targets": ["App"]}]
            if op == "apple_context_schema_labels":
                return [{"labels": ["XcodeWorkspace"]}]
            if op == "apple_context_schema_relationship_types":
                return [{"rels": ["REFERENCES_PROJECT"]}]
            if op == "apple_context_workspaces":
                return [{"workspace": "ios/App.xcworkspace/contents.xcworkspacedata", "projects": ["ios/App.xcodeproj/project.pbxproj"]}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/framecreator"))

        self.assertIn("## Apple Build Context", output)
        self.assertIn("target `App` bundles 4 file(s)", output)
        self.assertIn("scheme `App` builds App", output)
        self.assertIn("workspace `ios/App.xcworkspace/contents.xcworkspacedata` references ios/App.xcodeproj/project.pbxproj", output)

    def test_project_overview_skips_apple_queries_for_non_apple_repo(self):
        seen_ops = []

        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            seen_ops.append(op)
            if op == "get_project_overview_file_count":
                return [{"files": 12}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 20}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "src", "files": 8, "syms": 14}]
            if op == "get_project_overview_key_files":
                return [{"fp": "src/app.js", "n": 4, "ex": ["start", "stop"]}]
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op in {"apple_context_targets", "apple_context_schemes", "apple_context_schema_labels", "apple_context_schema_relationship_types", "apple_context_workspaces"}:
                raise AssertionError(f"unexpected Apple query: {op}")
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/framecreator"))

        self.assertNotIn("## Apple Build Context", output)
        self.assertIn("apple_context_presence", seen_ops)

    def test_get_flow_summary_apple_mode_dispatches_to_apple_summary(self):
        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(
                self.module.graph_flow_summary,
                "get_apple_build_summary_impl",
                new=mock.AsyncMock(return_value="No Apple build graph paths found."),
            ) as apple_mock:
                with mock.patch.object(
                    self.module.graph_flow_summary,
                    "get_backend_flow_summary_impl",
                    new=mock.AsyncMock(return_value="backend result"),
                ) as backend_mock:
                    output = asyncio.run(
                        self.mcp.tools["get_flow_summary"](
                            "/tmp/framecreator",
                            mode="apple",
                            limit=5,
                        )
                    )

        self.assertEqual("No Apple build graph paths found.", output)
        apple_mock.assert_awaited_once()
        backend_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

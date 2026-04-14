import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "summaries.py"


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_module():
    spec = importlib.util.spec_from_file_location("search_summaries_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda project_path: "proj123"
    graph_core_mod = types.ModuleType("tools.brain.graph.core")

    async def _execute_read(*args, **kwargs):
        return []

    graph_core_mod._execute_read = _execute_read

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    graph_pkg = types.ModuleType("tools.brain.graph")
    graph_pkg.__path__ = []

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.graph": graph_pkg,
            "tools.brain.graph.core": graph_core_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


class SearchSummaryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_symbol_exports_summary_applies_filters(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "get_symbol_exports_summary_filtered_rows":
                return [
                    {
                        "file": "src/app.py",
                        "symbol": "AppFoo",
                        "target_symbol": "AppFoo",
                        "alias_edges": 0,
                        "importers": 3,
                    },
                    {
                        "file": "src/app.py",
                        "symbol": "Other",
                        "target_symbol": "Other",
                        "alias_edges": 0,
                        "importers": 2,
                    },
                    {
                        "file": "tests/test_app.py",
                        "symbol": "AppFoo",
                        "target_symbol": "AppFoo",
                        "alias_edges": 0,
                        "importers": 1,
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_paths=["src/*"],
                    exclude_paths=["tests/*"],
                    symbol_prefix="App",
                )
            )
        self.assertIn("AppFoo", output)
        self.assertIn("src/app.py", output)
        self.assertNotIn("tests/test_app.py", output)

    def test_symbol_imports_overview_limits_implicit_sources_to_swift_files(self):
        captured_queries = []

        async def fake_execute_read(session, query, **kwargs):
            captured_queries.append((kwargs.get("op"), query))
            op = kwargs.get("op")
            if op == "get_symbol_imports_overview_exp_count":
                return [{"n": 1}]
            if op == "get_symbol_imports_overview_imp_count":
                return [{"n": 2}]
            if op == "get_symbol_imports_overview_exp_symbols":
                return [{"symbol": "Foo", "n": 1}]
            if op == "get_symbol_imports_overview_exp_files":
                return [{"file": "src/a.py", "n": 1, "symbols": ["Foo"]}]
            if op == "get_symbol_imports_overview_imp_symbols":
                return [{"symbol": "Bar", "n": 2}]
            if op == "get_symbol_imports_overview_imp_files":
                return [{"file": "Sources/App/View.swift", "n": 2, "symbols": ["Bar"]}]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_imports_overview_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_implicit=True,
                )
            )

        self.assertIn("Sources/App/View.swift", output)
        implicit_queries = {
            op: query
            for op, query in captured_queries
            if op in {
                "get_symbol_imports_overview_imp_symbols",
                "get_symbol_imports_overview_imp_files",
            }
        }
        self.assertIn("WHERE f.filepath ENDS WITH '.swift'", implicit_queries["get_symbol_imports_overview_imp_symbols"])
        self.assertIn("WHERE f.filepath ENDS WITH '.swift'", implicit_queries["get_symbol_imports_overview_imp_files"])

    def test_symbol_exports_summary_falls_back_to_visibility_and_python_naming(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 0}]
            if op == "get_symbol_exports_summary_symbols":
                return []
            if op == "get_symbol_exports_summary_files":
                return []
            if op == "get_symbol_exports_summary_heuristic":
                return [
                    {"file": "src/api/lib.rs", "symbol": "Router", "visibility": "pub"},
                    {"file": "src/api/lib.rs", "symbol": "_internal_router", "visibility": "pub"},
                    {"file": "pkg/service.py", "symbol": "PublicService", "visibility": ""},
                    {"file": "pkg/service.py", "symbol": "_private_helper", "visibility": ""},
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("Source: heuristic public-surface inference", output)
        self.assertIn("Router", output)
        self.assertIn("PublicService", output)
        self.assertNotIn("_private_helper", output)

    def test_symbol_exports_summary_excludes_test_files_by_default(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 2}]
            if op == "get_symbol_exports_summary_symbols":
                return [
                    {"symbol": "ProdSymbol", "exporters": 1, "importers": 2},
                    {"symbol": "FakeSession", "exporters": 1, "importers": 0},
                ]
            if op == "get_symbol_exports_summary_files":
                return [
                    {"file": "src/app.py", "n": 1, "symbols": ["ProdSymbol"]},
                    {"file": "tests/test_app.py", "n": 1, "symbols": ["FakeSession"]},
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("src/app.py", output)
        self.assertNotIn("tests/test_app.py", output)

    def test_symbol_exports_summary_ranks_by_downstream_importers(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 3}]
            if op == "get_symbol_exports_summary_symbols":
                return [
                    {
                        "symbol": "register",
                        "target_symbol": "register",
                        "alias_edges": 0,
                        "exporters": 3,
                        "importers": 1,
                    },
                    {
                        "symbol": "WorkspaceRegistry",
                        "target_symbol": "WorkspaceRegistry",
                        "alias_edges": 0,
                        "exporters": 1,
                        "importers": 5,
                    },
                ]
            if op == "get_symbol_exports_summary_files":
                return [
                    {"file": "src/core.py", "n": 2, "symbols": ["WorkspaceRegistry", "register"]},
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertLess(output.find("WorkspaceRegistry"), output.find("register"))
        self.assertIn("imported by 5 file(s)", output)

    def test_symbol_exports_summary_surfaces_alias_exports(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 1}]
            if op == "get_symbol_exports_summary_symbols":
                return [
                    {
                        "symbol": "PublicFoo",
                        "target_symbol": "Foo",
                        "alias_edges": 1,
                        "exporters": 1,
                        "importers": 4,
                    }
                ]
            if op == "get_symbol_exports_summary_files":
                return [
                    {"file": "src/index.ts", "n": 1, "symbols": ["PublicFoo -> Foo"]},
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("PublicFoo -> Foo", output)
        self.assertIn("src/index.ts", output)

    def test_symbol_exports_summary_filtered_alias_exports(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 2}]
            if op == "get_symbol_exports_summary_filtered_rows":
                return [
                    {
                        "file": "packages/sdk/js/src/client.ts",
                        "symbol": "OpencodeClientConfig",
                        "target_symbol": "Config",
                        "alias_edges": 1,
                        "importers": 6,
                    },
                    {
                        "file": "packages/sdk/js/src/v2/client.ts",
                        "symbol": "OpencodeClientConfig",
                        "target_symbol": "Config",
                        "alias_edges": 1,
                        "importers": 6,
                    },
                    {
                        "file": "packages/sdk/js/src/client.ts",
                        "symbol": "OpencodeClient",
                        "target_symbol": "OpencodeClient",
                        "alias_edges": 0,
                        "importers": 2,
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_paths=["packages/sdk/js/src/client.ts", "packages/sdk/js/src/v2/client.ts"],
                    symbol_prefix="Opencode",
                )
            )

        self.assertIn("OpencodeClientConfig -> Config", output)
        self.assertIn("packages/sdk/js/src/client.ts", output)


if __name__ == "__main__":
    unittest.main()

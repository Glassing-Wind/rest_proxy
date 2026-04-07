import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/summaries.py"


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

    def test_symbol_imports_summary_formats_rows(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "get_symbol_imports_summary_symbols":
                return [{"symbol": "Foo", "n": 3}]
            if kwargs.get("op") == "get_symbol_imports_summary_files":
                return [{"file": "src/a.py", "n": 2, "symbols": ["Foo", "Bar"]}]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_imports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )
        self.assertIn("Top imported symbols", output)
        self.assertIn("src/a.py", output)

    def test_symbol_exports_summary_applies_filters(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "get_symbol_exports_summary_symbols":
                return [{"symbol": "AppFoo", "n": 3}, {"symbol": "Other", "n": 2}]
            if kwargs.get("op") == "get_symbol_exports_summary_files":
                return [
                    {"file": "src/app.py", "n": 2, "symbols": ["AppFoo", "Other"]},
                    {"file": "tests/test_app.py", "n": 1, "symbols": ["AppFoo"]},
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


if __name__ == "__main__":
    unittest.main()

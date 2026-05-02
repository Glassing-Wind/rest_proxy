import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "tools.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeDriver:
    pass


def load_module(search_summaries_mod):
    spec = importlib.util.spec_from_file_location("tools.brain.search.tools", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    search_pkg = types.ModuleType("tools.brain.search")
    search_pkg.__path__ = []
    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP

    with mock.patch.dict(
        sys.modules,
        {
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.search": search_pkg,
            "tools.brain.search.summaries": search_summaries_mod,
            "mcp.server.fastmcp": mcp_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


class SearchSummaryToolTests(unittest.TestCase):
    def setUp(self):
        self.search_summaries_mod = types.ModuleType("tools.brain.search.summaries")
        self.search_summaries_mod.get_symbol_exports_summary_impl = mock.AsyncMock(
            return_value="exports summary ok"
        )
        self.search_summaries_mod.get_symbol_imports_overview_impl = mock.AsyncMock(
            return_value="imports overview ok"
        )
        self.module = load_module(self.search_summaries_mod)
        self.mcp = FakeMCP()
        self.module.register(self.mcp)

        self.graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        self.graph_bootstrap_mod.require_driver = _require_driver
        self.graph_bootstrap_mod._NEO4J_DB = "neo4j"

    def test_get_symbol_exports_summary_forwards_filters(self):
        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            output = asyncio.run(
                self.mcp.tools["get_symbol_exports_summary"](
                    "/tmp/repo",
                    limit=15,
                    include_paths=["src/*"],
                    exclude_paths=["tests/*"],
                    symbol_prefix="App",
                )
            )

        self.assertEqual(output, "exports summary ok")
        self.search_summaries_mod.get_symbol_exports_summary_impl.assert_awaited_once()
        _, kwargs = self.search_summaries_mod.get_symbol_exports_summary_impl.await_args
        self.assertEqual(kwargs["neo4j_db"], "neo4j")
        self.assertEqual(kwargs["project_path"], "/tmp/repo")
        self.assertEqual(kwargs["limit"], 15)
        self.assertEqual(kwargs["include_paths"], ["src/*"])
        self.assertEqual(kwargs["exclude_paths"], ["tests/*"])
        self.assertEqual(kwargs["symbol_prefix"], "App")

    def test_get_symbol_imports_overview_returns_tool_error_shape(self):
        self.search_summaries_mod.get_symbol_imports_overview_impl = mock.AsyncMock(
            side_effect=RuntimeError("graph unavailable")
        )
        self.module = load_module(self.search_summaries_mod)
        self.mcp = FakeMCP()
        self.module.register(self.mcp)

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            output = asyncio.run(
                self.mcp.tools["get_symbol_imports_overview"](
                    "/tmp/repo",
                    limit=12,
                    include_implicit=True,
                )
            )

        self.assertEqual(
            output,
            "Error summarizing symbol imports overview: graph unavailable",
        )


if __name__ == "__main__":
    unittest.main()

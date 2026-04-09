import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/graph_query.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_module():
    spec = importlib.util.spec_from_file_location("tools.brain.search.graph_query", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.get_workspace_path = lambda workspace_id: workspace_id

    search_core = types.ModuleType("tools.brain.search.core")
    search_core._execute_read = mock.AsyncMock(return_value=[])

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
            "_helpers": helpers_mod,
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.search": search_pkg,
            "tools.brain.search.core": search_core,
            "mcp.server.fastmcp": mcp_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module, search_core


class GraphQueryTests(unittest.TestCase):
    def setUp(self):
        self.module, self.search_core = load_module()
        self.mcp = FakeMCP()
        self.module.register(self.mcp)
        self.graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        self.graph_bootstrap_mod.require_driver = _require_driver
        self.graph_bootstrap_mod._NEO4J_DB = "neo4j"

    def test_find_definitions_prefers_repo_owned_over_generated_and_test_matches(self):
        rows = [
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/sdk/js/src/v2/gen/types.gen.ts",
                "line": 1,
                "type": "TypeAlias",
            },
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/opencode/src/config/config.ts",
                "line": 8,
                "type": "Class",
            },
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/opencode/test/config/config.test.ts",
                "line": 5,
                "type": "Class",
            },
            {
                "project_id": "profilequery123",
                "project_path": "",
                "file": "scratch/query.ts",
                "line": 4,
                "type": "Function",
            },
        ]

        async def fake_execute_read(session, cypher, **kwargs):
            return rows

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["find_definitions"]("Config"))

        lines = [line for line in output.splitlines() if line.startswith("- [")]
        self.assertGreaterEqual(len(lines), 3)
        self.assertIn("packages/opencode/src/config/config.ts:8", lines[0])
        self.assertIn("packages/opencode/test/config/config.test.ts:5", lines[1])
        self.assertIn("packages/sdk/js/src/v2/gen/types.gen.ts:1", lines[2])
        self.assertNotIn("profilequery123", output)


if __name__ == "__main__":
    unittest.main()

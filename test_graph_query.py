import asyncio
import importlib.util
import json
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
    spec = importlib.util.spec_from_file_location("graph_query_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: "ab4dfef13e8c"
    helpers_mod.get_workspace_path = lambda workspace_id: "/Users/michaelmarler/Projects/rental"

    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP

    search_core_mod = types.ModuleType("tools.brain.search.core")

    async def _execute_read(*args, **kwargs):
        return []

    search_core_mod._execute_read = _execute_read

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    search_pkg = types.ModuleType("tools.brain.search")
    search_pkg.__path__ = []

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "mcp.server.fastmcp": mcp_mod,
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.search": search_pkg,
            "tools.brain.search.core": search_core_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


class GraphQueryToolTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.mcp = FakeMCP()
        self.module.register(self.mcp)
        self.graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        self.graph_bootstrap_mod.require_driver = _require_driver
        self.graph_bootstrap_mod._NEO4J_DB = "neo4j"

    def test_query_graph_binds_workspace_and_project_params(self):
        async def fake_execute_read(session, cypher, **params):
            self.assertEqual("MATCH (f:File {project_id: $pid}) RETURN count(f) AS c", cypher)
            self.assertEqual("ab4dfef13e8c", params["pid"])
            self.assertEqual("ab4dfef13e8c", params["project_id"])
            self.assertEqual("/Users/michaelmarler/Projects/rental", params["workspace_path"])
            self.assertEqual("/Users/michaelmarler/Projects/rental", params["project_path"])
            self.assertEqual("/Users/michaelmarler/Projects/rental", params["workspace_id"])
            return [{"c": 138}]

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["query_graph"](
                        "MATCH (f:File {project_id: $pid}) RETURN count(f) AS c",
                        workspace_id="/Users/michaelmarler/Projects/rental",
                    )
                )
        self.assertEqual([{"c": 138}], json.loads(output))

    def test_resolve_graph_project_returns_workspace_mapping(self):
        output = asyncio.run(
            self.mcp.tools["resolve_graph_project"]("/Users/michaelmarler/Projects/rental")
        )
        payload = json.loads(output)
        self.assertEqual("ab4dfef13e8c", payload["project_id"])
        self.assertEqual("/Users/michaelmarler/Projects/rental", payload["workspace_path"])


if __name__ == "__main__":
    unittest.main()

import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/hands/dev.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeSession:
    rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute_read(self, fn):
        return await fn(self)

    async def run(self, cypher, **params):
        return self

    async def data(self):
        return list(self.rows)


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_dev_module():
    spec = importlib.util.spec_from_file_location("tools.hands.dev", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (None, None, None, None, None)
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.normalize_neo4j_path = lambda path: path.replace("\\", "/")
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/workspace"

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

    async def _require_driver():
        return FakeDriver()

    graph_bootstrap_mod.require_driver = _require_driver
    graph_bootstrap_mod._NEO4J_DB = "neo4j"

    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP
    neo4j_mod = types.ModuleType("neo4j")
    neo4j_mod.unit_of_work = lambda *args, **kwargs: (lambda fn: fn)

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "graph_bootstrap": graph_bootstrap_mod,
            "mcp.server.fastmcp": mcp_mod,
            "neo4j": neo4j_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module, graph_bootstrap_mod


class SwiftDocLookupTests(unittest.TestCase):
    def setUp(self):
        self.module, self.graph_bootstrap_mod = load_dev_module()
        self.mcp = FakeMCP()
        self.module.register(self.mcp)

    def test_swift_doc_lookup_prefers_graph_enrichment(self):
        FakeSession.rows = [
            {
                "name": "SidebarView",
                "kind": "Struct",
                "start_line": 3,
                "end_line": 1258,
                "swift_doc_comment": "Indexed doc comment",
                "doc_comment": None,
                "swift_usr": "s:12FrameCreator11SidebarViewV",
            }
        ]

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch("os.path.exists", return_value=True):
                output = asyncio.run(
                    self.mcp.tools["swift_doc_lookup"](
                        "/tmp/workspace",
                        "FrameCreator/Views/SidebarView.swift",
                        "SidebarView",
                    )
                )

        self.assertIn("**SidebarView** (Struct)", output)
        self.assertIn("USR: `s:12FrameCreator11SidebarViewV`", output)
        self.assertIn("Indexed doc comment", output)


if __name__ == "__main__":
    unittest.main()

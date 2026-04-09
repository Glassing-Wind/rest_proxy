import asyncio
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/tools.py"


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


def load_tools_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("tools.brain.graph.tools", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/repo"

    graph_core_mod = types.ModuleType("tools.brain.graph.core")

    async def _execute_read(*args, **kwargs):
        return []

    graph_core_mod._execute_read = _execute_read
    graph_core_mod._get_cli_flow_summary = mock.AsyncMock(return_value="cli/app.py -> main")

    flow_summary_mod = types.ModuleType("tools.brain.graph.flow_summary")
    flow_summary_mod.get_app_flow_summary_impl = mock.AsyncMock(return_value="No UI → API → Service → DB paths found.")
    flow_summary_mod.get_backend_flow_summary_impl = mock.AsyncMock(
        return_value="src/api/routes.py -> src/services/user_service.py -> src/db/models.py"
    )
    flow_summary_mod.get_apple_build_summary_impl = mock.AsyncMock(return_value="No Apple build graph paths found.")

    overview_mod = types.ModuleType("tools.brain.graph.overview")
    overview_mod.get_directory_snapshot_impl = mock.AsyncMock(return_value="snapshot")
    overview_mod.get_project_overview_impl = mock.AsyncMock(return_value="overview")
    overview_mod.get_repo_dependency_summary_impl = mock.AsyncMock(return_value="deps")

    utility_mod = types.ModuleType("tools.brain.graph.utility")
    utility_mod.get_heuristic_flow_summary_impl = mock.AsyncMock(return_value="No heuristic flows found.")
    utility_mod.get_topology_summary_impl = mock.AsyncMock(return_value="topology")

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
            "tools.brain.graph.overview": overview_mod,
            "tools.brain.graph.utility": utility_mod,
        },
    ):
        spec.loader.exec_module(module)
    module._graph_bootstrap_mod = graph_bootstrap_mod
    return module


class GraphToolTriageTests(unittest.TestCase):
    def test_get_flow_summary_labels_selected_strategy(self):
        module = load_tools_module()
        mcp = FakeMCP()
        module.register(mcp)

        with mock.patch.dict(sys.modules, {"graph_bootstrap": module._graph_bootstrap_mod}):
            output = asyncio.run(mcp.tools["get_flow_summary"]("/tmp/repo", mode="backend"))

        self.assertIn("### Flow Type: API -> Service -> DB", output)
        self.assertIn("src/api/routes.py -> src/services/user_service.py", output)


if __name__ == "__main__":
    unittest.main()

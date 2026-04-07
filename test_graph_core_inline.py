import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/core.py"


def load_core_module():
    spec = importlib.util.spec_from_file_location("tools.brain.graph.core", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (None, None, None, None, types.SimpleNamespace(debug_log=lambda *a, **k: None))
    helpers_mod.get_project_id = lambda workspace_id: "proj123"

    neo4j_utils_mod = types.ModuleType("graphrag_core.neo4j")

    async def _execute_write(*args, **kwargs):
        return None

    async def _execute_read(*args, **kwargs):
        return []

    neo4j_utils_mod.execute_write = _execute_write
    neo4j_utils_mod.execute_read = _execute_read

    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = object

    graph_pkg = types.ModuleType("tools")
    graph_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    graph_subpkg = types.ModuleType("tools.brain.graph")
    graph_subpkg.__path__ = []

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "graphrag_core": types.ModuleType("graphrag_core"),
            "graphrag_core.neo4j": neo4j_utils_mod,
            "mcp.server.fastmcp": mcp_mod,
            "tools": graph_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.graph": graph_subpkg,
        },
    ):
        spec.loader.exec_module(module)
    return module


class GraphCoreInlineTests(unittest.TestCase):
    def test_run_post_index_graph_build_runs_inline_asset_phase(self):
        module = load_core_module()
        events = []

        def fake_ensure():
            return None

        async def fake_retry(fn, label, project_path):
            events.append((label, "start", project_path))
            result = await fn(project_path)
            events.append((label, "done", project_path))
            return result

        async def fake_asset(project_path):
            return "assets ok"

        module._ensure_graph_runtime_configured = fake_ensure
        module._run_graph_build_with_retry = fake_retry
        module._build_asset_graph_impl = fake_asset
        module._debug_log = lambda *args, **kwargs: None
        module._record_metric = lambda *args, **kwargs: None

        result = asyncio.run(module.run_post_index_graph_build("/tmp/project"))

        self.assertEqual(result, "assets ok")
        self.assertEqual(
            events,
            [
                ("assets", "start", "/tmp/project"),
                ("assets", "done", "/tmp/project"),
            ],
        )


if __name__ == "__main__":
    unittest.main()

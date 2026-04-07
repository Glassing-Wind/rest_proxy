import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/utility.py"


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_module():
    spec = importlib.util.spec_from_file_location("graph_utility_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/repo"

    graph_core_mod = types.ModuleType("tools.brain.graph.core")
    graph_core_mod._summarize_batches = lambda event, limit: (1, 2, 3)
    graph_core_mod.get_last_graph_build_metric = lambda: {"project_path": "/tmp/repo", "elapsed_ms": 99}

    async def _execute_read(*args, **kwargs):
        return []

    graph_core_mod._execute_read = _execute_read

    runtime_mod = types.ModuleType("tools.brain.graph.runtime")
    runtime_mod.get_recent_metrics = lambda limit: [{"event": "done", "project_path": "/tmp/repo", "elapsed_ms": 99}]
    runtime_mod.record_metric = lambda *args, **kwargs: None

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
            "tools.brain.graph.runtime": runtime_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


class GraphUtilityTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_graph_build_metrics_formats_recent_events(self):
        output = asyncio.run(self.module.get_graph_build_metrics_impl(50))
        self.assertIn("Graph build metrics", output)
        self.assertIn("import_graph_batch", output)
        self.assertIn("done /tmp/repo elapsed_ms=99", output)

    def test_topology_summary_formats_rows(self):
        async def fake_execute_read(session, query, **kwargs):
            return [{"fp": "src/a.py", "inbound": 2, "outbound": 3}]

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_topology_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/repo",
                    limit=10,
                )
            )
        self.assertIn("src/a.py", output)
        self.assertIn("2 incoming, 3 outgoing", output)


if __name__ == "__main__":
    unittest.main()

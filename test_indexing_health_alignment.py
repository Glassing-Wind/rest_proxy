import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/hands/indexing.py"


class FakeMCP:
    def tool(self):
        def decorator(fn):
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


class FakeMemoryStore:
    async def open_pool(self):
        return None

    def _pg_pool_available(self):
        return False


def load_indexing_module():
    spec = importlib.util.spec_from_file_location("hands_indexing_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    jobs_mod = types.ModuleType("_jobs")
    jobs_mod._JOBS = {}
    jobs_mod._JOBS_LOCK = mock.MagicMock()
    jobs_mod._drain_proc_output = lambda *args, **kwargs: None
    jobs_mod._finalize_job = lambda *args, **kwargs: None
    jobs_mod.client_session_id = types.SimpleNamespace(get=lambda: None)

    helpers_mod = types.ModuleType("_helpers")
    fake_memory = FakeMemoryStore()
    helpers_mod.get_memory_modules = lambda: (fake_memory, None, None, None, None)
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/repo"

    runtime_mod = types.ModuleType("_runtime")
    runtime_mod.resolve_python_runtime = lambda: {"cmd": ["python"], "python": "python", "source": "test"}

    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = FakeMCP

    config_mod = types.ModuleType("graphrag_core.config")
    config_mod.load_env = lambda: None

    watcher_mod = types.ModuleType("graphrag_core.indexing.watcher")
    watcher_mod.load_watched_config = lambda: None
    watcher_mod.start_watcher = lambda index_fn: None
    watcher_mod.stop_watcher = lambda: None
    watcher_mod.add_watch = lambda path: True
    watcher_mod.remove_watch = lambda path: True

    manifest_mod = types.ModuleType("graphrag_core.indexing.manifest")
    manifest_mod.build_manifest = lambda project_path: [
        {"rel_path": "src/app.py", "abs_path": "/tmp/repo/src/app.py"}
    ]
    manifest_mod.load_indexignore_patterns = lambda *args, **kwargs: []
    manifest_mod.suggest_indexignore_entries = lambda *args, **kwargs: []

    registry_mod = types.ModuleType("graphrag_core.indexing.registry")
    async def _record_indexed_project(*args, **kwargs):
        return None
    registry_mod.record_indexed_project = _record_indexed_project

    neo4j_utils_mod = types.ModuleType("graphrag_core.neo4j")
    async def _execute_read(*args, **kwargs):
        return []
    neo4j_utils_mod.execute_read = _execute_read

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")
    async def _require_driver():
        return FakeDriver()
    graph_bootstrap_mod.require_driver = _require_driver
    graph_bootstrap_mod._NEO4J_DB = "neo4j"

    sys.modules["_jobs"] = jobs_mod
    sys.modules["_helpers"] = helpers_mod
    sys.modules["_runtime"] = runtime_mod
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod
    sys.modules["graphrag_core.config"] = config_mod
    sys.modules["graphrag_core.indexing.watcher"] = watcher_mod
    sys.modules["graphrag_core.indexing.manifest"] = manifest_mod
    sys.modules["graphrag_core.indexing.registry"] = registry_mod
    sys.modules["graphrag_core.neo4j"] = neo4j_utils_mod
    sys.modules["graph_bootstrap"] = graph_bootstrap_mod
    spec.loader.exec_module(module)
    return module


class IndexingHealthAlignmentTests(unittest.TestCase):
    def test_health_reports_aligned_runs(self):
        module = load_indexing_module()

        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "get_indexing_health_files":
                return [
                    {
                        "fp": "src/app.py",
                        "ts": 2_000_000,
                        "vts": 2_000_000,
                        "parsed": True,
                    }
                ]
            if op == "get_indexing_health_runs":
                return [
                    {
                        "struct_active_run_id": "struct-1",
                        "struct_last_successful_run_id": "struct-1",
                        "struct_index_status": "done",
                        "semantic_active_run_id": "sem-1",
                        "semantic_last_successful_run_id": "sem-1",
                        "semantic_target_struct_run_id": "struct-1",
                        "semantic_active_struct_run_id": "struct-1",
                        "semantic_index_status": "done",
                    }
                ]
            return []

        with mock.patch.object(module, "_execute_read", side_effect=fake_execute_read), mock.patch(
            "os.path.exists", return_value=True
        ), mock.patch("os.path.getmtime", return_value=1000.0):
            output = asyncio.run(module.get_indexing_health("/tmp/repo"))

        self.assertIn("**Run Alignment**:        ✅ Aligned", output)
        self.assertIn("No actions required. Everything looks healthy!", output)

    def test_health_reports_misaligned_runs(self):
        module = load_indexing_module()

        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "get_indexing_health_files":
                return [
                    {
                        "fp": "src/app.py",
                        "ts": 2_000_000,
                        "vts": 2_000_000,
                        "parsed": True,
                    }
                ]
            if op == "get_indexing_health_runs":
                return [
                    {
                        "struct_active_run_id": "struct-2",
                        "struct_last_successful_run_id": "struct-2",
                        "struct_index_status": "done",
                        "semantic_active_run_id": "sem-1",
                        "semantic_last_successful_run_id": "sem-1",
                        "semantic_target_struct_run_id": "struct-1",
                        "semantic_active_struct_run_id": "struct-1",
                        "semantic_index_status": "done",
                    }
                ]
            return []

        with mock.patch.object(module, "_execute_read", side_effect=fake_execute_read), mock.patch(
            "os.path.exists", return_value=True
        ), mock.patch("os.path.getmtime", return_value=1000.0):
            output = asyncio.run(module.get_indexing_health("/tmp/repo"))

        self.assertIn("**Run Alignment**:        ⚠️ Not aligned", output)
        self.assertIn("Structural and semantic runs are not aligned.", output)


if __name__ == "__main__":
    unittest.main()

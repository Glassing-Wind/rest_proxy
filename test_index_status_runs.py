import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "hands" / "indexing.py"


class FakeMCP:
    def tool(self):
        def decorator(fn):
            return fn

        return decorator


def load_indexing_module():
    spec = importlib.util.spec_from_file_location("hands_indexing_status_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    jobs_mod = types.ModuleType("_jobs")
    jobs_mod._JOBS = {}
    jobs_mod._JOBS_LOCK = mock.MagicMock()
    jobs_mod._drain_proc_output = lambda *args, **kwargs: None
    jobs_mod._finalize_job = lambda *args, **kwargs: None
    jobs_mod.client_session_id = types.SimpleNamespace(get=lambda: None)

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (None, None, None, None, None)
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
    manifest_mod.build_manifest = lambda project_path: []
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

    sys.modules["_jobs"] = jobs_mod
    sys.modules["_helpers"] = helpers_mod
    sys.modules["_runtime"] = runtime_mod
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod
    sys.modules["graphrag_core.config"] = config_mod
    sys.modules["graphrag_core.indexing.watcher"] = watcher_mod
    sys.modules["graphrag_core.indexing.manifest"] = manifest_mod
    sys.modules["graphrag_core.indexing.registry"] = registry_mod
    sys.modules["graphrag_core.neo4j"] = neo4j_utils_mod
    spec.loader.exec_module(module)
    return module, jobs_mod


class IndexStatusRunsTests(unittest.TestCase):
    def test_get_index_status_includes_run_summary(self):
        module, jobs_mod = load_indexing_module()
        jobs_mod._JOBS["job1"] = {
            "status": "done",
            "session_id": None,
            "project_id": "proj123",
            "project_path": "/tmp/repo",
            "file_count": 3,
            "struct_rc": 0,
            "sem_rc": 0,
            "logs": [],
            "started_at": 100.0,
            "finished_at": 101.5,
            "runtime_python": "python",
            "runtime_source": "test",
            "runtime_conda_env": "lmproxy",
            "run_summary": {
                "struct_active_run_id": "struct-1",
                "semantic_active_run_id": "sem-1",
                "semantic_active_struct_run_id": "struct-1",
                "struct_index_status": "done",
                "semantic_index_status": "done",
            },
            "metrics": {
                "struct": {
                    "parse_s": 1.25,
                    "nodes_s": 0.1,
                    "imports_s": 0.02,
                    "rels_s": 0.01,
                    "calls_s": 0.4,
                    "total_s": 1.78,
                },
                "semantic": {
                    "new_chunks": 12,
                    "skipped_chunks": 5,
                    "parsed_files": 3,
                    "skipped_files": 0,
                    "total_s": 0.62,
                },
                "gds": {
                    "leiden": "community written to 3 File nodes.",
                    "betweenness": "betweenness written to 3 File nodes. (estimated_bytes_max=64, sampling_size=3)",
                    "wcc": "1 isolated File nodes marked.",
                },
            },
        }

        with mock.patch("time.time", return_value=102.0):
            output = asyncio.run(module.get_index_status("job1"))

        self.assertIn("struct_run: struct-1", output)
        self.assertIn("semantic_run: sem-1", output)
        self.assertIn("semantic_struct_run: struct-1", output)
        self.assertIn("aligned:    yes", output)
        self.assertIn("struct_metrics: parse=1.25s", output)
        self.assertIn("semantic_metrics: new=12 skipped=5", output)
        self.assertIn("gds: leiden=community written to 3 File nodes.", output)


if __name__ == "__main__":
    unittest.main()

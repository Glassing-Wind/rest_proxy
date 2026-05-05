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


class FakeCursor:
    def __init__(self, rows_by_query):
        self._rows_by_query = rows_by_query
        self._active_rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params=None):
        query_norm = " ".join(str(query).split()).lower()
        for needle, rows in self._rows_by_query.items():
            if needle in query_norm:
                self._active_rows = list(rows)
                break
        else:
            self._active_rows = []
        return None

    async def fetchall(self):
        return list(self._active_rows)


class FakeConnection:
    def __init__(self, rows_by_query):
        self._rows_by_query = rows_by_query

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self._rows_by_query)


class FakePool:
    def __init__(self, rows_by_query):
        self._rows_by_query = rows_by_query

    def connection(self):
        return FakeConnection(self._rows_by_query)


class FakePgMemoryStore(FakeMemoryStore):
    def __init__(self, rows_by_query):
        self._pg_rows_by_query = rows_by_query
        self._pg_pool = FakePool(rows_by_query)

    def _pg_pool_available(self):
        return True


def load_indexing_module():
    spec = importlib.util.spec_from_file_location("hands_indexing_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    jobs_mod = types.ModuleType("_jobs")
    jobs_mod._JOBS = {}
    jobs_mod._JOBS_LOCK = mock.MagicMock()
    jobs_mod.claim_project_job_lock = lambda *args, **kwargs: (True, None)
    jobs_mod._drain_proc_output = lambda *args, **kwargs: None
    jobs_mod._finalize_job = lambda *args, **kwargs: None
    jobs_mod._job_control_paths = lambda *args, **kwargs: {}
    jobs_mod._persist_job_state = lambda *args, **kwargs: None
    jobs_mod._release_project_job_lock = lambda *args, **kwargs: None
    jobs_mod._reconcile_job_process_state = lambda *args, **kwargs: None
    jobs_mod._render_job_logs = lambda *args, **kwargs: []
    jobs_mod.load_job_record = lambda *args, **kwargs: None
    jobs_mod.register_main_loop = lambda *args, **kwargs: None
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
    def test_semantic_expected_excludes_empty_files(self):
        module = load_indexing_module()

        with mock.patch("os.path.getsize", return_value=0):
            self.assertFalse(
                module._is_semantic_expected_path(
                    "tests/fixtures/empty.txt",
                    "/tmp/repo/tests/fixtures/empty.txt",
                    "txt",
                )
            )

    def test_semantic_expected_excludes_xcassets_resource_metadata(self):
        module = load_indexing_module()
        fake_ts_pack = types.SimpleNamespace(
            should_use_line_window_fallback=lambda _path: False,
            detect_language_from_extension=lambda ext: "json" if ext == "json" else None,
            detect_language=lambda _path: "json",
            has_language=lambda lang: lang == "json",
        )

        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            self.assertFalse(
                module._is_semantic_expected_path(
                    "App/Assets.xcassets/AppIcon.appiconset/Contents.json",
                    "/tmp/repo/App/Assets.xcassets/AppIcon.appiconset/Contents.json",
                    "json",
                )
            )

    def test_semantic_expected_excludes_info_plist_metadata(self):
        module = load_indexing_module()
        self.assertFalse(
            module._is_semantic_expected_path(
                "App/Food-Truck-Info.plist",
                "/tmp/repo/App/Food-Truck-Info.plist",
                "plist",
            )
        )

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

    def test_parse_success_rate_uses_source_eligible_files(self):
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
                    },
                    {
                        "fp": "docs/.cursor/rules.mdc",
                        "ts": 2_000_000,
                        "vts": 2_000_000,
                        "parsed": False,
                    },
                    {
                        "fp": "Makefile",
                        "ts": 2_000_000,
                        "vts": 2_000_000,
                        "parsed": False,
                    },
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

        self.assertIn("**Parse Success Rate**: 100.0% (1/1 source-eligible files)", output)
        self.assertIn("Support-file coverage: 33.3% (1/3 across all manifest-kept files)", output)

    def test_health_uses_structural_and_pg_coverage_not_timestamp_subsets(self):
        module = load_indexing_module()
        fake_memory = FakePgMemoryStore(
            {
                "select file_path, bool_or(coalesce((metadata->>'semantic_contract_version')::int, 0) = %s) as current_contract from codebase_embeddings": [
                    ("src/app.py", True),
                    ("src/lib.py", True),
                ],
                "select file_path, count(*) from codebase_embeddings": [],
            }
        )

        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "get_indexing_health_files":
                return [
                    {
                        "fp": "src/app.py",
                        "ts": None,
                        "vts": None,
                        "parsed": True,
                    },
                    {
                        "fp": "src/lib.py",
                        "ts": None,
                        "vts": None,
                        "parsed": True,
                    },
                ]
            if op == "get_indexing_health_runs":
                return [
                    {
                        "struct_active_run_id": "struct-1",
                        "struct_last_successful_run_id": "struct-1",
                        "struct_index_status": "done",
                        "semantic_active_run_id": "sem-1",
                        "semantic_last_successful_run_id": "sem-1",
                        "semantic_target_struct_run_id": None,
                        "semantic_active_struct_run_id": None,
                        "semantic_index_status": "done",
                    }
                ]
            return []

        manifest = [
            {"rel_path": "src/app.py", "abs_path": "/tmp/repo/src/app.py"},
            {"rel_path": "src/lib.py", "abs_path": "/tmp/repo/src/lib.py"},
        ]

        with mock.patch.object(module, "_execute_read", side_effect=fake_execute_read), mock.patch.object(
            module, "get_memory_modules", return_value=(fake_memory, None, None, None, None)
        ), mock.patch.object(module, "build_manifest", return_value=manifest), mock.patch(
            "os.path.exists", return_value=True
        ), mock.patch("os.path.getmtime", return_value=1000.0), mock.patch(
            "os.path.getsize", return_value=1
        ):
            output = asyncio.run(module.get_indexing_health("/tmp/repo"))

        self.assertIn("Files in structural index: 2", output)
        self.assertIn("Files in semantic index:   2", output)
        self.assertIn("**Sync Status**: ✅ Healthy", output)
        self.assertIn("**Run Alignment**:        ✅ Aligned", output)
        self.assertIn("No actions required. Everything looks healthy!", output)

    def test_health_treats_verified_coverage_as_aligned_even_when_semantic_struct_run_lags(self):
        module = load_indexing_module()
        fake_memory = FakePgMemoryStore(
            {
                "select file_path, bool_or(coalesce((metadata->>'semantic_contract_version')::int, 0) = %s) as current_contract from codebase_embeddings": [
                    ("src/app.py", True),
                    ("src/lib.py", True),
                ],
                "select file_path, count(*) from codebase_embeddings": [],
            }
        )

        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "get_indexing_health_files":
                return [
                    {
                        "fp": "src/app.py",
                        "ts": None,
                        "vts": None,
                        "parsed": True,
                    },
                    {
                        "fp": "src/lib.py",
                        "ts": None,
                        "vts": None,
                        "parsed": True,
                    },
                ]
            if op == "get_indexing_health_runs":
                return [
                    {
                        "struct_active_run_id": "struct-2",
                        "struct_last_successful_run_id": "struct-2",
                        "struct_index_status": "done",
                        "semantic_active_run_id": "sem-2",
                        "semantic_last_successful_run_id": "sem-2",
                        "semantic_target_struct_run_id": "struct-1",
                        "semantic_active_struct_run_id": "struct-1",
                        "semantic_index_status": "done",
                    }
                ]
            return []

        manifest = [
            {"rel_path": "src/app.py", "abs_path": "/tmp/repo/src/app.py"},
            {"rel_path": "src/lib.py", "abs_path": "/tmp/repo/src/lib.py"},
        ]

        with mock.patch.object(module, "_execute_read", side_effect=fake_execute_read), mock.patch.object(
            module, "get_memory_modules", return_value=(fake_memory, None, None, None, None)
        ), mock.patch.object(module, "build_manifest", return_value=manifest), mock.patch(
            "os.path.exists", return_value=True
        ), mock.patch("os.path.getmtime", return_value=1000.0), mock.patch(
            "os.path.getsize", return_value=1
        ):
            output = asyncio.run(module.get_indexing_health("/tmp/repo"))

        self.assertIn("**Sync Status**: ✅ Healthy", output)
        self.assertIn("**Run Alignment**:        ✅ Aligned", output)
        self.assertIn("No actions required. Everything looks healthy!", output)

    def test_health_ignores_intentionally_skipped_structural_dotfiles(self):
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

        manifest = [
            {"rel_path": "src/app.py", "abs_path": "/tmp/repo/src/app.py"},
            {"rel_path": ".gitignore", "abs_path": "/tmp/repo/.gitignore"},
            {"rel_path": ".indexignore", "abs_path": "/tmp/repo/.indexignore"},
            {"rel_path": ".env.example", "abs_path": "/tmp/repo/.env.example"},
        ]

        with mock.patch.object(module, "_execute_read", side_effect=fake_execute_read), mock.patch.object(
            module, "build_manifest", return_value=manifest
        ), mock.patch("os.path.exists", return_value=True), mock.patch(
            "os.path.getmtime", return_value=1000.0
        ):
            output = asyncio.run(module.get_indexing_health("/tmp/repo"))

        self.assertIn("**Sync Status**: ✅ Healthy", output)
        self.assertNotIn("Files missing from index entirely", output)

    def test_health_reports_partial_apple_graph_coverage(self):
        module = load_indexing_module()

        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "get_indexing_health_files":
                return [
                    {
                        "fp": "FrameCreator.xcodeproj/project.pbxproj",
                        "ts": 2_000_000,
                        "vts": None,
                        "parsed": True,
                    },
                    {
                        "fp": "FrameCreator.xcodeproj/xcshareddata/xcschemes/FrameCreator.xcscheme",
                        "ts": 2_000_000,
                        "vts": None,
                        "parsed": True,
                    },
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
            if op == "get_indexing_health_apple_graph":
                return [
                    {
                        "project_files": 1,
                        "workspace_files": 1,
                        "scheme_files": 1,
                        "resource_like_files": 3,
                        "targets": 2,
                        "schemes": 0,
                        "workspaces": 0,
                        "resources": 0,
                        "bundles_file_edges": 0,
                        "builds_target_edges": 0,
                        "references_project_edges": 0,
                    }
                ]
            return []

        manifest = [
            {"rel_path": "FrameCreator.xcodeproj/project.pbxproj", "abs_path": "/tmp/repo/FrameCreator.xcodeproj/project.pbxproj"},
            {"rel_path": "FrameCreator.xcodeproj/xcshareddata/xcschemes/FrameCreator.xcscheme", "abs_path": "/tmp/repo/FrameCreator.xcodeproj/xcshareddata/xcschemes/FrameCreator.xcscheme"},
        ]

        with mock.patch.object(module, "_execute_read", side_effect=fake_execute_read), mock.patch.object(
            module, "build_manifest", return_value=manifest
        ), mock.patch("os.path.exists", return_value=True), mock.patch(
            "os.path.getmtime", return_value=1000.0
        ):
            output = asyncio.run(module.get_indexing_health("/tmp/repo"))

        self.assertIn("## 1.75 Apple Build Coverage", output)
        self.assertIn("**Apple Graph Status**: ⚠️ Partial", output)
        self.assertIn("scheme files exist, but no XcodeScheme nodes were materialized", output)
        self.assertIn("workspace metadata exists, but no XcodeWorkspace nodes were materialized", output)
        self.assertIn("Apple build metadata is only partially materialized.", output)


if __name__ == "__main__":
    unittest.main()

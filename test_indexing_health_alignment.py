import asyncio
import importlib.util
import sys
import tempfile
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


class IndexedProjectsTests(unittest.TestCase):
    def test_graph_registry_query_uses_current_project_properties(self):
        module = load_indexing_module()
        graph_bootstrap = types.ModuleType("graph_bootstrap")
        graph_bootstrap._NEO4J_ENABLED = True
        graph_bootstrap._NEO4J_DB = "proxy"
        graph_bootstrap.require_driver = mock.AsyncMock(return_value=FakeDriver())
        seen_queries = []

        async def fake_execute_read(session, query, **kwargs):
            seen_queries.append(str(query))
            return [
                {
                    "id": "project-id",
                    "path": "/tmp/project",
                    "last_indexed": 10,
                }
            ]

        with (
            mock.patch.dict(sys.modules, {"graph_bootstrap": graph_bootstrap}),
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
        ):
            output = asyncio.run(module.get_indexed_projects())

        self.assertIn("/tmp/project", output)
        self.assertNotIn("source_url", "\n".join(seen_queries))


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
    spec = importlib.util.spec_from_file_location(
        "hands_indexing_under_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    jobs_mod = types.ModuleType("_jobs")
    jobs_mod._JOBS = {}
    jobs_mod._JOBS_LOCK = mock.MagicMock()
    jobs_mod._release_index_capacity_lock = lambda *args, **kwargs: None
    jobs_mod.claim_project_job_lock = lambda *args, **kwargs: (True, None)
    jobs_mod.claim_index_capacity_lock = lambda *args, **kwargs: (True, None)
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
    helpers_mod.normalize_neo4j_path = lambda path: str(path).replace("\\", "/")

    runtime_mod = types.ModuleType("_runtime")
    runtime_mod.resolve_python_runtime = lambda: {
        "cmd": ["python"],
        "python": "python",
        "source": "test",
    }

    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = FakeMCP
    fastmcp_mod.Context = type("Context", (), {})

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

    module_stubs = {
        "_jobs": jobs_mod,
        "_helpers": helpers_mod,
        "_runtime": runtime_mod,
        "mcp.server.fastmcp": fastmcp_mod,
        "graphrag_core.config": config_mod,
        "graphrag_core.indexing.watcher": watcher_mod,
        "graphrag_core.indexing.manifest": manifest_mod,
        "graphrag_core.indexing.registry": registry_mod,
        "graphrag_core.neo4j": neo4j_utils_mod,
        "graph_bootstrap": graph_bootstrap_mod,
    }
    previous_modules = {name: sys.modules.get(name) for name in module_stubs}
    sys.modules.update(module_stubs)
    spec.loader.exec_module(module)
    for name, previous in previous_modules.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    module._test_graph_bootstrap = graph_bootstrap_mod
    return module


def run_indexing_health(module, workspace_id: str, audit: bool = False) -> str:
    with mock.patch.dict(
        sys.modules,
        {"graph_bootstrap": module._test_graph_bootstrap},
    ):
        return asyncio.run(module.get_indexing_health(workspace_id, audit=audit))


class IndexingHealthAlignmentTests(unittest.TestCase):
    def test_watch_project_syncs_valid_mcp_client_roots(self):
        module = load_indexing_module()

        class FakeRootsSession:
            async def list_roots(self):
                return types.SimpleNamespace(
                    roots=[
                        types.SimpleNamespace(uri=first.as_uri()),
                        types.SimpleNamespace(uri=second.as_uri()),
                        types.SimpleNamespace(uri=first.as_uri()),
                        types.SimpleNamespace(uri="https://example.com/repo"),
                        types.SimpleNamespace(uri=(base / "missing").as_uri()),
                    ]
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            first = base / "first repo"
            second = base / "second"
            first.mkdir()
            second.mkdir()
            watched: set[str] = set()

            def add_watch(path):
                if path in watched:
                    return False
                watched.add(path)
                return True

            ctx = types.SimpleNamespace(session=FakeRootsSession())
            with mock.patch.object(module.index_watcher, "add_watch", side_effect=add_watch):
                output = asyncio.run(module.watch_project(ctx=ctx))

        self.assertIn("## MCP Client Roots Watch Sync", output)
        self.assertIn("Roots advertised: 5", output)
        self.assertIn("Newly pinned: 2", output)
        self.assertIn("Ignored: 2", output)
        self.assertIn(str(first.resolve()), output)
        self.assertIn(str(second.resolve()), output)
        self.assertIn("unsupported scheme `https`", output)
        self.assertIn("path is not an existing directory", output)

    def test_watch_project_reports_client_without_roots_support(self):
        module = load_indexing_module()

        class UnsupportedRootsSession:
            async def list_roots(self):
                raise RuntimeError("List roots not supported")

        ctx = types.SimpleNamespace(session=UnsupportedRootsSession())
        output = asyncio.run(module.watch_project(ctx=ctx))

        self.assertIn("Client roots are unavailable or unsupported", output)
        self.assertIn("Pass workspace_id explicitly", output)

    def test_watch_project_without_request_context_requires_explicit_path(self):
        module = load_indexing_module()
        output = asyncio.run(module.watch_project())

        self.assertIn("unavailable outside an MCP request", output)
        self.assertIn("Pass workspace_id explicitly", output)

    def test_get_index_status_lists_active_jobs_with_status_when_missing(self):
        module = load_indexing_module()
        with module._JOBS_LOCK:
            module._JOBS.clear()
            module._JOBS["abc12345"] = {
                "status": "running",
                "session_id": "owner-session",
                "project_path": "/tmp/repo",
                "project_id": "proj123",
                "file_count": 12,
                "started_at": 1000.0,
                "last_log_at": 1000.0,
                "logs": [],
            }

        with mock.patch.object(module.time, "time", return_value=1065.0):
            output = asyncio.run(module.get_index_status("missing"))

        self.assertIn("No job found for id 'missing'.", output)
        self.assertIn("Active jobs:", output)
        self.assertIn("abc12345: RUNNING 65s /tmp/repo", output)

    def test_cancel_index_job_force_overrides_strict_session(self):
        module = load_indexing_module()
        module.client_session_id = types.SimpleNamespace(get=lambda: "other-session")
        with module._JOBS_LOCK:
            module._JOBS.clear()
            module._JOBS["abc12345"] = {
                "status": "running",
                "session_id": "owner-session",
                "project_path": "/tmp/repo",
                "project_id": "proj123",
                "file_count": 12,
                "started_at": 1000.0,
                "last_log_at": 1000.0,
                "logs": [],
                "struct_pid": None,
                "sem_pid": None,
            }

        with mock.patch.dict("os.environ", {"LM_PROXY_STRICT_JOB_SESSION": "true"}):
            denied = asyncio.run(module.cancel_index_job("abc"))
            forced = asyncio.run(module.cancel_index_job("abc", force=True))

        self.assertIn("Access Denied", denied)
        self.assertIn("force=True", denied)
        self.assertIn("Cancel requested for job abc12345", forced)
        self.assertIn("Admin override used.", forced)
        with module._JOBS_LOCK:
            job = module._JOBS["abc12345"]
            self.assertEqual(job["status"], "cancelling")
            self.assertTrue(job["cancel_requested"])

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

    def test_semantic_expected_excludes_whitespace_only_source_files(self):
        module = load_indexing_module()

        with (
            mock.patch("os.path.getsize", return_value=1),
            mock.patch("builtins.open", mock.mock_open(read_data="\n")),
        ):
            self.assertFalse(
                module._is_semantic_expected_path(
                    "crates/example/src/lib.rs",
                    "/tmp/repo/crates/example/src/lib.rs",
                    "rs",
                )
            )

    def test_semantic_expected_excludes_xcassets_resource_metadata(self):
        module = load_indexing_module()
        fake_ts_pack = types.SimpleNamespace(
            should_use_line_window_fallback=lambda _path: False,
            detect_language_from_extension=lambda ext: (
                "json" if ext == "json" else None
            ),
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

        with (
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.getmtime", return_value=1000.0),
        ):
            output = run_indexing_health(module, "/tmp/repo")

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

        with (
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.getmtime", return_value=1000.0),
        ):
            output = run_indexing_health(module, "/tmp/repo")

        self.assertIn("**Run Alignment**:        ⚠️ Not aligned", output)
        self.assertIn("Structural and semantic runs are not aligned.", output)

    def test_health_reports_stale_shadow_residue(self):
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
            if op == "get_shadow_graph_node_health":
                return [{"nodes": 12, "projects": 2}]
            if op == "get_shadow_graph_rel_health":
                return [{"rels": 3, "rel_projects": 1}]
            return []

        with (
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.getmtime", return_value=1000.0),
        ):
            output = run_indexing_health(module, "/tmp/repo")

        self.assertIn("## 1.6 Global Shadow Graph Residue", output)
        self.assertIn("Shadow project IDs with nodes: 2", output)
        self.assertIn("Shadow nodes: 12", output)
        self.assertIn("Shadow relationships: 3", output)
        self.assertIn("cleanup_stale_shadow_graph(dry_run=False)", output)

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

        with (
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.getmtime", return_value=1000.0),
        ):
            output = run_indexing_health(module, "/tmp/repo")

        self.assertIn(
            "**Parse Success Rate**: 100.0% (1/1 source-eligible files)", output
        )
        self.assertIn(
            "Support-file coverage: 33.3% (1/3 across all manifest-kept files)", output
        )

    def test_audit_explains_isolated_file_impact(self):
        module = load_indexing_module()
        fake_memory = FakePgMemoryStore(
            {
                "select file_path, bool_or(coalesce((metadata->>'semantic_contract_version')::int, 0) = %s) as current_contract from codebase_embeddings": [
                    ("src/app.py", True),
                    ("src/orphan.py", True),
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
                        "ts": 2_000_000,
                        "vts": 2_000_000,
                        "parsed": True,
                    },
                    {
                        "fp": "src/orphan.py",
                        "ts": 2_000_000,
                        "vts": 2_000_000,
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
            if op == "audit_import_materialization":
                return [{"facts": 2, "file_edges": 1, "symbol_edges": 2}]
            if op == "audit_symbol_density":
                return []
            if op == "audit_isolation":
                return [{"fp": "src/orphan.py"}]
            return []

        manifest = [
            {"rel_path": "src/app.py", "abs_path": "/tmp/repo/src/app.py"},
            {"rel_path": "src/orphan.py", "abs_path": "/tmp/repo/src/orphan.py"},
        ]

        with (
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
            mock.patch.object(
                module,
                "get_memory_modules",
                return_value=(fake_memory, None, None, None, None),
            ),
            mock.patch.object(module, "build_manifest", return_value=manifest),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.getmtime", return_value=1000.0),
            mock.patch("os.path.getsize", return_value=1),
        ):
            output = run_indexing_health(module, "/tmp/repo", audit=True)

        self.assertIn("**Isolated Source Files**: 1 detected", output)
        self.assertIn(
            "**Import Graph Materialization**: facts=2, file_edges=1, symbol_edges=2",
            output,
        )
        self.assertIn("isolated files have no structural links", output)
        self.assertIn("related-file, call-chain, and blast-radius tools may miss", output)
        self.assertIn(
            "Inspect isolated files for missing import/call extraction", output
        )

    def test_audit_reports_disconnected_current_import_graph_without_rebuild_advice(self):
        module = load_indexing_module()

        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "get_indexing_health_files":
                return [{"fp": "src/app.py", "ts": 2_000_000, "vts": None, "parsed": True}]
            if op == "get_indexing_health_runs":
                return []
            if op == "audit_import_materialization":
                return [{"facts": 4, "file_edges": 0, "symbol_edges": 0}]
            if op in {"audit_symbol_density", "audit_isolation"}:
                return []
            return []

        with (
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.getmtime", return_value=1000.0),
        ):
            output = run_indexing_health(module, "/tmp/repo", audit=True)

        self.assertIn("facts=4, file_edges=0, symbol_edges=0", output)
        self.assertIn("no internal import links were materialized", output)
        self.assertIn("Investigate language-specific import resolution", output)
        self.assertNotIn("mode='rebuild'", output)

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

        with (
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
            mock.patch.object(
                module,
                "get_memory_modules",
                return_value=(fake_memory, None, None, None, None),
            ),
            mock.patch.object(module, "build_manifest", return_value=manifest),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.getmtime", return_value=1000.0),
            mock.patch("os.path.getsize", return_value=1),
        ):
            output = run_indexing_health(module, "/tmp/repo")

        self.assertIn("Files in structural index: 2", output)
        self.assertIn("Files in semantic index:   2", output)
        self.assertIn("**Sync Status**: ✅ Healthy", output)
        self.assertIn("**Run Alignment**:        ✅ Aligned", output)
        self.assertIn("No actions required. Everything looks healthy!", output)

    def test_health_treats_verified_coverage_as_aligned_even_when_semantic_struct_run_lags(
        self,
    ):
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

        with (
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
            mock.patch.object(
                module,
                "get_memory_modules",
                return_value=(fake_memory, None, None, None, None),
            ),
            mock.patch.object(module, "build_manifest", return_value=manifest),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.getmtime", return_value=1000.0),
            mock.patch("os.path.getsize", return_value=1),
        ):
            output = run_indexing_health(module, "/tmp/repo")

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

        with (
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
            mock.patch.object(module, "build_manifest", return_value=manifest),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.getmtime", return_value=1000.0),
        ):
            output = run_indexing_health(module, "/tmp/repo")

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
            {
                "rel_path": "FrameCreator.xcodeproj/project.pbxproj",
                "abs_path": "/tmp/repo/FrameCreator.xcodeproj/project.pbxproj",
            },
            {
                "rel_path": "FrameCreator.xcodeproj/xcshareddata/xcschemes/FrameCreator.xcscheme",
                "abs_path": "/tmp/repo/FrameCreator.xcodeproj/xcshareddata/xcschemes/FrameCreator.xcscheme",
            },
        ]

        with (
            mock.patch.object(module, "_execute_read", side_effect=fake_execute_read),
            mock.patch.object(module, "build_manifest", return_value=manifest),
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.getmtime", return_value=1000.0),
        ):
            output = run_indexing_health(module, "/tmp/repo")

        self.assertIn("## 1.75 Apple Build Coverage", output)
        self.assertIn("**Apple Graph Status**: ⚠️ Partial", output)
        self.assertIn(
            "scheme files exist, but no XcodeScheme nodes were materialized", output
        )
        self.assertIn(
            "workspace metadata exists, but no XcodeWorkspace nodes were materialized",
            output,
        )
        self.assertIn("Apple build metadata is only partially materialized.", output)


if __name__ == "__main__":
    unittest.main()

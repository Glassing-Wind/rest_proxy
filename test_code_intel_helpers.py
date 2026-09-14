import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
FILE_DESCRIBE_PATH = REPO_ROOT / "tools" / "brain" / "code_intel" / "file_describe.py"
REFERENCES_PATH = REPO_ROOT / "tools" / "brain" / "code_intel" / "references.py"
SYMBOL_GRAPH_PATH = REPO_ROOT / "tools" / "brain" / "code_intel" / "symbol_graph.py"


def load_file_describe_module():
    spec = importlib.util.spec_from_file_location("file_describe_under_test", FILE_DESCRIBE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (None, None, None, None, None)
    ts_diag = types.ModuleType("ts_diagnostics")
    ts_diag.normalize_ts_pack_result = lambda code, lang, result: result

    with mock.patch.dict(sys.modules, {"_helpers": helpers_mod, "ts_diagnostics": ts_diag}):
        spec.loader.exec_module(module)
    return module


def load_references_module():
    spec = importlib.util.spec_from_file_location("references_under_test", REFERENCES_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: workspace_id.rstrip("/").split("/")[-1]
    helpers_mod.get_workspace_path = lambda workspace_id: workspace_id
    helpers_mod.get_memory_modules = lambda: (None, None, None, None, None)
    neo4j_mod = types.ModuleType("neo4j")

    def unit_of_work(timeout=None, metadata=None):
        def decorator(fn):
            return fn
        return decorator

    neo4j_mod.unit_of_work = unit_of_work

    with mock.patch.dict(sys.modules, {"_helpers": helpers_mod, "neo4j": neo4j_mod}):
        spec.loader.exec_module(module)
    return module


def load_symbol_graph_module():
    spec = importlib.util.spec_from_file_location("symbol_graph_under_test", SYMBOL_GRAPH_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CodeIntelHelperTests(unittest.TestCase):
    def test_resolve_describe_paths(self):
        module = load_file_describe_module()
        abs_path, display = module.resolve_describe_paths("", "/tmp/file.swift")
        self.assertEqual(abs_path, "/tmp/file.swift")
        self.assertEqual(display, "/tmp/file.swift")

    def test_format_ts_pack_symbols(self):
        module = load_file_describe_module()
        symbols, label = module.format_ts_pack_symbols(
            {
                "_language": "swift",
                "metrics": {"error_count": 1},
                "structure": [
                    {
                        "name": "AppView",
                        "kind": "struct",
                        "span": {"start_line": 0, "end_line": 9},
                        "children": [],
                    }
                ],
            }
        )
        self.assertIn("[swift]", label)
        self.assertTrue(symbols)

    def test_find_references_formats_graph_and_semantic_sections(self):
        module = load_references_module()

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            async def data(self):
                return self.rows

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute_read(self, fn):
                return await fn(self)

            async def run(self, cypher, **params):
                if "CALLS_EXTERNAL_SYMBOL" in cypher:
                    return FakeResult(
                        [
                            {
                                "fp": "src/ext.py",
                                "sl": 30,
                                "cn": "use_external",
                                "qn": "json.Unmarshal",
                                "language": "go",
                                "tpid": "repo",
                            }
                        ]
                    )
                return FakeResult([{"fp": "src/a.py", "sl": 12, "cn": "caller", "tpid": "repo"}])

        class FakeDriver:
            def session(self, database=None):
                return FakeSession()

        class FakeCursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, query, params):
                return None

            def __aiter__(self):
                async def gen():
                    yield ("src/b.py", "21", "repo", None, "symbol_name()")
                return gen()

        class FakeConnection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return FakeCursor()

        class FakePool:
            def connection(self):
                return FakeConnection()

        class FakeMemoryStore:
            _pg_pool = FakePool()

            @staticmethod
            async def open_pool():
                return None

        graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        graph_bootstrap_mod.require_driver = _require_driver
        graph_bootstrap_mod._NEO4J_DB = "neo4j"

        with mock.patch.object(module, "get_memory_modules", return_value=(FakeMemoryStore, None, None, None, None)):
            with mock.patch.dict(sys.modules, {"graph_bootstrap": graph_bootstrap_mod}):
                output = asyncio.run(module.find_references_impl(["/tmp/repo"], "symbol_name"))

        self.assertIn("Functional References (Graph)", output)
        self.assertIn("External Symbol Callers (Graph)", output)
        self.assertIn("Mentions & Type Usages (Semantic)", output)
        self.assertIn("- src/b.py (1 semantic mention)", output)
        self.assertIn("  - src/b.py:21 (semantic)", output)

    def test_find_references_groups_multiple_semantic_hits_by_file(self):
        module = load_references_module()

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            async def data(self):
                return self.rows

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute_read(self, fn):
                return await fn(self)

            async def run(self, cypher, **params):
                return FakeResult([])

        class FakeDriver:
            def session(self, database=None):
                return FakeSession()

        class FakeCursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, query, params):
                return None

            def __aiter__(self):
                async def gen():
                    yield ("src/b.py", "21", "repo", [], "symbol_name()")
                    yield ("src/b.py", "33", "repo", [], "return symbol_name")
                    yield ("src/c.py", "9", "repo", [], "symbol_name")
                return gen()

        class FakeConnection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return FakeCursor()

        class FakePool:
            def connection(self):
                return FakeConnection()

        class FakeMemoryStore:
            _pg_pool = FakePool()

            @staticmethod
            async def open_pool():
                return None

        graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        graph_bootstrap_mod.require_driver = _require_driver
        graph_bootstrap_mod._NEO4J_DB = "neo4j"

        with mock.patch.object(module, "get_memory_modules", return_value=(FakeMemoryStore, None, None, None, None)):
            with mock.patch.dict(sys.modules, {"graph_bootstrap": graph_bootstrap_mod}):
                output = asyncio.run(module.find_references_impl(["/tmp/repo"], "symbol_name"))

        self.assertIn("- src/b.py (2 semantic mentions)", output)
        self.assertIn("  - src/b.py:21 (semantic)", output)
        self.assertIn("  - src/b.py:33 (semantic)", output)
        self.assertIn("- src/c.py (1 semantic mention)", output)

    def test_find_references_filters_low_signal_semantic_paths(self):
        module = load_references_module()

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            async def data(self):
                return self.rows

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute_read(self, fn):
                return await fn(self)

            async def run(self, cypher, **params):
                return FakeResult([])

        class FakeDriver:
            def session(self, database=None):
                return FakeSession()

        class FakeCursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, query, params):
                return None

            def __aiter__(self):
                async def gen():
                    yield ("session-ses_deadbeef.md", "1", "repo", None, "SidebarView")
                    yield ("FrameCreator/Views/ContentView.swift", "12", "repo", None, "SidebarView()")
                return gen()

        class FakeConnection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return FakeCursor()

        class FakePool:
            def connection(self):
                return FakeConnection()

        class FakeMemoryStore:
            _pg_pool = FakePool()

            @staticmethod
            async def open_pool():
                return None

        graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        graph_bootstrap_mod.require_driver = _require_driver
        graph_bootstrap_mod._NEO4J_DB = "neo4j"

        with mock.patch.object(module, "get_memory_modules", return_value=(FakeMemoryStore, None, None, None, None)):
            with mock.patch.dict(sys.modules, {"graph_bootstrap": graph_bootstrap_mod}):
                output = asyncio.run(module.find_references_impl(["/tmp/repo"], "SidebarView"))

        self.assertIn("FrameCreator/Views/ContentView.swift", output)
        self.assertNotIn("session-ses_deadbeef.md", output)

    def test_find_references_keeps_test_like_semantic_path_when_file_roles_are_present_empty(self):
        module = load_references_module()

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            async def data(self):
                return self.rows

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute_read(self, fn):
                return await fn(self)

            async def run(self, cypher, **params):
                return FakeResult([])

        class FakeDriver:
            def session(self, database=None):
                return FakeSession()

        class FakeCursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, query, params):
                return None

            def __aiter__(self):
                async def gen():
                    yield (
                        "tests/providers/test_provider_names.py",
                        "8",
                        "repo",
                        [],
                        "SidebarView",
                    )
                return gen()

        class FakeConnection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return FakeCursor()

        class FakePool:
            def connection(self):
                return FakeConnection()

        class FakeMemoryStore:
            _pg_pool = FakePool()

            @staticmethod
            async def open_pool():
                return None

        graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        graph_bootstrap_mod.require_driver = _require_driver
        graph_bootstrap_mod._NEO4J_DB = "neo4j"

        with mock.patch.object(module, "get_memory_modules", return_value=(FakeMemoryStore, None, None, None, None)):
            with mock.patch.dict(sys.modules, {"graph_bootstrap": graph_bootstrap_mod}):
                output = asyncio.run(module.find_references_impl(["/tmp/repo"], "SidebarView"))

        self.assertIn("tests/providers/test_provider_names.py", output)

    def test_find_references_legacy_test_like_semantic_path_still_surfaces_in_semantic_section_when_roles_missing(self):
        module = load_references_module()

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            async def data(self):
                return self.rows

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute_read(self, fn):
                return await fn(self)

            async def run(self, cypher, **params):
                return FakeResult([])

        class FakeDriver:
            def session(self, database=None):
                return FakeSession()

        class FakeCursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, query, params):
                return None

            def __aiter__(self):
                async def gen():
                    yield (
                        "tests/providers/test_provider_names.py",
                        "8",
                        "repo",
                        None,
                        "SidebarView",
                    )
                return gen()

        class FakeConnection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return FakeCursor()

        class FakePool:
            def connection(self):
                return FakeConnection()

        class FakeMemoryStore:
            _pg_pool = FakePool()

            @staticmethod
            async def open_pool():
                return None

        graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        graph_bootstrap_mod.require_driver = _require_driver
        graph_bootstrap_mod._NEO4J_DB = "neo4j"

        with mock.patch.object(module, "get_memory_modules", return_value=(FakeMemoryStore, None, None, None, None)):
            with mock.patch.dict(sys.modules, {"graph_bootstrap": graph_bootstrap_mod}):
                output = asyncio.run(module.find_references_impl(["/tmp/repo"], "SidebarView"))

        self.assertIn("Mentions & Type Usages (Semantic)", output)
        self.assertIn("tests/providers/test_provider_names.py", output)

    def test_reference_path_penalty_treats_legacy_example_paths_as_low_signal(self):
        module = load_references_module()
        low_signal_bucket, _ = module._reference_path_penalty(
            "examples/provider_walkthrough.py",
            None,
        )
        normal_bucket, _ = module._reference_path_penalty(
            "src/providers/provider.py",
            None,
        )
        self.assertGreater(low_signal_bucket, normal_bucket)

    def test_reference_path_penalty_treats_legacy_benchmark_paths_as_low_signal(self):
        module = load_references_module()
        low_signal_bucket, _ = module._reference_path_penalty(
            "benchmarks/provider_walkthrough.py",
            None,
        )
        normal_bucket, _ = module._reference_path_penalty(
            "src/providers/provider.py",
            None,
        )
        self.assertGreater(low_signal_bucket, normal_bucket)

    def test_reference_path_penalty_prefers_implementation_over_support_role(self):
        module = load_references_module()
        bucket, _ = module._reference_path_penalty(
            "tools/docs/provider.py",
            ["implementation_surface", "support_surface"],
        )
        support_bucket, _ = module._reference_path_penalty(
            "tools/docs/provider.py",
            ["support_surface"],
        )
        self.assertEqual(bucket, 1)
        self.assertLess(bucket, support_bucket)

    def test_format_symbol_context_includes_external_calls(self):
        module = load_symbol_graph_module()
        output = module.format_symbol_context(
            {
                "kind": "Function",
                "filepath": "src/lib.rs",
                "start_line": 10,
                "end_line": 30,
                "signature": "fn do_work()",
                "callers": [],
                "callees": [{"name": "helper", "file": "src/helper.rs"}],
                "external_callees": [
                    {
                        "name": "Unmarshal",
                        "qualified_name": "json.Unmarshal",
                        "language": "go",
                    }
                ],
            },
            "do_work",
        )

        rendered = "\n".join(output)
        self.assertIn("**External Calls** (1):", rendered)
        self.assertIn("`json.Unmarshal` [go]", rendered)

    def test_pick_symbol_context_candidate_prefers_library_entrypoint(self):
        module = load_symbol_graph_module()
        picked = module.pick_symbol_context_candidate(
            [
                {
                    "kind": "Function",
                    "name": "process",
                    "qualified_name": "process",
                    "filepath": "crates/ts-pack-cli/src/main.rs",
                    "start_line": 10,
                    "signature": "fn process()",
                    "callers_in": 0,
                    "callees_out": 4,
                },
                {
                    "kind": "Function",
                    "name": "process",
                    "qualified_name": "process",
                    "filepath": "crates/ts-pack-core/src/lib.rs",
                    "start_line": 200,
                    "signature": "pub fn process(source: &str, config: &ProcessConfig)",
                    "callers_in": 3,
                    "callees_out": 2,
                },
            ],
            symbol_name="process",
            normalized_file_path=None,
            normalized_signature=None,
        )
        self.assertEqual(picked["filepath"], "crates/ts-pack-core/src/lib.rs")

    def test_pick_symbol_context_candidate_prefers_exact_file_match_even_for_main(self):
        module = load_symbol_graph_module()
        picked = module.pick_symbol_context_candidate(
            [
                {
                    "kind": "Function",
                    "name": "main",
                    "qualified_name": "build.main",
                    "filepath": "packages/desktop/src-tauri/build.rs",
                    "start_line": 8,
                    "signature": "fn main()",
                    "callers_in": 0,
                    "callees_out": 2,
                },
                {
                    "kind": "Function",
                    "name": "main",
                    "qualified_name": "runtime.main",
                    "filepath": "/tmp/opencode/packages/desktop/src-tauri/src/main.rs",
                    "start_line": 14,
                    "signature": "fn main()",
                    "callers_in": 1,
                    "callees_out": 6,
                },
            ],
            symbol_name="main",
            normalized_file_path="packages/desktop/src-tauri/src/main.rs",
            normalized_signature=None,
        )
        self.assertEqual(picked["filepath"], "/tmp/opencode/packages/desktop/src-tauri/src/main.rs")

    def test_symbol_path_penalty_skips_test_path_fallback_when_file_roles_are_present(self):
        module = load_symbol_graph_module()
        self.assertLess(
            module._symbol_path_penalty("packages/sdk/js/src/tests/config.test.ts", []),
            module._symbol_path_penalty("packages/sdk/js/src/tests/config.test.ts", None),
        )
        self.assertEqual(
            module._symbol_path_penalty(
                "packages/sdk/js/src/tests/config.test.ts",
                ["test_surface"],
            ),
            4,
        )

    def test_symbol_path_penalty_legacy_docs_fallback_still_applies_when_roles_missing(self):
        module = load_symbol_graph_module()
        self.assertEqual(
            module._symbol_path_penalty("packages/sdk/js/docs/runtime.md", None),
            3,
        )

    def test_symbol_path_penalty_legacy_benchmark_fallback_still_applies_when_roles_missing(self):
        module = load_symbol_graph_module()
        self.assertEqual(
            module._symbol_path_penalty("packages/sdk/js/benchmarks/provider_walkthrough.ts", None),
            4,
        )

    def test_symbol_path_penalty_prefers_implementation_over_support_role(self):
        module = load_symbol_graph_module()
        self.assertEqual(
            module._symbol_path_penalty(
                "tools/docs/provider.py",
                ["implementation_surface", "support_surface"],
            ),
            0,
        )

    def test_symbol_context_callee_rank_skips_test_path_fallback_when_file_roles_are_present(self):
        module = load_symbol_graph_module()
        target = "packages/sdk/js/src/runtime/main.ts"
        test_like_path = "packages/sdk/js/src/runtime/tests/config.test.ts"
        self.assertLess(
            module._symbol_context_callee_rank(
                {"file": test_like_path, "name": "buildConfig", "file_roles": []},
                target_filepath=target,
            )[0],
            module._symbol_context_callee_rank(
                {"file": test_like_path, "name": "buildConfig", "file_roles": None},
                target_filepath=target,
            )[0],
        )

    def test_symbol_context_callee_rank_keeps_generated_same_dir_penalty(self):
        module = load_symbol_graph_module()
        target = "packages/sdk/js/src/runtime/main.ts"
        generated_path = "packages/sdk/js/src/runtime/generated/config.pb.swift"
        self.assertGreaterEqual(
            module._symbol_context_callee_rank(
                {"file": generated_path, "name": "Config", "file_roles": None},
                target_filepath=target,
            )[0],
            4,
        )

    def test_symbol_context_cypher_parenthesizes_label_filter(self):
        module = load_symbol_graph_module()
        cypher = module.SYMBOL_CONTEXT_CYPHER
        self.assertIn("WHERE (s:Function OR s:Class", cypher)
        self.assertIn(")\\n      AND s.project_id = $pid".replace("\\n", "\n"), cypher)

    def test_should_disambiguate_symbol_context_keeps_generic_close_scores_ambiguous(self):
        module = load_symbol_graph_module()
        candidates = [
            {
                "kind": "TypeAlias",
                "name": "Config",
                "qualified_name": None,
                "filepath": "packages/plugin/src/index.ts",
                "start_line": 10,
                "signature": None,
                "callers_in": 1,
                "callees_out": 0,
            },
            {
                "kind": "TypeAlias",
                "name": "Config",
                "qualified_name": None,
                "filepath": "packages/opencode/src/control-plane/adaptors/worktree.ts",
                "start_line": 12,
                "signature": None,
                "callers_in": 2,
                "callees_out": 0,
            },
            {
                "kind": "Class",
                "name": "Config",
                "qualified_name": None,
                "filepath": "packages/sdk/js/src/gen/sdk.gen.ts",
                "start_line": 30,
                "signature": "class Config",
                "callers_in": 0,
                "callees_out": 0,
            },
        ]
        self.assertTrue(
            module.should_disambiguate_symbol_context(
                candidates,
                symbol_name="Config",
                normalized_file_path=None,
                normalized_signature=None,
            )
        )

    def test_should_disambiguate_symbol_context_skips_test_path_fallback_when_roles_are_present(self):
        module = load_symbol_graph_module()
        present_role_candidates = [
            {
                "kind": "TypeAlias",
                "name": "Config",
                "qualified_name": None,
                "filepath": "packages/sdk/js/src/runtime/config.ts",
                "start_line": 10,
                "signature": None,
                "callers_in": 1,
                "callees_out": 0,
                "file_roles": [],
            },
            {
                "kind": "TypeAlias",
                "name": "Config",
                "qualified_name": None,
                "filepath": "packages/sdk/js/src/runtime/tests/config.test.ts",
                "start_line": 12,
                "signature": None,
                "callers_in": 1,
                "callees_out": 0,
                "file_roles": [],
            },
            {
                "kind": "Class",
                "name": "Config",
                "qualified_name": None,
                "filepath": "packages/sdk/js/src/gen/sdk.gen.ts",
                "start_line": 30,
                "signature": "class Config",
                "callers_in": 0,
                "callees_out": 0,
                "file_roles": ["generated_surface"],
            },
        ]
        legacy_candidates = [dict(candidate) for candidate in present_role_candidates]
        legacy_candidates[1]["file_roles"] = None
        self.assertTrue(
            module.should_disambiguate_symbol_context(
                present_role_candidates,
                symbol_name="Config",
                normalized_file_path=None,
                normalized_signature=None,
            )
        )
        self.assertFalse(
            module.should_disambiguate_symbol_context(
                legacy_candidates,
                symbol_name="Config",
                normalized_file_path=None,
                normalized_signature=None,
            )
        )

    def test_call_chain_candidate_uses_suffix_file_match(self):
        module = load_symbol_graph_module()
        picked = module.pick_call_chain_candidate(
            [
                {
                    "eid": "1",
                    "name": "main",
                    "qualified_name": "main",
                    "signature": "fn main()",
                    "filepath": "/tmp/opencode/packages/desktop/src-tauri/build.rs",
                    "rank": 0,
                    "file_roles": [],
                    "callers_in": 0,
                },
                {
                    "eid": "2",
                    "name": "main",
                    "qualified_name": "main",
                    "signature": "fn main()",
                    "filepath": "/tmp/opencode/packages/desktop/src-tauri/src/main.rs",
                    "rank": 0,
                    "file_roles": [],
                    "callers_in": 3,
                },
            ],
            normalized_file_path="packages/desktop/src-tauri/src/main.rs",
            normalized_signature=None,
        )
        self.assertEqual(picked["filepath"], "/tmp/opencode/packages/desktop/src-tauri/src/main.rs")

    def test_call_chain_resolve_cypher_returns_semantic_file_roles(self):
        module = load_symbol_graph_module()
        cypher = module.CALL_CHAIN_RESOLVE_CYPHER
        self.assertIn("parent.semantic_file_roles AS file_roles", cypher)
        self.assertNotIn("END AS path_rank", cypher)
        self.assertNotIn("CONTAINS '/tests/'", cypher)
        self.assertNotIn("CONTAINS '/generated/'", cypher)

    def test_visualize_focus_cypher_returns_semantic_file_roles(self):
        module = load_symbol_graph_module()
        cypher = module.VISUALIZE_SUBGRAPH_FOCUS_CYPHER
        self.assertIn("coalesce(n.semantic_file_roles, parent.semantic_file_roles) AS file_roles", cypher)

    def test_call_chain_path_cypher_returns_per_hop_semantic_file_roles(self):
        module = load_symbol_graph_module()
        _, cypher = module.build_call_chain_path_cypher("down", 3, is_backend_root=True)
        self.assertIn("AS file_roles", cypher)
        self.assertNotIn("CONTAINS '/tests/'", cypher)
        self.assertNotIn("CONTAINS '/generated/'", cypher)

    def test_swift_protocol_call_chain_fallback_returns_per_hop_semantic_file_roles(self):
        module = load_symbol_graph_module()
        cypher = module.build_swift_protocol_upward_fallback_cypher(2)
        self.assertIn("AS file_roles", cypher)
        self.assertNotIn("CONTAINS '/tests/'", cypher)
        self.assertNotIn("CONTAINS '/generated/'", cypher)

    def test_format_call_chain_rows_prefers_same_source_subtree_for_explicit_main(self):
        module = load_symbol_graph_module()
        output = module.format_call_chain_rows(
            [
                {
                    "chain": ["main", "configure_display_backend"],
                    "files": [
                        "packages/desktop/src-tauri/src/main.rs",
                        "packages/desktop/src-tauri/src/main.rs",
                    ],
                    "lines": [42, 18],
                },
                {
                    "chain": ["main", "run"],
                    "files": [
                        "packages/desktop/src-tauri/src/main.rs",
                        "packages/desktop/src-tauri/src/lib.rs",
                    ],
                    "lines": [42, 12],
                },
                {
                    "chain": ["main", "run"],
                    "files": [
                        "packages/desktop/src-tauri/src/main.rs",
                        "packages/opencode/src/git/index.ts",
                    ],
                    "lines": [42, 88],
                },
            ],
            resolved_name="main",
            symbol_name="main",
            direction="down",
            depth=1,
            resolved_filepath="packages/desktop/src-tauri/src/main.rs",
        )
        self.assertIn("packages/desktop/src-tauri/src/main.rs", output)
        self.assertIn("packages/desktop/src-tauri/src/lib.rs", output)
        self.assertNotIn("packages/opencode/src/git/index.ts", output)

    def test_format_call_chain_rows_filters_backend_public_assets_after_query(self):
        module = load_symbol_graph_module()
        output = module.format_call_chain_rows(
            [
                {
                    "chain": ["buildRouter", "leaseRouter"],
                    "files": ["src/api/routes/buildRouter.ts", "src/api/routes/leaseRoutes.ts"],
                    "lines": [12, 30],
                    "file_roles": [[], []],
                },
                {
                    "chain": ["buildRouter", "renderPublic"],
                    "files": ["src/api/routes/buildRouter.ts", "src/public/assets/application-center.js"],
                    "lines": [12, 44],
                    "file_roles": [[], []],
                },
            ],
            resolved_name="buildRouter",
            symbol_name="buildRouter",
            direction="down",
            depth=2,
            resolved_filepath="src/api/routes/buildRouter.ts",
        )
        self.assertIn("leaseRouter", output)
        self.assertNotIn("renderPublic", output)

    def test_format_call_chain_rows_prefers_test_like_first_hop_when_roles_are_present_empty(self):
        module = load_symbol_graph_module()
        output = module.format_call_chain_rows(
            [
                {
                    "chain": ["main", "testHelper"],
                    "files": ["src/runtime/main.ts", "src/tests/helpers.spec.ts"],
                    "lines": [12, 30],
                    "file_roles": [[], []],
                },
                {
                    "chain": ["main", "uiHelper"],
                    "files": ["src/runtime/main.ts", "packages/ui/button.ts"],
                    "lines": [12, 31],
                    "file_roles": [[], []],
                }
            ],
            resolved_name="main",
            symbol_name="main",
            direction="down",
            depth=1,
            resolved_filepath="src/runtime/main.ts",
        )
        self.assertIn("testHelper", output)
        self.assertNotIn("uiHelper", output)

    def test_format_call_chain_rows_demotes_test_like_first_hop_when_roles_missing(self):
        module = load_symbol_graph_module()
        output = module.format_call_chain_rows(
            [
                {
                    "chain": ["main", "testHelper"],
                    "files": ["src/runtime/main.ts", "src/tests/helpers.spec.ts"],
                    "lines": [12, 30],
                    "file_roles": [None, None],
                },
                {
                    "chain": ["main", "uiHelper"],
                    "files": ["src/runtime/main.ts", "packages/ui/button.ts"],
                    "lines": [12, 31],
                    "file_roles": [None, None],
                }
            ],
            resolved_name="main",
            symbol_name="main",
            direction="down",
            depth=1,
            resolved_filepath="src/runtime/main.ts",
        )
        self.assertIn("uiHelper", output)
        self.assertNotIn("testHelper", output)

    def test_python_exact_call_graph_guidance_appears_when_edges_are_sparse(self):
        module = load_symbol_graph_module()
        output = module.format_symbol_context(
            {
                "kind": "Function",
                "filepath": "pkg/parser.py",
                "start_line": 10,
                "end_line": 30,
                "signature": "def parse_tree(source):",
                "callers": [],
                "callees": [],
                "external_callees": [],
            },
            "parse_tree",
        )
        rendered = "\n".join(output)
        self.assertIn("Exactness note", rendered)
        self.assertIn("find_references", rendered)

    def test_python_exact_call_graph_guidance_stays_off_when_one_side_exists(self):
        module = load_symbol_graph_module()
        self.assertIsNone(
            module.exact_call_graph_guidance(
                "pkg/parser.py",
                has_callers=True,
                has_callees=False,
            )
        )


if __name__ == "__main__":
    unittest.main()

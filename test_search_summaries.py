import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "summaries.py"


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_module():
    spec = importlib.util.spec_from_file_location("search_summaries_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda project_path: "proj123"
    graph_core_mod = types.ModuleType("tools.brain.graph.core")

    async def _execute_read(*args, **kwargs):
        return []

    graph_core_mod._execute_read = _execute_read

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
        },
    ):
        spec.loader.exec_module(module)
    return module


class SearchSummaryTests(unittest.TestCase):
    def test_symbol_imports_overview_explains_missing_edge_recovery(self):
        with mock.patch.object(
            self.module.graph_tools, "_execute_read", return_value=[]
        ):
            output = asyncio.run(
                self.module.get_symbol_imports_overview_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                )
            )

        self.assertIn("IMPORTS_SYMBOL", output)
        self.assertIn("get_indexing_health", output)
        self.assertIn("get_related_files", output)

    def setUp(self):
        self.module = load_module()

    def test_symbol_exports_summary_applies_filters(self):
        async def fake_execute_read(session, query, **kwargs):
            if kwargs.get("op") == "get_symbol_exports_summary_filtered_rows":
                return [
                    {
                        "file": "src/app.py",
                        "symbol": "AppFoo",
                        "target_symbol": "AppFoo",
                        "alias_edges": 0,
                        "importers": 3,
                    },
                    {
                        "file": "src/app.py",
                        "symbol": "Other",
                        "target_symbol": "Other",
                        "alias_edges": 0,
                        "importers": 2,
                    },
                    {
                        "file": "tests/test_app.py",
                        "symbol": "AppFoo",
                        "target_symbol": "AppFoo",
                        "alias_edges": 0,
                        "importers": 1,
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_paths=["src/*"],
                    exclude_paths=["tests/*"],
                    symbol_prefix="App",
                )
            )
        self.assertIn("AppFoo", output)
        self.assertIn("src/app.py", output)
        self.assertNotIn("tests/test_app.py", output)

    def test_symbol_imports_overview_limits_implicit_sources_to_swift_files(self):
        captured_queries = []

        async def fake_execute_read(session, query, **kwargs):
            captured_queries.append((kwargs.get("op"), query))
            op = kwargs.get("op")
            if op == "get_symbol_imports_overview_exp_count":
                return [{"n": 1}]
            if op == "get_symbol_imports_overview_imp_count":
                return [{"n": 2}]
            if op == "get_symbol_imports_overview_exp_symbols":
                return [{"symbol": "Foo", "n": 1}]
            if op == "get_symbol_imports_overview_exp_files":
                return [{"file": "src/a.py", "n": 1, "symbols": ["Foo"]}]
            if op == "get_symbol_imports_overview_imp_symbols":
                return [{"symbol": "Bar", "n": 2}]
            if op == "get_symbol_imports_overview_imp_files":
                return [{"file": "Sources/App/View.swift", "n": 2, "symbols": ["Bar"]}]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_imports_overview_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_implicit=True,
                )
            )

        self.assertIn("Sources/App/View.swift", output)
        self.assertIn("Use this to decide which files pull the widest symbol surface", output)
        self.assertIn("## Inspect First", output)
        implicit_queries = {
            op: query
            for op, query in captured_queries
            if op in {
                "get_symbol_imports_overview_imp_symbols",
                "get_symbol_imports_overview_imp_files",
            }
        }
        self.assertIn("WHERE f.filepath ENDS WITH '.swift'", implicit_queries["get_symbol_imports_overview_imp_symbols"])
        self.assertIn("WHERE f.filepath ENDS WITH '.swift'", implicit_queries["get_symbol_imports_overview_imp_files"])

    def test_symbol_imports_overview_filters_generic_top_symbols_and_samples(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_imports_overview_exp_count":
                return [{"n": 3}]
            if op == "get_symbol_imports_overview_exp_symbols":
                return [
                    {"symbol": "Result", "n": 8},
                    {"symbol": "create", "n": 7},
                    {"symbol": "Language", "n": 7},
                    {"symbol": "CaptureOutput", "n": 6},
                ]
            if op == "get_symbol_imports_overview_exp_files":
                return [
                    {
                        "file": "src/lib.rs",
                        "n": 9,
                        "symbols": ["Result", "Language", "create", "CaptureOutput", "CommentKind"],
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_imports_overview_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_implicit=False,
                )
            )

        self.assertIn("CaptureOutput", output)
        self.assertNotIn("- Result  (8)", output)
        self.assertNotIn("- create  (7)", output)
        self.assertIn("CaptureOutput, CommentKind", output)

    def test_symbol_imports_overview_prefers_implementation_files_over_test_or_story_surfaces(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_imports_overview_exp_count":
                return [{"n": 6}]
            if op == "get_symbol_imports_overview_exp_symbols":
                return [
                    {"symbol": "tmpdir", "n": 12},
                    {"symbol": "DocumentSymbol", "n": 11},
                    {"symbol": "CommandChild", "n": 9},
                    {"symbol": "ServerReadyData", "n": 8},
                ]
            if op == "get_symbol_imports_overview_exp_files":
                return [
                    {
                        "file": "packages/opencode/test/lsp/index.test.ts",
                        "n": 22,
                        "symbols": ["DocumentSymbol", "tmpdir", "Status"],
                        "file_roles": None,
                    },
                    {
                        "file": "packages/ui/src/components/context-menu.stories.tsx",
                        "n": 19,
                        "symbols": ["ContextMenuContentProps", "ContextMenuGroupProps", "Icon"],
                        "file_roles": None,
                    },
                    {
                        "file": "packages/desktop-electron/src/main/index.ts",
                        "n": 26,
                        "symbols": ["CommandChild", "InitStep", "ServerReadyData", "WslConfig"],
                        "file_roles": None,
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_imports_overview_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_implicit=False,
                )
            )

        self.assertIn("packages/desktop-electron/src/main/index.ts", output)
        inspect_line_index = output.find("start with `packages/desktop-electron/src/main/index.ts`")
        self.assertNotEqual(inspect_line_index, -1)

    def test_symbol_imports_overview_skips_test_path_penalty_when_file_roles_are_present(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_imports_overview_exp_count":
                return [{"n": 2}]
            if op == "get_symbol_imports_overview_exp_symbols":
                return [{"symbol": "OwnerControllerFlow", "n": 8}]
            if op == "get_symbol_imports_overview_exp_files":
                return [
                    {
                        "file": "src/main/java/org/example/tests/OwnerControllerFlow.java",
                        "n": 12,
                        "symbols": ["OwnerControllerFlow", "OwnerController"],
                        "file_roles": [],
                    },
                    {
                        "file": "src/main/java/org/example/Pet.java",
                        "n": 10,
                        "symbols": ["Pet"],
                        "file_roles": [],
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_imports_overview_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_implicit=False,
                )
            )

        self.assertIn(
            "start with `src/main/java/org/example/tests/OwnerControllerFlow.java`",
            output,
        )

    def test_symbol_imports_overview_legacy_test_path_penalty_still_applies_when_roles_missing(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_imports_overview_exp_count":
                return [{"n": 2}]
            if op == "get_symbol_imports_overview_exp_symbols":
                return [{"symbol": "OwnerControllerFlow", "n": 8}]
            if op == "get_symbol_imports_overview_exp_files":
                return [
                    {
                        "file": "src/main/java/org/example/tests/OwnerControllerFlow.java",
                        "n": 12,
                        "symbols": ["OwnerControllerFlow", "OwnerController"],
                        "file_roles": None,
                    },
                    {
                        "file": "src/main/java/org/example/Pet.java",
                        "n": 10,
                        "symbols": ["Pet"],
                        "file_roles": [],
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_imports_overview_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_implicit=False,
                )
            )

        self.assertIn("start with `src/main/java/org/example/Pet.java`", output)

    def test_symbol_imports_overview_demotes_docs_and_component_leaf_files(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_imports_overview_exp_count":
                return [{"n": 6}]
            if op == "get_symbol_imports_overview_exp_symbols":
                return [
                    {"symbol": "bootstrap_schema", "n": 30},
                    {"symbol": "assemble_memory", "n": 28},
                    {"symbol": "get_embedding_service", "n": 27},
                ]
            if op == "get_symbol_imports_overview_exp_files":
                return [
                    {
                        "file": "tools/brain/docs/search.py",
                        "n": 84,
                        "symbols": ["get_embedding_service", "bootstrap_schema", "assemble_memory"],
                        "file_roles": ["docs_surface"],
                    },
                    {
                        "file": "scripts/index_workspace.py",
                        "n": 76,
                        "symbols": ["get_embedding_service", "bootstrap_schema", "assemble_memory"],
                        "file_roles": [],
                    },
                    {
                        "file": "packages/web/src/components/share/part.tsx",
                        "n": 33,
                        "symbols": ["formatCount", "formatDuration", "formatNumber"],
                        "file_roles": [],
                    },
                    {
                        "file": "packages/desktop-electron/src/main/index.ts",
                        "n": 26,
                        "symbols": ["CommandChild", "InitStep", "ServerReadyData"],
                        "file_roles": [],
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_imports_overview_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_implicit=False,
                )
            )

        self.assertIn("start with `packages/desktop-electron/src/main/index.ts`", output)

    def test_symbol_exports_summary_skips_test_path_penalty_when_file_roles_are_present(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 2}]
            if op == "get_symbol_exports_summary_symbols":
                return [{"symbol": "OwnerControllerFlow", "target_symbol": "OwnerControllerFlow", "alias_edges": 0, "exporters": 1, "importers": 8}]
            if op == "get_symbol_exports_summary_files":
                return [
                    {
                        "file": "src/main/java/org/example/tests/OwnerControllerFlow.java",
                        "n": 12,
                        "symbols": ["OwnerControllerFlow", "OwnerController"],
                        "file_roles": [],
                    },
                    {
                        "file": "src/main/java/org/example/Pet.java",
                        "n": 10,
                        "symbols": ["Pet"],
                        "file_roles": [],
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn(
            "inspect `src/main/java/org/example/tests/OwnerControllerFlow.java` next",
            output,
        )

    def test_symbol_exports_summary_legacy_test_path_penalty_still_applies_when_roles_missing(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 2}]
            if op == "get_symbol_exports_summary_symbols":
                return [{"symbol": "OwnerControllerFlow", "target_symbol": "OwnerControllerFlow", "alias_edges": 0, "exporters": 1, "importers": 8}]
            if op == "get_symbol_exports_summary_files":
                return [
                    {
                        "file": "src/main/java/org/example/tests/OwnerControllerFlow.java",
                        "n": 12,
                        "symbols": ["OwnerControllerFlow", "OwnerController"],
                        "file_roles": None,
                    },
                    {
                        "file": "src/main/java/org/example/Pet.java",
                        "n": 10,
                        "symbols": ["Pet"],
                        "file_roles": [],
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("inspect `src/main/java/org/example/Pet.java` next", output)

    def test_symbol_exports_summary_legacy_example_path_penalty_still_applies_when_roles_missing(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 2}]
            if op == "get_symbol_exports_summary_symbols":
                return [{"symbol": "OwnerControllerFlow", "target_symbol": "OwnerControllerFlow", "alias_edges": 0, "exporters": 1, "importers": 8}]
            if op == "get_symbol_exports_summary_files":
                return [
                    {
                        "file": "examples/OwnerControllerFlow.java",
                        "n": 12,
                        "symbols": ["OwnerControllerFlow", "OwnerController"],
                        "file_roles": None,
                    },
                    {
                        "file": "src/main/java/org/example/Pet.java",
                        "n": 10,
                        "symbols": ["Pet"],
                        "file_roles": None,
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("inspect `src/main/java/org/example/Pet.java` next", output)

    def test_symbol_exports_summary_legacy_benchmark_path_penalty_still_applies_when_roles_missing(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 2}]
            if op == "get_symbol_exports_summary_symbols":
                return [
                    {"symbol": "ByteBuffer", "target_symbol": "ByteBuffer", "alias_edges": 0, "exporters": 1, "importers": 292}
                ]
            if op == "get_symbol_exports_summary_files":
                return [
                    {
                        "file": "Benchmarks/Benchmarks/NIOCoreBenchmarks/Benchmarks.swift",
                        "n": 5,
                        "symbols": ["ByteBufferEnvelopeForwardingHandler"],
                        "file_roles": None,
                    },
                    {
                        "file": "Sources/NIOCore/ByteBuffer-core.swift",
                        "n": 2,
                        "symbols": ["ByteBuffer", "ByteBufferAllocator"],
                        "file_roles": None,
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("inspect `Sources/NIOCore/ByteBuffer-core.swift` next", output)

    def test_symbol_exports_summary_falls_back_to_visibility_and_python_naming(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 0}]
            if op == "get_symbol_exports_summary_symbols":
                return []
            if op == "get_symbol_exports_summary_files":
                return []
            if op == "get_symbol_exports_summary_heuristic":
                return [
                    {"file": "src/api/lib.rs", "symbol": "Router", "visibility": "pub", "file_roles": []},
                    {"file": "src/api/lib.rs", "symbol": "_internal_router", "visibility": "pub", "file_roles": []},
                    {"file": "pkg/service.py", "symbol": "PublicService", "visibility": "", "file_roles": []},
                    {"file": "pkg/service.py", "symbol": "_private_helper", "visibility": "", "file_roles": []},
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("Source: heuristic public-surface inference", output)
        self.assertIn("Use this to find the main public surfaces", output)
        self.assertIn("## Inspect First", output)
        self.assertIn("Router", output)
        self.assertIn("PublicService", output)
        self.assertNotIn("_private_helper", output)

    def test_symbol_exports_summary_heuristic_skips_test_path_penalty_when_file_roles_are_present(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 0}]
            if op == "get_symbol_exports_summary_symbols":
                return []
            if op == "get_symbol_exports_summary_files":
                return []
            if op == "get_symbol_exports_summary_heuristic":
                return [
                    {
                        "file": "tests/test_app.py",
                        "symbol": "FakeSession",
                        "visibility": "",
                        "file_roles": [],
                    }
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("tests/test_app.py", output)

    def test_symbol_exports_summary_heuristic_legacy_test_path_penalty_still_applies_when_roles_missing(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 0}]
            if op == "get_symbol_exports_summary_symbols":
                return []
            if op == "get_symbol_exports_summary_files":
                return []
            if op == "get_symbol_exports_summary_heuristic":
                return [
                    {
                        "file": "tests/test_app.py",
                        "symbol": "FakeSession",
                        "visibility": "",
                        "file_roles": None,
                    }
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertEqual(
            output,
            "No symbol exports found.\nChecked EXPORTS_SYMBOL edges, then visibility/name-based public-surface heuristics.",
        )

    def test_symbol_exports_summary_excludes_test_files_by_default(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 2}]
            if op == "get_symbol_exports_summary_symbols":
                return [
                    {"symbol": "ProdSymbol", "exporters": 1, "importers": 2},
                    {"symbol": "FakeSession", "exporters": 1, "importers": 0},
                ]
            if op == "get_symbol_exports_summary_files":
                return [
                    {"file": "src/app.py", "n": 1, "symbols": ["ProdSymbol"]},
                    {"file": "tests/test_app.py", "n": 1, "symbols": ["FakeSession"]},
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("src/app.py", output)
        self.assertNotIn("tests/test_app.py", output)

    def test_symbol_exports_summary_ranks_by_downstream_importers(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 3}]
            if op == "get_symbol_exports_summary_symbols":
                return [
                    {
                        "symbol": "register",
                        "target_symbol": "register",
                        "alias_edges": 0,
                        "exporters": 3,
                        "importers": 1,
                    },
                    {
                        "symbol": "WorkspaceRegistry",
                        "target_symbol": "WorkspaceRegistry",
                        "alias_edges": 0,
                        "exporters": 1,
                        "importers": 5,
                    },
                ]
            if op == "get_symbol_exports_summary_files":
                return [
                    {"file": "src/core.py", "n": 2, "symbols": ["WorkspaceRegistry", "register"]},
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertLess(output.find("WorkspaceRegistry"), output.find("register"))
        self.assertIn("imported by 5 file(s)", output)
        self.assertIn("start with `WorkspaceRegistry` because it has the strongest downstream import surface", output)

    def test_symbol_exports_summary_surfaces_alias_exports(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 1}]
            if op == "get_symbol_exports_summary_symbols":
                return [
                    {
                        "symbol": "PublicFoo",
                        "target_symbol": "Foo",
                        "alias_edges": 1,
                        "exporters": 1,
                        "importers": 4,
                    }
                ]
            if op == "get_symbol_exports_summary_files":
                return [
                    {"file": "src/index.ts", "n": 1, "symbols": ["PublicFoo -> Foo"]},
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("PublicFoo -> Foo", output)
        self.assertIn("src/index.ts", output)

    def test_symbol_exports_summary_filtered_alias_exports(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 2}]
            if op == "get_symbol_exports_summary_filtered_rows":
                return [
                    {
                        "file": "packages/sdk/js/src/client.ts",
                        "symbol": "OpencodeClientConfig",
                        "target_symbol": "Config",
                        "alias_edges": 1,
                        "importers": 6,
                    },
                    {
                        "file": "packages/sdk/js/src/v2/client.ts",
                        "symbol": "OpencodeClientConfig",
                        "target_symbol": "Config",
                        "alias_edges": 1,
                        "importers": 6,
                    },
                    {
                        "file": "packages/sdk/js/src/client.ts",
                        "symbol": "OpencodeClient",
                        "target_symbol": "OpencodeClient",
                        "alias_edges": 0,
                        "importers": 2,
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                    include_paths=["packages/sdk/js/src/client.ts", "packages/sdk/js/src/v2/client.ts"],
                    symbol_prefix="Opencode",
                )
            )

        self.assertIn("OpencodeClientConfig -> Config", output)
        self.assertIn("packages/sdk/js/src/client.ts", output)
        self.assertIn("## Inspect First", output)

    def test_symbol_exports_summary_prefers_real_file_over_stub_surface(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_symbol_exports_summary_count":
                return [{"n": 5}]
            if op == "get_symbol_exports_summary_symbols":
                return [
                    {
                        "symbol": "Language",
                        "target_symbol": "Language",
                        "alias_edges": 0,
                        "exporters": 1,
                        "importers": 16,
                    }
                ]
            if op == "get_symbol_exports_summary_files":
                return [
                    {
                        "file": "crates/ts-pack-python/python/tree_sitter_language_pack/__init__.pyi",
                        "n": 70,
                        "symbols": ["Language", "Snippet", "SnippetStatus"],
                    },
                    {
                        "file": "crates/ts-pack-core/src/lib.rs",
                        "n": 34,
                        "symbols": ["available_languages", "cache_dir", "clean_cache"],
                    },
                ]
            return []

        with mock.patch.object(self.module.graph_tools, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_symbol_exports_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    project_path="/tmp/repo",
                    limit=20,
                )
            )

        self.assertIn("inspect `crates/ts-pack-core/src/lib.rs` next", output)
        self.assertLess(output.find("crates/ts-pack-core/src/lib.rs"), output.find("__init__.pyi"))


if __name__ == "__main__":
    unittest.main()

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "graph" / "tools.py"
OVERVIEW_PATH = REPO_ROOT / "tools" / "brain" / "graph" / "overview.py"
UTILITY_PATH = REPO_ROOT / "tools" / "brain" / "graph" / "utility.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeCursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        self.query = query
        self.params = params
        return None

    async def fetchone(self):
        return (42,)

    async def fetchall(self):
        return []


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


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_tools_module():
    overview_spec = importlib.util.spec_from_file_location("tools.brain.graph.overview", OVERVIEW_PATH)
    overview_module = importlib.util.module_from_spec(overview_spec)
    assert overview_spec.loader is not None

    utility_spec = importlib.util.spec_from_file_location("tools.brain.graph.utility", UTILITY_PATH)
    utility_module = importlib.util.module_from_spec(utility_spec)
    assert utility_spec.loader is not None

    spec = importlib.util.spec_from_file_location("tools.brain.graph.tools", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (FakeMemoryStore, None, None, None, None)
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/framecreator"

    graph_core_mod = types.ModuleType("tools.brain.graph.core")
    graph_core_mod._SYMBOL_FILTER_CYPHER = "true"

    async def _execute_read(*args, **kwargs):
        return []

    graph_core_mod._execute_read = _execute_read
    graph_core_mod._get_cli_flow_summary = mock.AsyncMock(return_value="No CLI flows found.")
    graph_core_mod._summarize_batches = lambda *args, **kwargs: (0, 0, 0)
    graph_core_mod.get_last_graph_build_metric = lambda: None

    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *args, **kwargs: None

    graphrag_pkg = types.ModuleType("graphrag_core")
    graphrag_pkg.__path__ = []
    graphrag_indexing_pkg = types.ModuleType("graphrag_core.indexing")
    graphrag_indexing_pkg.__path__ = []
    watcher_mod = types.ModuleType("graphrag_core.indexing.watcher")
    watcher_mod.load_indexed_projects = lambda: {}

    flow_summary_mod = types.ModuleType("tools.brain.graph.flow_summary")
    flow_summary_mod.get_app_flow_summary_impl = mock.AsyncMock(return_value="No UI → API → Service → DB paths found.")
    flow_summary_mod.get_backend_flow_summary_impl = mock.AsyncMock(return_value="No API → Service → DB paths found.")
    flow_summary_mod.get_apple_build_summary_impl = mock.AsyncMock(return_value="No Apple build graph paths found.")

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
    graph_contract_mod = types.ModuleType("tools.brain.graph_contract")
    graph_contract_mod.node_label = lambda name: {
        "file": "File",
        "xcode_target": "XcodeTarget",
        "xcode_scheme": "XcodeScheme",
        "xcode_workspace": "XcodeWorkspace",
        "cargo_crate": "CargoCrate",
        "cargo_workspace": "CargoWorkspace",
    }.get(name, name)
    graph_contract_mod.rel_type = lambda name: {
        "bundles_file": "BUNDLES_FILE",
        "builds_target": "BUILDS_TARGET",
        "references_project": "REFERENCES_PROJECT",
        "defined_in_file": "DEFINED_IN_FILE",
        "has_package": "HAS_PACKAGE",
        "depends_on_package": "DEPENDS_ON_PACKAGE",
        "imports": "IMPORTS",
        "asset_links": "ASSET_LINKS",
        "calls_api": "CALLS_API",
        "contains": "CONTAINS",
    }.get(name, name.upper())
    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "graph_bootstrap": graph_bootstrap_mod,
            "dotenv": dotenv_mod,
            "graphrag_core": graphrag_pkg,
            "graphrag_core.indexing": graphrag_indexing_pkg,
            "graphrag_core.indexing.watcher": watcher_mod,
            "mcp.server.fastmcp": mcp_mod,
            "tools": graph_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.graph_contract": graph_contract_mod,
            "tools.brain.graph": graph_subpkg,
            "tools.brain.graph.core": graph_core_mod,
            "tools.brain.graph.flow_summary": flow_summary_mod,
            "tools.brain.graph.utility": utility_module,
        },
    ):
        overview_spec.loader.exec_module(overview_module)
        sys.modules["tools.brain.graph.overview"] = overview_module
        utility_spec.loader.exec_module(utility_module)
        sys.modules["tools.brain.graph.utility"] = utility_module
        spec.loader.exec_module(module)
    return module


class GraphToolsTests(unittest.TestCase):
    def setUp(self):
        self.module = load_tools_module()
        self.mcp = FakeMCP()
        self.module.register(self.mcp)
        self.graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        self.graph_bootstrap_mod.require_driver = _require_driver
        self.graph_bootstrap_mod._NEO4J_DB = "neo4j"

    def test_directory_snapshot_includes_apple_build_context(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 1}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            if op == "get_directory_snapshot_files":
                return [{"fp": "ios/App/View.swift", "sym_count": 3, "samples": ["body", "preview"]}]
            if op == "get_directory_snapshot_inbound":
                return []
            if op == "get_directory_snapshot_outbound":
                return []
            if op == "get_directory_snapshot_assets":
                return []
            if op == "apple_context_targets":
                return [{"target": "App", "project_file": "ios/App.xcodeproj/project.pbxproj", "bundled_files": 4}]
            if op == "apple_context_schemes":
                return [{"scheme": "App", "targets": ["App"]}]
            if op == "apple_context_schema_labels":
                return [{"labels": []}]
            if op == "apple_context_schema_relationship_types":
                return [{"rels": []}]
            if op == "apple_context_workspaces":
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_directory_snapshot"]("/tmp/framecreator", "ios", 5))

        self.assertIn("Apple Build Context", output)
        self.assertIn("target `App` bundles 4 file(s)", output)
        self.assertIn("scheme `App` builds App", output)
        self.assertIn("Use this to land in one directory", output)
        self.assertIn("Recommended Inspection Order", output)

    def test_directory_snapshot_prioritizes_workspace_references_for_apple_workspace_dir(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 1}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            if op == "get_directory_snapshot_files":
                return [{"fp": "BGM.xcworkspace/contents.xcworkspacedata", "sym_count": 0, "samples": []}]
            if op == "get_directory_snapshot_inbound":
                return []
            if op == "get_directory_snapshot_outbound":
                return []
            if op == "get_directory_snapshot_assets":
                return []
            if op == "apple_context_targets":
                return []
            if op == "apple_context_schemes":
                return []
            if op == "apple_context_schema_labels":
                return [{"labels": ["XcodeWorkspace"]}]
            if op == "apple_context_schema_relationship_types":
                return [{"rels": ["REFERENCES_PROJECT"]}]
            if op == "apple_context_workspaces":
                return [
                    {
                        "workspace": "BGM.xcworkspace/contents.xcworkspacedata",
                        "projects": [
                            "BGMDriver/BGMDriver.xcodeproj/project.pbxproj",
                            "BGMApp/BGMApp.xcodeproj/project.pbxproj",
                        ],
                    }
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["get_directory_snapshot"](
                        "/tmp/loombackgroundmusic",
                        "BGM.xcworkspace",
                        5,
                    )
                )

        self.assertIn(
            "inspect workspace project references first because this directory defines the top-level Xcode workspace",
            output,
        )
        self.assertIn(
            "check `BGM.xcworkspace/contents.xcworkspacedata` next because it resolves the owning Xcode projects for this workspace",
            output,
        )
        self.assertIn(
            "workspace `BGM.xcworkspace/contents.xcworkspacedata` references BGMDriver/BGMDriver.xcodeproj/project.pbxproj, BGMApp/BGMApp.xcodeproj/project.pbxproj",
            output,
        )

    def test_directory_snapshot_falls_back_to_file_graph_for_swift_coupling(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 1}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            if op == "get_directory_snapshot_files":
                return [
                    {"fp": "FrameCreator/Views/SidebarView.swift", "sym_count": 2, "samples": ["SidebarView", "SidebarView"]},
                    {"fp": "FrameCreator/Views/CanvasView.swift", "sym_count": 5, "samples": ["CanvasView", "CheckerboardBackground"]},
                ]
            if op == "get_directory_snapshot_inbound":
                return []
            if op == "get_directory_snapshot_outbound":
                return []
            if op == "get_directory_snapshot_inbound_file_graph_fallback":
                return []
            if op == "get_directory_snapshot_outbound_file_graph_fallback":
                return []
            if op == "get_directory_snapshot_inbound_symbol_call_fallback":
                return [{"caller": "FrameCreator/Views/ContentView.swift", "n_imports": 1}]
            if op == "get_directory_snapshot_outbound_symbol_call_fallback":
                return [{"dependency": "FrameCreator/ViewModels/EditorViewModel.swift", "n_usages": 1}]
            if op == "get_directory_snapshot_assets":
                return []
            if op == "apple_context_targets":
                return [{"target": "FrameCreator", "project_file": "FrameCreator.xcodeproj/project.pbxproj", "bundled_files": 0}]
            if op == "apple_context_schemes":
                return [{"scheme": "FrameCreator", "targets": ["FrameCreator", "FrameCreatorUITests"]}]
            if op == "apple_context_schema_labels":
                return [{"labels": []}]
            if op == "apple_context_schema_relationship_types":
                return [{"rels": []}]
            if op == "apple_context_workspaces":
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_directory_snapshot"]("/tmp/framecreator", "FrameCreator/Views", 5))

        self.assertIn("### 📥 Consumers (External files importing from here)", output)
        self.assertIn("FrameCreator/Views/ContentView.swift", output)
        self.assertIn("### 📤 Dependencies (External files imported by here)", output)
        self.assertIn("FrameCreator/ViewModels/EditorViewModel.swift", output)
        self.assertNotIn("### 📥 Consumers: None found.", output)
        self.assertNotIn("### 📤 Dependencies: None found.", output)

    def test_directory_snapshot_downweights_test_consumers_and_dependencies(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            if op == "get_directory_snapshot_files":
                return [
                    {"fp": "Sources/NIOPosix/BaseSocketChannel.swift", "sym_count": 12, "samples": ["run", "bind"]},
                    {"fp": "Sources/NIOPosix/Bootstrap.swift", "sym_count": 10, "samples": ["bootstrap", "bind"]},
                ]
            if op == "get_directory_snapshot_inbound":
                return [
                    {"caller": "Tests/NIOPosixTests/ChannelTests.swift", "n_imports": 5, "signal": "import"},
                    {"caller": "Sources/NIOEchoClient/main.swift", "n_imports": 3, "signal": "import"},
                ]
            if op == "get_directory_snapshot_outbound":
                return [
                    {"dependency": "Tests/NIOCoreTests/ByteBufferSpanTests.swift", "n_usages": 4, "signal": "import"},
                    {"dependency": "Sources/NIOCore/AsyncAwaitSupport.swift", "n_usages": 3, "signal": "import"},
                ]
            if op == "get_directory_snapshot_assets":
                return []
            if op in {
                "apple_context_targets",
                "apple_context_schemes",
                "apple_context_schema_labels",
                "apple_context_schema_relationship_types",
                "apple_context_workspaces",
                "cargo_context_schema_labels",
                "cargo_context_schema_relationship_types",
                "cargo_context_crates",
                "cargo_context_workspaces",
                "cargo_context_dependencies",
                "cargo_directory_schema_labels",
                "cargo_directory_schema_relationship_types",
                "cargo_directory_dependencies_outbound",
                "cargo_directory_dependencies_inbound",
                "get_directory_snapshot_local_symbols",
                "get_directory_snapshot_external_symbols",
            }:
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_directory_snapshot"]("/tmp/swift-nio", "Sources/NIOPosix", 8))

        prod_consumer_idx = output.index("Sources/NIOEchoClient/main.swift")
        test_consumer_idx = output.index("Tests/NIOPosixTests/ChannelTests.swift")
        prod_dep_idx = output.index("Sources/NIOCore/AsyncAwaitSupport.swift")
        test_dep_idx = output.index("Tests/NIOCoreTests/ByteBufferSpanTests.swift")
        self.assertLess(prod_consumer_idx, test_consumer_idx)
        self.assertLess(prod_dep_idx, test_dep_idx)

    def test_directory_snapshot_hides_only_static_config_and_test_consumers_in_code_dirs(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            if op == "get_directory_snapshot_files":
                return [
                    {"fp": "src/main/java/org/example/owner/OwnerController.java", "sym_count": 1, "samples": ["OwnerController"]},
                    {"fp": "src/main/java/org/example/owner/PetController.java", "sym_count": 1, "samples": ["PetController"]},
                ]
            if op == "get_directory_snapshot_inbound":
                return [
                    {"caller": "src/main/resources/static/resources/css/app.css", "n_imports": 84, "signal": "import"},
                    {"caller": "k8s/app.yml", "n_imports": 2, "signal": "import"},
                    {"caller": "src/test/java/org/example/owner/OwnerControllerTests.java", "n_imports": 4, "signal": "import"},
                ]
            if op == "get_directory_snapshot_outbound":
                return []
            if op == "get_directory_snapshot_assets":
                return []
            if op in {
                "apple_context_targets",
                "apple_context_schemes",
                "apple_context_schema_labels",
                "apple_context_schema_relationship_types",
                "apple_context_workspaces",
                "cargo_context_schema_labels",
                "cargo_context_schema_relationship_types",
                "cargo_context_crates",
                "cargo_context_workspaces",
                "cargo_context_dependencies",
                "cargo_directory_schema_labels",
                "cargo_directory_schema_relationship_types",
                "cargo_directory_dependencies_outbound",
                "cargo_directory_dependencies_inbound",
                "get_directory_snapshot_inbound_file_graph_fallback",
                "get_directory_snapshot_outbound_file_graph_fallback",
                "get_directory_snapshot_inbound_symbol_call_fallback",
                "get_directory_snapshot_outbound_symbol_call_fallback",
                "get_directory_snapshot_local_symbols",
                "get_directory_snapshot_external_symbols",
            }:
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["get_directory_snapshot"](
                        "/tmp/spring-petclinic",
                        "src/main/java/org/example/owner",
                        8,
                    )
                )

        self.assertIn("### 📥 Consumers: None found.", output)
        self.assertNotIn("app.css", output)
        self.assertNotIn("k8s/app.yml", output)
        self.assertNotIn("OwnerControllerTests.java", output)

    def test_directory_snapshot_includes_cargo_context(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"file_count": 1}]
            if op == "get_directory_snapshot_files":
                return [{"fp": "crates/api/src/lib.rs", "sym_count": 5, "samples": ["run", "serve"]}]
            if op == "get_directory_snapshot_inbound":
                return []
            if op == "get_directory_snapshot_outbound":
                return []
            if op == "get_directory_snapshot_assets":
                return []
            if op == "cargo_context_schema_labels":
                return [{"labels": ["CargoCrate", "CargoWorkspace"]}]
            if op == "cargo_context_schema_relationship_types":
                return [{"rels": ["HAS_PACKAGE", "DEPENDS_ON_PACKAGE"]}]
            if op == "cargo_context_crates":
                return [{"crate": "api", "crate_name": "api", "manifest_path": "crates/api/Cargo.toml", "manifest_files": 1}]
            if op == "cargo_context_workspaces":
                return [{"workspace": "Cargo.toml", "crates": ["api", "core"]}]
            if op == "cargo_context_dependencies":
                return [{"crate": "api", "deps": ["core", "serde"]}]
            if op == "cargo_directory_schema_labels":
                return [{"labels": ["CargoCrate"]}]
            if op == "cargo_directory_schema_relationship_types":
                return [{"rels": ["DEPENDS_ON_PACKAGE"]}]
            if op == "cargo_directory_dependencies_outbound":
                return [{"crate": "api", "deps": ["core", "serde"]}]
            if op == "cargo_directory_dependencies_inbound":
                return [{"crate": "api", "dependents": ["cli", "admin"]}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_directory_snapshot"]("/tmp/rustws", "crates/api", 5))

        self.assertIn("Cargo Context", output)
        self.assertIn("crate `api` (api) via `crates/api/Cargo.toml`", output)
        self.assertIn("workspace `Cargo.toml` includes api, core", output)
        self.assertIn("crate `api` depends on core, serde", output)
        self.assertIn("local crate `api` is used by cli, admin", output)
        self.assertIn("Recommended Inspection Order", output)

    def test_directory_snapshot_filters_external_cargo_crates_when_local_manifest_exists(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"file_count": 1}]
            if op == "get_directory_snapshot_files":
                return [{"fp": "packages/desktop/src-tauri/src/lib.rs", "sym_count": 29, "samples": ["await_initialization"]}]
            if op == "get_directory_snapshot_inbound":
                return []
            if op == "get_directory_snapshot_outbound":
                return []
            if op == "get_directory_snapshot_assets":
                return []
            if op == "cargo_context_schema_labels":
                return [{"labels": ["CargoCrate", "CargoWorkspace"]}]
            if op == "cargo_context_schema_relationship_types":
                return [{"rels": ["HAS_PACKAGE", "DEPENDS_ON_PACKAGE"]}]
            if op == "cargo_context_crates":
                return [
                    {"crate": "chrono", "crate_name": "chrono", "manifest_path": "", "manifest_files": 0},
                ]
            if op == "cargo_context_crates_manifest_fallback":
                return [
                    {
                        "crate": "desktop",
                        "crate_name": "desktop",
                        "manifest_path": "packages/desktop/src-tauri/Cargo.toml",
                        "manifest_files": 1,
                    }
                ]
            if op == "cargo_context_workspaces":
                return [{"workspace": "Cargo.toml", "crates": ["desktop"]}]
            if op == "cargo_context_dependencies":
                return [{"crate": "desktop", "deps": ["chrono"]}]
            if op == "cargo_directory_schema_labels":
                return [{"labels": []}]
            if op == "cargo_directory_schema_relationship_types":
                return [{"rels": []}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["get_directory_snapshot"]("/tmp/opencode", "packages/desktop/src-tauri", 5)
                )

        self.assertIn("crate `desktop` (desktop) via `packages/desktop/src-tauri/Cargo.toml`", output)
        self.assertNotIn("crate `chrono` (chrono) via `(external crate)`", output)

    def test_directory_snapshot_prefers_java_controller_and_demotes_static_resource_consumers(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"file_count": 0}]
            if op == "get_directory_snapshot_files":
                return [
                    {
                        "fp": "src/main/java/org/springframework/samples/petclinic/owner/Owner.java",
                        "sym_count": 1,
                        "samples": ["Owner"],
                    },
                    {
                        "fp": "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
                        "sym_count": 1,
                        "samples": ["OwnerController"],
                    },
                    {
                        "fp": "src/main/java/org/springframework/samples/petclinic/owner/PetController.java",
                        "sym_count": 1,
                        "samples": ["PetController"],
                    },
                ]
            if op == "get_directory_snapshot_inbound":
                return [
                    {
                        "caller": "src/main/resources/static/resources/css/petclinic.css",
                        "n_imports": 84,
                        "signal": "import",
                    },
                    {
                        "caller": "src/test/java/org/springframework/samples/petclinic/owner/OwnerControllerTests.java",
                        "n_imports": 4,
                        "signal": "import",
                    },
                    {
                        "caller": "src/main/java/org/springframework/samples/petclinic/owner/VisitController.java",
                        "n_imports": 3,
                        "signal": "import",
                    },
                ]
            if op == "get_directory_snapshot_outbound":
                return []
            if op == "get_directory_snapshot_assets":
                return []
            if op in {
                "get_directory_snapshot_local_symbols",
                "get_directory_snapshot_external_symbols",
            }:
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["get_directory_snapshot"](
                        "/tmp/spring-petclinic-upstream",
                        "src/main/java/org/springframework/samples/petclinic/owner",
                        12,
                    )
                )

        self.assertIn(
            "start with `src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java`",
            output,
        )
        self.assertIn(
            "check inbound usage from `src/main/java/org/springframework/samples/petclinic/owner/VisitController.java` first",
            output,
        )
        self.assertLess(
            output.index("OwnerController.java"),
            output.index("Owner.java"),
        )

    def test_directory_snapshot_prefers_production_files_over_tests_in_code_dirs(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"file_count": 0}]
            if op == "get_directory_snapshot_files":
                return [
                    {
                        "fp": "axum/src/routing/tests/mod.rs",
                        "sym_count": 66,
                        "samples": ["asterisk_in_route", "body_limited_by_default"],
                    },
                    {
                        "fp": "axum/src/routing/tests/nest.rs",
                        "sym_count": 32,
                        "samples": ["asterisk_in_route", "colon_in_route"],
                    },
                    {
                        "fp": "axum/src/routing/mod.rs",
                        "sym_count": 12,
                        "samples": ["take_route_or_internal_error", "traits"],
                    },
                    {
                        "fp": "axum/src/routing/method_routing.rs",
                        "sym_count": 11,
                        "samples": ["any", "any_service"],
                    },
                ]
            if op == "get_directory_snapshot_inbound":
                return [{"caller": "axum-extra/src/routing/mod.rs", "n_imports": 1, "signal": "import"}]
            if op == "get_directory_snapshot_outbound":
                return [{"dependency": "axum/src/middleware/map_request.rs", "n_usages": 1, "signal": "import"}]
            if op == "get_directory_snapshot_assets":
                return []
            if op in {
                "get_directory_snapshot_local_symbols",
                "get_directory_snapshot_external_symbols",
            }:
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_directory_snapshot"]("/tmp/axum", "axum/src/routing", 12))

        self.assertIn(
            "start with `axum/src/routing/mod.rs` because it has the densest local symbol surface (12 symbols)",
            output,
        )
        self.assertNotIn("start with `axum/src/routing/tests/mod.rs`", output)
        self.assertLess(output.index("axum/src/routing/mod.rs"), output.index("axum/src/routing/method_routing.rs"))
        self.assertNotIn("axum/src/routing/tests/mod.rs", output.split("### 📥 Consumers", 1)[0])

    def test_directory_snapshot_falls_back_to_sibling_impl_when_only_static_consumers_exist(self):
        file_rows = [
            {"fp": "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java", "sym_count": 1},
            {"fp": "src/main/java/org/springframework/samples/petclinic/owner/PetController.java", "sym_count": 1},
            {"fp": "src/main/java/org/springframework/samples/petclinic/owner/Owner.java", "sym_count": 1},
        ]
        inbound_rows = [
            {"caller": "src/main/resources/static/resources/css/petclinic.css", "n_imports": 84, "signal": "import"},
            {"caller": "k8s/petclinic.yml", "n_imports": 2, "signal": "import"},
        ]

        lines = self.module.graph_overview._directory_snapshot_priority_lines(
            "src/main/java/org/springframework/samples/petclinic/owner",
            file_rows,
            inbound_rows,
            [],
            [],
            False,
            False,
            [],
            [],
        )

        self.assertIn(
            "- inspect sibling implementation `src/main/java/org/springframework/samples/petclinic/owner/PetController.java` next because external consumer signal here is mostly static/config noise",
            lines,
        )

    def test_directory_snapshot_demotes_generated_swift_consumers_and_dependencies(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"file_count": 0}]
            if op == "get_directory_snapshot_files":
                return [
                    {
                        "fp": "FrameCreator/Views/SidebarView.swift",
                        "sym_count": 4,
                        "samples": ["SidebarView", "moveSelectedReferenceUp"],
                    }
                ]
            if op == "get_directory_snapshot_inbound":
                return [
                    {
                        "caller": "FrameCreator/Services/Generation/DrawThingsGRPC/imageService.pb.swift",
                        "n_imports": 142,
                    },
                    {"caller": "FrameCreator/Views/ContentView.swift", "n_imports": 1},
                ]
            if op == "get_directory_snapshot_outbound":
                return [
                    {
                        "dependency": "FrameCreator/Services/Generation/DrawThingsGRPC/imageService.grpc.swift",
                        "n_usages": 5,
                    },
                    {"dependency": "FrameCreator/ViewModels/EditorViewModel.swift", "n_usages": 4},
                ]
            if op == "get_directory_snapshot_inbound_symbol_call_fallback":
                return [{"caller": "FrameCreator/Views/ContentView.swift", "n_imports": 1}]
            if op == "get_directory_snapshot_outbound_symbol_call_fallback":
                return [{"dependency": "FrameCreator/ViewModels/EditorViewModel.swift", "n_usages": 4}]
            if op == "get_directory_snapshot_assets":
                return []
            if op == "get_directory_snapshot_local_symbols":
                return [{"name": "SidebarView"}]
            if op == "get_directory_snapshot_external_symbols":
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["get_directory_snapshot"]("/tmp/framecreator", "FrameCreator/Views", 5)
                )

        consumers_index = output.index("### 📥 Consumers")
        content_index = output.index("FrameCreator/Views/ContentView.swift", consumers_index)
        generated_index = output.index(
            "FrameCreator/Services/Generation/DrawThingsGRPC/imageService.pb.swift", consumers_index
        )
        self.assertLess(content_index, generated_index)
        self.assertIn("[generated/support]", output)
        deps_index = output.index("### 📤 Dependencies")
        editor_index = output.index("FrameCreator/ViewModels/EditorViewModel.swift", deps_index)
        grpc_index = output.index(
            "FrameCreator/Services/Generation/DrawThingsGRPC/imageService.grpc.swift", deps_index
        )
        self.assertLess(editor_index, grpc_index)

    def test_directory_snapshot_demotes_cross_language_include_headers_in_code_dirs(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"file_count": 0}]
            if op == "get_directory_snapshot_files":
                return [
                    {
                        "fp": "Sources/NIOCore/ChannelPipeline.swift",
                        "sym_count": 113,
                        "samples": ["removeHandler0", "whitespace"],
                    }
                ]
            if op == "get_directory_snapshot_inbound":
                return [{"caller": "Sources/NIOFS/BufferedWriter.swift", "n_imports": 1, "signal": "import"}]
            if op == "get_directory_snapshot_outbound":
                return [
                    {"dependency": "Sources/CNIOLinux/include/CNIOLinux.h", "n_usages": 8, "signal": "import"},
                    {
                        "dependency": "Sources/NIOConcurrencyHelpers/NIOThreadPoolWorkAvailable.swift",
                        "n_usages": 6,
                        "signal": "import",
                    },
                ]
            if op == "get_directory_snapshot_assets":
                return []
            if op in {
                "get_directory_snapshot_local_symbols",
                "get_directory_snapshot_external_symbols",
            }:
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_directory_snapshot"]("/tmp/swift-nio", "Sources/NIOCore", 12))

        self.assertIn(
            "check outbound dependency `Sources/NIOConcurrencyHelpers/NIOThreadPoolWorkAvailable.swift` because files here rely on it most often",
            output,
        )
        deps_index = output.index("### 📤 Dependencies")
        swift_index = output.index("Sources/NIOConcurrencyHelpers/NIOThreadPoolWorkAvailable.swift", deps_index)
        header_index = output.index("Sources/CNIOLinux/include/CNIOLinux.h", deps_index)
        self.assertLess(swift_index, header_index)

    def test_directory_snapshot_demotes_apple_assets_and_build_support_in_code_dirs(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "apple_context_presence":
                return [{"n": 1}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            if op == "get_directory_snapshot_files":
                return [
                    {
                        "fp": "App/Store/SubscriptionStoreView.swift",
                        "sym_count": 15,
                        "samples": ["binding", "subscriptionOptionCell"],
                    }
                ]
            if op == "get_directory_snapshot_inbound":
                return [
                    {
                        "caller": "FoodTruckKit/Sources/Assets.xcassets/donut/donut.symbolset/donut.svg",
                        "n_imports": 55,
                        "signal": "import",
                    },
                    {
                        "caller": "Food Truck.xcodeproj/project.pbxproj",
                        "n_imports": 11,
                        "signal": "import",
                    },
                    {
                        "caller": "FoodTruckKit/Sources/Order/Order.swift",
                        "n_imports": 5,
                        "signal": "import",
                    },
                ]
            if op == "get_directory_snapshot_outbound":
                return [
                    {
                        "dependency": "Food Truck.xcodeproj/xcshareddata/xcschemes/FoodTruckKit.xcscheme",
                        "n_usages": 37,
                        "signal": "import",
                    },
                    {
                        "dependency": "FoodTruckKit/Sources/Donut/DonutView.swift",
                        "n_usages": 9,
                        "signal": "import",
                    },
                ]
            if op == "get_directory_snapshot_assets":
                return []
            if op == "apple_context_targets":
                return [{"target": "Food Truck", "project_file": "Food Truck.xcodeproj/project.pbxproj", "bundled_files": 7}]
            if op == "apple_context_schemes":
                return [{"scheme": "Food Truck", "targets": ["Food Truck"]}]
            if op == "apple_context_schema_labels":
                return [{"labels": []}]
            if op == "apple_context_schema_relationship_types":
                return [{"rels": []}]
            if op == "apple_context_workspaces":
                return []
            if op in {
                "get_directory_snapshot_local_symbols",
                "get_directory_snapshot_external_symbols",
            }:
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_directory_snapshot"]("/tmp/sample-food-truck", "App", 12))

        self.assertIn(
            "check inbound usage from `FoodTruckKit/Sources/Order/Order.swift` first because it is the strongest external consumer",
            output,
        )
        self.assertIn(
            "check outbound dependency `FoodTruckKit/Sources/Donut/DonutView.swift` because files here rely on it most often",
            output,
        )
        consumers_index = output.index("### 📥 Consumers")
        order_index = output.index("FoodTruckKit/Sources/Order/Order.swift", consumers_index)
        asset_index = output.index("FoodTruckKit/Sources/Assets.xcassets/donut/donut.symbolset/donut.svg", consumers_index)
        project_index = output.index("Food Truck.xcodeproj/project.pbxproj", consumers_index)
        self.assertLess(order_index, asset_index)
        self.assertLess(order_index, project_index)
        deps_index = output.index("### 📤 Dependencies")
        donut_index = output.index("FoodTruckKit/Sources/Donut/DonutView.swift", deps_index)
        scheme_index = output.index("Food Truck.xcodeproj/xcshareddata/xcschemes/FoodTruckKit.xcscheme", deps_index)
        self.assertLess(donut_index, scheme_index)

    def test_directory_snapshot_prefers_symbol_call_signal_over_import_volume(self):
        merged = self.module.graph_overview._merge_directory_snapshot_rows(
            [
                {
                    "caller": "FrameCreator/Services/Generation/DrawThingsGRPC/imageService.pb.swift",
                    "n_imports": 142,
                    "signal": "import",
                },
                {
                    "caller": "FrameCreator/Views/ContentView.swift",
                    "n_imports": 1,
                    "signal": "import",
                },
            ],
            [
                {
                    "caller": "FrameCreator/Views/ContentView.swift",
                    "n_imports": 1,
                    "signal": "symbol_call",
                }
            ],
            path_key="caller",
            count_key="n_imports",
        )
        ranked = self.module.graph_overview._rank_directory_snapshot_rows(
            merged,
            path_key="caller",
            count_key="n_imports",
            limit=5,
        )

        self.assertEqual(ranked[0]["caller"], "FrameCreator/Views/ContentView.swift")
        self.assertEqual(ranked[0]["signal"], "symbol_call")

    def test_repo_dependency_summary_includes_inspect_first_guidance(self):
        with mock.patch.object(
            self.module.graph_overview,
            "_parse_repo_linked_dependencies",
            return_value=[
                {
                    "package": "repoanalyzer",
                    "repo_name": "RepoAnalyzer",
                    "repo_url": "https://example.com/repoanalyzer.git",
                    "rev": "abc123",
                    "subdirectory": "python",
                },
                {
                    "package": "thinlib",
                    "repo_name": "thinlib",
                    "repo_url": "https://example.com/thinlib.git",
                    "rev": "",
                    "subdirectory": "",
                },
            ],
        ), mock.patch.object(
            self.module.graph_overview,
            "_load_indexed_project_paths",
            return_value={"RepoAnalyzer": "/tmp/RepoAnalyzer"},
        ), mock.patch.object(
            self.module.graph_overview,
            "_find_repo_link_evidence",
            side_effect=lambda project_path, package, limit=8: ["src/app.py", "src/index.py"]
            if package == "repoanalyzer"
            else ["src/thin.py"],
        ):
            output = asyncio.run(self.mcp.tools["get_repo_dependency_summary"]("/tmp/framecreator"))

        self.assertIn("Use this to spot cross-repo integration boundaries", output)
        self.assertIn("## Inspect First", output)
        self.assertIn("`repoanalyzer` from `RepoAnalyzer`", output)
        self.assertIn("indexed sibling repo", output)

    def test_project_overview_includes_apple_build_context(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 12}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 20}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "ios", "files": 8, "syms": 14}]
            if op == "get_project_overview_key_files":
                return [{"fp": "ios/App/View.swift", "n": 4, "ex": ["body", "preview"]}]
            if op == "apple_context_presence":
                return [{"n": 1}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            if op == "apple_context_targets":
                return [{"target": "App", "project_file": "ios/App.xcodeproj/project.pbxproj", "bundled_files": 4}]
            if op == "apple_context_schemes":
                return [{"scheme": "App", "targets": ["App"]}]
            if op == "apple_context_schema_labels":
                return [{"labels": ["XcodeWorkspace"]}]
            if op == "apple_context_schema_relationship_types":
                return [{"rels": ["REFERENCES_PROJECT"]}]
            if op == "apple_context_workspaces":
                return [{"workspace": "ios/App.xcworkspace/contents.xcworkspacedata", "projects": ["ios/App.xcodeproj/project.pbxproj"]}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/framecreator"))

        self.assertIn("## Apple Build Context", output)
        self.assertIn("## Inspect First", output)
        self.assertIn("inspect Apple build context first because target `App` via `ios/App.xcodeproj/project.pbxproj` anchors the app structure", output)
        self.assertIn("target `App` bundles 4 file(s)", output)
        self.assertIn("scheme `App` builds App", output)
        self.assertIn("workspace `ios/App.xcworkspace/contents.xcworkspacedata` references ios/App.xcodeproj/project.pbxproj", output)

    def test_project_overview_includes_cargo_workspace_context(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 24}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 40}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "crates", "files": 18, "syms": 31}]
            if op == "get_project_overview_key_files":
                return [{"fp": "crates/api/src/lib.rs", "n": 6, "ex": ["run", "serve"]}]
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"file_count": 1}]
            if op == "cargo_context_schema_labels":
                return [{"labels": ["CargoCrate", "CargoWorkspace"]}]
            if op == "cargo_context_schema_relationship_types":
                return [{"rels": ["HAS_PACKAGE", "DEPENDS_ON_PACKAGE"]}]
            if op == "cargo_context_crates":
                return [
                    {"crate": "api", "crate_name": "api", "manifest_path": "crates/api/Cargo.toml", "manifest_files": 1},
                    {"crate": "core-lib", "crate_name": "core_lib", "manifest_path": "crates/core/Cargo.toml", "manifest_files": 1},
                ]
            if op == "cargo_context_workspaces":
                return [{"workspace": "Cargo.toml", "crates": ["api", "core-lib"]}]
            if op == "cargo_context_dependencies":
                return [{"crate": "api", "deps": ["core-lib", "serde"]}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/rustws"))

        self.assertIn("## Cargo Workspace Context", output)
        self.assertIn("crate `api` (api) via `crates/api/Cargo.toml`", output)
        self.assertIn("crate `core-lib` (core_lib) via `crates/core/Cargo.toml`", output)
        self.assertIn("workspace `Cargo.toml` includes api, core-lib", output)
        self.assertIn("crate `api` depends on core-lib, serde", output)

    def test_project_overview_skips_apple_queries_for_non_apple_repo(self):
        seen_ops = []

        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            seen_ops.append(op)
            if op == "get_project_overview_file_count":
                return [{"files": 12}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 20}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "src", "files": 8, "syms": 14}]
            if op == "get_project_overview_key_files":
                return [{"fp": "src/app.js", "n": 4, "ex": ["start", "stop"]}]
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            if op in {"apple_context_targets", "apple_context_schemes", "apple_context_schema_labels", "apple_context_schema_relationship_types", "apple_context_workspaces"}:
                raise AssertionError(f"unexpected Apple query: {op}")
            if op in {"cargo_context_crates", "cargo_context_workspaces", "cargo_context_dependencies", "cargo_context_schema_labels", "cargo_context_schema_relationship_types"}:
                raise AssertionError(f"unexpected Cargo query: {op}")
            if op in {"cargo_directory_schema_labels", "cargo_directory_schema_relationship_types", "cargo_directory_dependencies_outbound", "cargo_directory_dependencies_inbound"}:
                raise AssertionError(f"unexpected Cargo dependency query: {op}")
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/framecreator"))

        self.assertNotIn("## Apple Build Context", output)
        self.assertIn("apple_context_presence", seen_ops)
        self.assertIn("cargo_context_presence", seen_ops)

    def test_project_overview_downweights_frontend_asset_heaviness(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 30}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 100}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "src", "files": 24, "syms": 90}]
            if op == "get_project_overview_key_files":
                return [
                    {
                        "fp": "src/public/assets/financials.js",
                        "n": 82,
                        "ex": ["getToken", "money", "percent"],
                    },
                    {
                        "fp": "src/services/QuickBooksService.ts",
                        "n": 18,
                        "ex": ["syncAccounts", "exportBatch"],
                    },
                ]
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/rental"))

        quickbooks_index = output.index("src/services/QuickBooksService.ts")
        financials_index = output.index("src/public/assets/financials.js")
        self.assertLess(quickbooks_index, financials_index)

    def test_project_overview_downweights_generated_sdk_files(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 200}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 800}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "packages", "files": 180, "syms": 760}]
            if op == "get_project_overview_key_files":
                return [
                    {
                        "fp": "packages/sdk/js/src/v2/gen/types.gen.ts",
                        "n": 597,
                        "ex": ["Agent", "AgentConfig", "AgentPart"],
                    },
                    {
                        "fp": "packages/opencode/src/config/config.ts",
                        "n": 54,
                        "ex": ["Agent", "Command", "Info"],
                    },
                ]
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/opencode"))

        self.assertIn("packages/opencode/src/config/config.ts", output)
        self.assertNotIn("packages/sdk/js/src/v2/gen/types.gen.ts", output)

    def test_project_overview_downweights_e2e_and_icon_heavy_files(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 400}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 1200}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "packages", "files": 360, "syms": 1100}]
            if op == "get_project_overview_key_files":
                return [
                    {
                        "fp": "packages/web/src/components/icons/index.tsx",
                        "n": 299,
                        "ex": ["IconAcademicCap", "IconAdjustmentsHorizontal", "IconAdjustmentsVertical"],
                    },
                    {
                        "fp": "packages/app/e2e/actions.ts",
                        "n": 58,
                        "ex": ["setHealthPhase", "healthPhase", "defocus"],
                    },
                    {
                        "fp": "packages/opencode/src/config/config.ts",
                        "n": 54,
                        "ex": ["Agent", "Command", "Info"],
                    },
                ]
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/opencode"))

        config_index = output.index("packages/opencode/src/config/config.ts")
        e2e_index = output.index("packages/app/e2e/actions.ts")
        icons_index = output.index("packages/web/src/components/icons/index.tsx")
        self.assertLess(config_index, e2e_index)
        self.assertLess(config_index, icons_index)

    def test_project_overview_downweights_test_and_docs_directories(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 300}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 1200}]
            if op == "get_project_overview_dirs":
                return [
                    {"top_dir": "tests", "files": 140, "syms": 800},
                    {"top_dir": "pydantic_ai_slim", "files": 90, "syms": 420},
                    {"top_dir": "docs", "files": 50, "syms": 100},
                    {"top_dir": "examples", "files": 20, "syms": 70},
                ]
            if op == "get_project_overview_key_files":
                return [{"fp": "pydantic_ai_slim/pydantic_ai/providers/__init__.py", "n": 15, "ex": ["infer_provider_class"]}]
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/pydantic-ai"))

        slim_index = output.index("📂 pydantic_ai_slim/")
        tests_index = output.index("📂 tests/")
        docs_index = output.index("📂 docs/")
        self.assertLess(slim_index, tests_index)
        self.assertLess(slim_index, docs_index)

    def test_project_overview_downweights_tests_and_wrapper_scripts_in_key_files(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 140}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 480}]
            if op == "get_project_overview_dirs":
                return [
                    {"top_dir": "src", "files": 60, "syms": 220},
                    {"top_dir": "Tests", "files": 70, "syms": 240},
                ]
            if op == "get_project_overview_key_files":
                return [
                    {"fp": "Tests/AppTests.swift", "n": 300, "ex": ["AppTests"]},
                    {"fp": "mvnw.cmd", "n": 1, "ex": ["unnamed"]},
                    {"fp": "src/main/java/example/App.java", "n": 2, "ex": ["App", "main"]},
                ]
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/example"))

        self.assertIn("src/main/java/example/App.java", output)
        self.assertNotIn("Tests/AppTests.swift  (300 symbols", output)
        self.assertNotIn("inspect `mvnw.cmd` first", output)

    def test_project_overview_skips_generated_protobuf_files_for_inspect_first(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 320}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 1400}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "Libraries", "files": 280, "syms": 1200}]
            if op == "get_project_overview_key_files":
                return [
                    {
                        "fp": "Libraries/GRPC/Models/Sources/imageService/imageService.pb.swift",
                        "n": 124,
                        "ex": ["Request", "Response", "SamplerType"],
                    },
                    {
                        "fp": "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
                        "n": 28,
                        "ex": ["ImageGenerationServiceImpl", "handleGenerateImage"],
                    },
                ]
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/draw-things-community"))

        self.assertIn("Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift", output)
        self.assertNotIn("imageService.pb.swift  (124 symbols", output)
        self.assertIn(
            "inspect `Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift` first",
            output,
        )

    def test_project_overview_downweights_nested_shell_install_helpers(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 253}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 202}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "BGMApp", "files": 155, "syms": 116}]
            if op == "get_project_overview_key_files":
                return [
                    {
                        "fp": "BGMDriver/BGMDriver/quick_install.sh",
                        "n": 4,
                        "ex": ["bold_face", "get_build_path", "read_quick_install_conf"],
                    },
                    {
                        "fp": "BGMApp/BGMApp/Music Players/BGMSwinsian.m",
                        "n": 2,
                        "ex": ["unnamed"],
                    },
                ]
            if op == "apple_context_presence":
                return [{"n": 1}]
            if op == "apple_context_targets":
                return [
                    {
                        "target": "Background Music",
                        "project_file": "BGMApp/BGMApp.xcodeproj/project.pbxproj",
                        "bundled_files": 9,
                    }
                ]
            if op == "apple_context_schemes":
                return []
            if op == "apple_context_schema_labels":
                return [{"labels": ["XcodeWorkspace"]}]
            if op == "apple_context_schema_relationship_types":
                return [{"rels": ["REFERENCES_PROJECT"]}]
            if op == "apple_context_workspaces":
                return []
            if op == "cargo_context_presence":
                return [{"n": 0}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/LoomBackgroundMusic"))

        self.assertIn("inspect Apple build context first", output)
        self.assertIn("BGMApp/BGMApp/Music Players/BGMSwinsian.m", output)
        self.assertNotIn("inspect `BGMDriver/BGMDriver/quick_install.sh` first", output)
        self.assertNotIn("BGMDriver/BGMDriver/quick_install.sh  (4 symbols", output)

    def test_project_overview_prefers_java_controller_over_bootstrap_entrypoint(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_project_overview_file_count":
                return [{"files": 114}]
            if op == "get_project_overview_symbol_count":
                return [{"syms": 223}]
            if op == "get_project_overview_dirs":
                return [{"top_dir": "src", "files": 92, "syms": 42}]
            if op == "get_project_overview_key_files":
                return [
                    {
                        "fp": "src/main/java/org/springframework/samples/petclinic/PetClinicApplication.java",
                        "n": 1,
                        "ex": ["PetClinicApplication"],
                    },
                    {
                        "fp": "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
                        "n": 1,
                        "ex": ["OwnerController"],
                    },
                    {
                        "fp": "src/main/java/org/springframework/samples/petclinic/PetClinicRuntimeHints.java",
                        "n": 1,
                        "ex": ["PetClinicRuntimeHints"],
                    },
                ]
            if op == "apple_context_presence":
                return [{"n": 0}]
            if op == "cargo_context_presence":
                return [{"n": 0}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["get_project_overview"]("/tmp/spring-petclinic-upstream"))

        self.assertIn("OwnerController.java", output)
        self.assertIn(
            "inspect `src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java` first",
            output,
        )
        self.assertLess(
            output.index("OwnerController.java"),
            output.index("PetClinicApplication.java"),
        )

    def test_get_flow_summary_apple_mode_dispatches_to_apple_summary(self):
        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(
                self.module.graph_flow_summary,
                "get_apple_build_summary_impl",
                new=mock.AsyncMock(return_value="No Apple build graph paths found."),
            ) as apple_mock:
                with mock.patch.object(
                    self.module.graph_flow_summary,
                    "get_backend_flow_summary_impl",
                    new=mock.AsyncMock(return_value="backend result"),
                ) as backend_mock:
                    output = asyncio.run(
                        self.mcp.tools["get_flow_summary"](
                            "/tmp/framecreator",
                            mode="apple",
                            limit=5,
                        )
                    )

        self.assertEqual("### Flow Type: Apple Build Graph\nNo Apple build graph paths found.", output)
        apple_mock.assert_awaited_once()
        backend_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

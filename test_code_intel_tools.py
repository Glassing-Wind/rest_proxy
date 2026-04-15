import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "code_intel" / "core.py"
HELPER_PATH = REPO_ROOT / "tools" / "brain" / "code_intel" / "symbol_graph.py"
FILE_DESCRIBE_PATH = REPO_ROOT / "tools" / "brain" / "code_intel" / "file_describe.py"
REFERENCES_PATH = REPO_ROOT / "tools" / "brain" / "code_intel" / "references.py"


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

    async def execute_read(self, fn):
        return await fn(self)

    async def run(self, cypher, **params):
        return FakeResult(await CURRENT_EXECUTOR(cypher, **params))


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    async def data(self):
        return self.rows


CURRENT_EXECUTOR = None


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_code_intel_module():
    helper_spec = importlib.util.spec_from_file_location(
        "tools.brain.code_intel.symbol_graph", HELPER_PATH
    )
    helper_module = importlib.util.module_from_spec(helper_spec)
    assert helper_spec.loader is not None

    file_describe_spec = importlib.util.spec_from_file_location(
        "tools.brain.code_intel.file_describe", FILE_DESCRIBE_PATH
    )
    file_describe_module = importlib.util.module_from_spec(file_describe_spec)
    assert file_describe_spec.loader is not None

    references_spec = importlib.util.spec_from_file_location(
        "tools.brain.code_intel.references", REFERENCES_PATH
    )
    references_module = importlib.util.module_from_spec(references_spec)
    assert references_spec.loader is not None

    spec = importlib.util.spec_from_file_location("tools.brain.code_intel.core", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (None, None, None, None, None)
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    proxy_logging = types.ModuleType("proxy.logging")
    proxy_logging.debug_log = lambda *args, **kwargs: None
    ts_diag = types.ModuleType("ts_diagnostics")
    ts_diag.normalize_ts_pack_result = lambda *args, **kwargs: {}
    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP
    neo4j_mod = types.ModuleType("neo4j")

    def unit_of_work(timeout=None, metadata=None):
        def decorator(fn):
            return fn

        return decorator

    neo4j_mod.unit_of_work = unit_of_work

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    code_intel_pkg = types.ModuleType("tools.brain.code_intel")
    code_intel_pkg.__path__ = []

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "proxy.logging": proxy_logging,
            "ts_diagnostics": ts_diag,
            "mcp.server.fastmcp": mcp_mod,
            "neo4j": neo4j_mod,
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.code_intel": code_intel_pkg,
            "tools.brain.code_intel.symbol_graph": helper_module,
            "tools.brain.code_intel.file_describe": file_describe_module,
            "tools.brain.code_intel.references": references_module,
        },
    ):
        helper_spec.loader.exec_module(helper_module)
        file_describe_spec.loader.exec_module(file_describe_module)
        references_spec.loader.exec_module(references_module)
        spec.loader.exec_module(module)
    return module


class CodeIntelToolTests(unittest.TestCase):
    def setUp(self):
        self.module = load_code_intel_module()
        self.mcp = FakeMCP()
        self.module.register(self.mcp)
        self.graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        self.graph_bootstrap_mod.require_driver = _require_driver
        self.graph_bootstrap_mod._NEO4J_DB = "neo4j"
        self.graph_bootstrap_mod._NEO4J_URI = "bolt://127.0.0.1:7687"
        self.graph_bootstrap_mod._NEO4J_USER = "neo4j"
        self.graph_bootstrap_mod._NEO4J_PASSWORD = "password"
        self.module.normalize_ts_pack_result = lambda value: value

    def test_get_call_chain_prefers_backend_candidate_and_filters_public_noise(self):
        async def fake_executor(cypher, **kwargs):
            if "ORDER BY rank ASC" in cypher:
                return [
                    {
                        "eid": "1",
                        "name": "buildRouter",
                        "qualified_name": "public.buildRouter",
                        "signature": None,
                        "filepath": "src/public/assets/application-center.js",
                        "rank": 0,
                        "path_rank": 3,
                    },
                    {
                        "eid": "2",
                        "name": "buildRouter",
                        "qualified_name": "api.buildRouter",
                        "signature": None,
                        "filepath": "src/api/routes/buildRouter.ts",
                        "rank": 0,
                        "path_rank": 0,
                    },
                ]
            if "MATCH path = (start)" in cypher:
                self.assertIn("/public/", cypher)
                self.assertIn("/test/", cypher)
                self.assertIn("/gen/", cypher)
                return [
                    {
                        "chain": ["buildRouter", "leaseRouter", "LeaseService"],
                        "files": [
                            "src/api/routes/buildRouter.ts",
                            "src/api/routes/leaseRoutes.ts",
                            "src/services/leaseService.ts",
                        ],
                    }
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(
                    self.mcp.tools["get_call_chain"](
                        "/tmp/rental", "buildRouter", depth=3, direction="down"
                    )
                )
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("Resolved `buildRouter` → `api.buildRouter`", output)
        self.assertIn("leaseRouter", output)
        self.assertNotIn("application-center.js", output)

    def test_visualize_subgraph_renders_mermaid_edges(self):
        async def fake_executor(cypher, **kwargs):
            if "RETURN n.id AS id" in cypher:
                return [
                    {
                        "id": "focus-1",
                        "kind": "Function",
                        "name": "buildRouter",
                        "fp": "src/api/routes/buildRouter.ts",
                        "sl": 10,
                    }
                ]
            if "parent.id AS parent_id" in cypher:
                return [
                    {
                        "parent_id": "file-1",
                        "parent_name": "buildRouter.ts",
                        "parent_fp": "src/api/routes/buildRouter.ts",
                        "callers": [{"id": "file-2", "name": "index.ts", "fp": "src/api/index.ts"}],
                        "importers": [],
                        "callees": [
                            {
                                "id": "callee-1",
                                "name": "leaseRouter",
                                "kind": "Function",
                                "fp": "src/api/routes/leaseRoutes.ts",
                            }
                        ],
                    }
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(self.mcp.tools["visualize_subgraph"]("/tmp/rental", "buildRouter"))
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("```mermaid", output)
        self.assertIn("|contains|", output)
        self.assertIn("|calls|", output)
        self.assertIn("leaseRouter", output)

    def test_visualize_subgraph_prefers_repo_owned_symbol_over_generated_match(self):
        async def fake_executor(cypher, **kwargs):
            if "RETURN n.id AS id" in cypher:
                return [
                    {
                        "id": "focus-generated",
                        "kind": "Struct",
                        "name": "LoRA",
                        "fp": "Libraries/DataModels/PreGeneratedSPM/config_data_model_generated.swift",
                        "sl": 157,
                    },
                    {
                        "id": "focus-owned",
                        "kind": "Struct",
                        "name": "LoRA",
                        "fp": "Apps/DrawThingsCLI/DrawThingsCLI.swift",
                        "sl": 44,
                    },
                ]
            if "parent.id AS parent_id" in cypher:
                self.assertEqual(kwargs.get("fid"), "focus-owned")
                return [
                    {
                        "parent_id": "file-1",
                        "parent_name": "DrawThingsCLI.swift",
                        "parent_fp": "Apps/DrawThingsCLI/DrawThingsCLI.swift",
                        "callers": [],
                        "importers": [],
                        "callees": [],
                    }
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(self.mcp.tools["visualize_subgraph"]("/tmp/draw-things-community", "LoRA"))
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("DrawThingsCLI.swift", output)
        self.assertNotIn("config_data_model_generated.swift", output)

    def test_trace_graph_provenance_formats_parse_resolve_finalize_samples(self):
        ts_pack_mod = types.ModuleType("tree_sitter_language_pack")
        ts_pack_mod.trace_graph_provenance = lambda *args, **kwargs: {
            "project_id": "proj123",
            "parse": {
                "call_ref_samples": [
                    {
                        "caller_filepath": "src/api/routes.py",
                        "callee": "load_lease",
                        "kind": "member",
                        "receiver_hint": "lease_service",
                        "qualified_hint": "lease_service.load_lease",
                    }
                ]
            },
            "resolve": {
                "resolved_internal_samples": [
                    {
                        "src": "src/api/routes.py",
                        "dst": "src/services/lease.py",
                        "caller": "build_router",
                        "callee": "load_lease",
                        "via": "CALLS",
                    }
                ],
                "external_symbol_samples": [
                    {
                        "src": "src/api/routes.py",
                        "caller": "build_router",
                        "callee": "get",
                        "qualified_name": "requests.get",
                        "language": "python",
                    }
                ],
                "note": "Unresolved and filtered decisions remain available through index-time provenance logging.",
            },
            "finalize": {
                "calls_file_samples": [
                    {
                        "src": "src/api/routes.py",
                        "dst": "src/services/lease.py",
                        "caller": "build_router",
                        "callee": "load_lease",
                        "via": "CALLS",
                    }
                ],
                "file_graph_link_samples": [
                    {
                        "src": "src/api/routes.py",
                        "dst": "src/services/lease.py",
                        "source_rel": "CALLS_FILE",
                    }
                ],
            },
        }

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": self.graph_bootstrap_mod,
                "tree_sitter_language_pack": ts_pack_mod,
            },
        ):
            output = asyncio.run(
                self.mcp.tools["trace_graph_provenance"](
                    "/tmp/rental",
                    symbol_filter="load_lease",
                    file_filter="src/api",
                )
            )

        self.assertIn("# Graph Provenance: rental", output)
        self.assertIn("Project ID: `proj123`", output)
        self.assertIn("Symbol filter: `load_lease`", output)
        self.assertIn("File filter: `src/api`", output)
        self.assertIn("## Parse Call Samples", output)
        self.assertIn(
            "`src/api/routes.py` -> `load_lease` [member] (receiver=lease_service, qualified=lease_service.load_lease)",
            output,
        )
        self.assertIn("## Resolved Internal Samples", output)
        self.assertIn(
            "`src/api/routes.py` -> `src/services/lease.py` via `build_router -> load_lease` [CALLS]",
            output,
        )
        self.assertIn("## External Symbol Samples", output)
        self.assertIn(
            "`src/api/routes.py` external via `build_router -> get` (qualified=requests.get, language=python)",
            output,
        )
        self.assertIn("Resolve note: Unresolved and filtered decisions remain available through index-time provenance logging.", output)
        self.assertIn(
            "`src/api/routes.py` -> `src/services/lease.py` [CALLS_FILE]",
            output,
        )

    def test_visualize_subgraph_filters_wrapper_and_test_neighbors_for_monorepo_runtime_symbol(self):
        async def fake_executor(cypher, **kwargs):
            if "RETURN n.id AS id" in cypher:
                return [
                    {
                        "id": "focus-boot",
                        "kind": "Function",
                        "name": "boot",
                        "fp": "packages/opencode/src/project/instance.ts",
                        "sl": 35,
                    }
                ]
            if "parent.id AS parent_id" in cypher:
                return [
                    {
                        "parent_id": "file-1",
                        "parent_name": "instance.ts",
                        "parent_fp": "packages/opencode/src/project/instance.ts",
                        "callers": [
                            {"id": "caller-test", "name": "project-init-git.test.ts", "fp": "packages/opencode/test/server/project-init-git.test.ts"},
                            {"id": "caller-runtime", "name": "router.ts", "fp": "packages/opencode/src/server/router.ts"},
                        ],
                        "importers": [
                            {"id": "imp-test", "name": "prompt-effect.test.ts", "fp": "packages/opencode/test/session/prompt-effect.test.ts"},
                            {"id": "imp-runtime", "name": "index.ts", "fp": "packages/opencode/src/worktree/index.ts"},
                        ],
                        "callees": [
                            {"id": "callee-wrap", "name": "iife", "kind": "Function", "fp": "packages/util/src/iife.ts"},
                            {"id": "callee-ui", "name": "track", "kind": "Function", "fp": "packages/ui/src/pierre/selection-bridge.ts"},
                            {"id": "callee-runtime", "name": "restore", "kind": "Function", "fp": "packages/opencode/src/project/instance.ts"},
                        ],
                    }
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(self.mcp.tools["visualize_subgraph"]("/tmp/opencode", "boot"))
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("restore", output)
        self.assertIn("router.ts", output)
        self.assertIn("worktree/index.ts", output)
        self.assertNotIn("iife", output)
        self.assertNotIn("project-init-git.test.ts", output)
        self.assertNotIn("prompt-effect.test.ts", output)

    def test_describe_file_includes_type_alias_symbols_from_graph(self):
        async def fake_executor(cypher, **kwargs):
            if "RETURN labels(s) AS kinds" in cypher or "RETURN head([label IN labels(s)" in cypher:
                return [
                    {
                        "kind": "TypeAlias",
                        "name": "Project",
                        "start": 4,
                        "end": 14,
                        "sig": None,
                    },
                    {
                        "kind": "TypeAlias",
                        "name": "ClientOptions",
                        "start": 1,
                        "end": 3,
                        "sig": None,
                    },
                ]
            return []

        fake_pool = types.SimpleNamespace(
            connection=lambda: types.SimpleNamespace(
                __aenter__=lambda self: self,
                __aexit__=lambda self, exc_type, exc, tb: False,
                cursor=lambda: types.SimpleNamespace(
                    __aenter__=lambda self: self,
                    __aexit__=lambda self, exc_type, exc, tb: False,
                    execute=mock.AsyncMock(),
                    fetchone=mock.AsyncMock(return_value=None),
                ),
            )
        )
        fake_memory_store = types.SimpleNamespace(
            open_pool=mock.AsyncMock(),
            _pg_pool=fake_pool,
        )

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.module, "get_memory_modules", return_value=(fake_memory_store, None, None, None, None)):
                global CURRENT_EXECUTOR
                CURRENT_EXECUTOR = fake_executor
                try:
                    output = asyncio.run(
                        self.mcp.tools["describe_file"](
                            "/tmp/opencode",
                            "packages/sdk/js/src/v2/gen/types.gen.ts",
                        )
                    )
                finally:
                    CURRENT_EXECUTOR = None

        self.assertIn("[TypeAlias] Project", output)
        self.assertIn("[TypeAlias] ClientOptions", output)

    def test_get_symbol_context_prefers_repo_owned_runtime_symbol_over_generated_match(self):
        async def fake_executor(cypher, **kwargs):
            if "OPTIONAL MATCH (s)<-[:CONTAINS]-(parent:File)" in cypher:
                return [
                    {
                        "kind": "TypeAlias",
                        "name": "Config",
                        "qualified_name": None,
                        "filepath": "packages/sdk/js/src/v2/gen/types.gen.ts",
                        "start_line": 1,
                        "end_line": 12,
                        "signature": None,
                        "parent_file": "packages/sdk/js/src/v2/gen/types.gen.ts",
                        "callers": [],
                        "callees": [],
                        "callers_in": 0,
                        "callees_out": 0,
                    },
                    {
                        "kind": "Class",
                        "name": "Config",
                        "qualified_name": None,
                        "filepath": "packages/opencode/src/config/config.ts",
                        "start_line": 8,
                        "end_line": 60,
                        "signature": "class Config",
                        "parent_file": "packages/opencode/src/config/config.ts",
                        "callers": [{"name": "boot", "file": "packages/opencode/src/app/app.ts", "line": 14}],
                        "callees": [],
                        "callers_in": 3,
                        "callees_out": 1,
                    },
                ]
            return []

        fake_pool = types.SimpleNamespace(
            connection=lambda: types.SimpleNamespace(
                __aenter__=lambda self: self,
                __aexit__=lambda self, exc_type, exc, tb: False,
                cursor=lambda: types.SimpleNamespace(
                    __aenter__=lambda self: self,
                    __aexit__=lambda self, exc_type, exc, tb: False,
                    execute=mock.AsyncMock(),
                    fetchall=mock.AsyncMock(return_value=[]),
                ),
            )
        )
        fake_memory_store = types.SimpleNamespace(
            open_pool=mock.AsyncMock(),
            _pg_pool=fake_pool,
        )

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(
                self.module,
                "get_memory_modules",
                return_value=(fake_memory_store, None, None, None, None),
            ):
                global CURRENT_EXECUTOR
                CURRENT_EXECUTOR = fake_executor
                try:
                    output = asyncio.run(
                        self.mcp.tools["get_symbol_context"](
                            "/tmp/opencode",
                            "Config",
                            include_source_preview=False,
                        )
                    )
                finally:
                    CURRENT_EXECUTOR = None

        self.assertIn("packages/opencode/src/config/config.ts", output)
        self.assertNotIn("packages/sdk/js/src/v2/gen/types.gen.ts", output)

    def test_get_symbol_context_reports_ambiguous_generic_monorepo_name(self):
        async def fake_executor(cypher, **kwargs):
            if "OPTIONAL MATCH (s)<-[:CONTAINS]-(parent:File)" in cypher:
                return [
                    {
                        "kind": "TypeAlias",
                        "name": "Config",
                        "qualified_name": None,
                        "filepath": "packages/sdk/js/src/v2/gen/types.gen.ts",
                        "start_line": 1,
                        "end_line": 12,
                        "signature": None,
                        "parent_file": "packages/sdk/js/src/v2/gen/types.gen.ts",
                        "callers": [],
                        "callees": [],
                        "callers_in": 0,
                        "callees_out": 0,
                    },
                    {
                        "kind": "Class",
                        "name": "Config",
                        "qualified_name": None,
                        "filepath": "packages/sdk/js/src/gen/sdk.gen.ts",
                        "start_line": 337,
                        "end_line": 371,
                        "signature": "class Config",
                        "parent_file": "packages/sdk/js/src/gen/sdk.gen.ts",
                        "callers": [],
                        "callees": [],
                        "callers_in": 0,
                        "callees_out": 0,
                    },
                    {
                        "kind": "TypeAlias",
                        "name": "Config",
                        "qualified_name": None,
                        "filepath": "packages/plugin/src/index.ts",
                        "start_line": 38,
                        "end_line": 40,
                        "signature": None,
                        "parent_file": "packages/plugin/src/index.ts",
                        "callers": [],
                        "callees": [],
                        "callers_in": 1,
                        "callees_out": 0,
                    },
                    {
                        "kind": "TypeAlias",
                        "name": "Config",
                        "qualified_name": None,
                        "filepath": "packages/desktop-electron/src/main/cli.ts",
                        "start_line": 23,
                        "end_line": 29,
                        "signature": None,
                        "parent_file": "packages/desktop-electron/src/main/cli.ts",
                        "callers": [],
                        "callees": [],
                        "callers_in": 0,
                        "callees_out": 0,
                    },
                    {
                        "kind": "TypeAlias",
                        "name": "Config",
                        "qualified_name": None,
                        "filepath": "packages/opencode/src/control-plane/adaptors/worktree.ts",
                        "start_line": 11,
                        "end_line": 17,
                        "signature": None,
                        "parent_file": "packages/opencode/src/control-plane/adaptors/worktree.ts",
                        "callers": [],
                        "callees": [],
                        "callers_in": 2,
                        "callees_out": 0,
                    },
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(
                    self.mcp.tools["get_symbol_context"](
                        "/tmp/opencode",
                        "Config",
                        include_source_preview=False,
                    )
                )
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("Multiple exact matches found for `Config`", output)
        self.assertIn("packages/opencode/src/control-plane/adaptors/worktree.ts", output)

    def test_get_symbol_context_uses_file_path_to_disambiguate(self):
        async def fake_executor(cypher, **kwargs):
            if "OPTIONAL MATCH (s)<-[:CONTAINS]-(parent:File)" in cypher:
                self.assertEqual(kwargs.get("file_path"), "crates/ts-pack-core/src/lib.rs")
                return [
                    {
                        "kind": "Function",
                        "name": "process",
                        "qualified_name": "process",
                        "filepath": "crates/ts-pack-cli/src/main.rs",
                        "start_line": 10,
                        "end_line": 30,
                        "signature": "fn process()",
                        "parent_file": "crates/ts-pack-cli/src/main.rs",
                        "callers": [],
                        "callees": [],
                        "callers_in": 0,
                        "callees_out": 4,
                    },
                    {
                        "kind": "Function",
                        "name": "process",
                        "qualified_name": "process",
                        "filepath": "crates/ts-pack-core/src/lib.rs",
                        "start_line": 235,
                        "end_line": 244,
                        "signature": "pub fn process(source: &str, config: &ProcessConfig)",
                        "parent_file": "crates/ts-pack-core/src/lib.rs",
                        "callers": [],
                        "callees": [],
                        "callers_in": 3,
                        "callees_out": 2,
                    },
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(
                    self.mcp.tools["get_symbol_context"](
                        "/tmp/tree-sitter-language-pack",
                        "process",
                        include_source_preview=False,
                        file_path="crates/ts-pack-core/src/lib.rs",
                    )
                )
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("crates/ts-pack-core/src/lib.rs", output)
        self.assertNotIn("crates/ts-pack-cli/src/main.rs", output)

    def test_get_call_chain_summarizes_broad_fanout(self):
        async def fake_executor(cypher, **kwargs):
            if "ORDER BY rank ASC" in cypher:
                return [
                    {
                        "eid": "2",
                        "name": "registerFinanceAdminRoutes",
                        "qualified_name": None,
                        "signature": None,
                        "filepath": "src/api/routes/financeAdminRoutes.ts",
                        "rank": 0,
                        "path_rank": 0,
                    }
                ]
            if "MATCH path = (start)" in cypher:
                rows = []
                for idx in range(15):
                    rows.append(
                        {
                            "chain": ["registerFinanceAdminRoutes", f"serviceCall{idx}", f"leaf{idx}"],
                            "files": [
                                "src/api/routes/financeAdminRoutes.ts",
                                f"src/services/Service{idx}.ts",
                                f"src/services/Leaf{idx}.ts",
                            ],
                        }
                    )
                return rows
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(
                    self.mcp.tools["get_call_chain"](
                        "/tmp/rental", "registerFinanceAdminRoutes", depth=2, direction="down"
                    )
                )
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("serviceCall0", output)
        self.assertIn("… 3 more first-hop call(s) hidden", output)

    def test_get_call_chain_up_filters_unnamed_callers(self):
        async def fake_executor(cypher, **kwargs):
            if "ORDER BY rank ASC" in cypher:
                return [
                    {
                        "eid": "1",
                        "name": "loadSummary",
                        "qualified_name": "loadSummary",
                        "signature": None,
                        "filepath": "src/public/assets/financial-summary.js",
                        "rank": 0,
                        "path_rank": 3,
                    }
                ]
            if "MATCH path = (start)" in cypher:
                return [
                    {
                        "chain": ["loadSummary", "unnamed"],
                        "files": [
                            "src/public/assets/financial-summary.js",
                            "src/public/assets/financial-summary.js",
                        ],
                        "lines": [127, 183],
                    }
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(
                    self.mcp.tools["get_call_chain"](
                        "/tmp/rental",
                        "loadSummary",
                        depth=2,
                        direction="up",
                        file_path="src/public/assets/financial-summary.js",
                    )
                )
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("no named callers", output)
        self.assertNotIn("`unnamed`", output)
        self.assertIn("src/public/assets/financial-summary.js:183", output)

    def test_get_call_chain_skips_iife_and_fn_wrapper_nodes(self):
        async def fake_executor(cypher, **kwargs):
            if "ORDER BY rank ASC" in cypher:
                return [
                    {
                        "eid": "1",
                        "name": "boot",
                        "qualified_name": "boot",
                        "signature": None,
                        "filepath": "packages/opencode/src/project/instance.ts",
                        "rank": 0,
                        "path_rank": 1,
                        "callers_in": 7,
                    }
                ]
            if "MATCH path = (start)" in cypher:
                return [
                    {
                        "chain": ["boot", "iife", "fn", "loadProject"],
                        "files": [
                            "packages/opencode/src/project/instance.ts",
                            "packages/util/src/iife.ts",
                            "packages/opencode/test/snapshot/snapshot.test.ts",
                            "packages/opencode/src/project/load.ts",
                        ],
                        "lines": [12, 3, 10, 44],
                    }
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(
                    self.mcp.tools["get_call_chain"](
                        "/tmp/opencode",
                        "boot",
                        depth=3,
                        direction="down",
                    )
                )
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("`loadProject`", output)
        self.assertNotIn("`iife`", output)
        self.assertNotIn("`fn`", output)

    def test_get_call_chain_prefers_same_package_runtime_hops_for_monorepo_entrypoint(self):
        async def fake_executor(cypher, **kwargs):
            if "ORDER BY rank ASC" in cypher:
                return [
                    {
                        "eid": "1",
                        "name": "boot",
                        "qualified_name": "boot",
                        "signature": None,
                        "filepath": "packages/opencode/src/project/instance.ts",
                        "rank": 0,
                        "path_rank": 1,
                        "callers_in": 7,
                    }
                ]
            if "MATCH path = (start)" in cypher:
                return [
                    {
                        "chain": ["boot", "track", "leafTrack"],
                        "files": [
                            "packages/opencode/src/project/instance.ts",
                            "packages/ui/src/pierre/selection-bridge.ts",
                            "packages/ui/src/components/message-part.tsx",
                        ],
                        "lines": [12, 30, 44],
                    },
                    {
                        "chain": ["boot", "loadProject", "readSettings"],
                        "files": [
                            "packages/opencode/src/project/instance.ts",
                            "packages/opencode/src/project/load.ts",
                            "packages/opencode/src/config/load.ts",
                        ],
                        "lines": [12, 18, 52],
                    },
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(
                    self.mcp.tools["get_call_chain"](
                        "/tmp/opencode",
                        "boot",
                        depth=3,
                        direction="down",
                    )
                )
            finally:
                CURRENT_EXECUTOR = None

        self.assertLess(output.index("`loadProject`"), output.index("`track`"))

    def test_get_code_importance_includes_cargo_crate_context(self):
        async def fake_executor(cypher, **kwargs):
            if "f.pagerank IS NOT NULL" in cypher:
                return [
                    {
                        "file": "crates/api/src/lib.rs",
                        "sym_count": 6,
                        "sym_examples": ["run", "serve"],
                        "top_pagerank": 1.2345,
                        "score": 2.3456,
                        "betweenness": 4.0,
                        "isolated": False,
                    }
                ]
            if "CALL db.labels()" in cypher:
                return [{"labels": ["CargoCrate"]}]
            if "MATCH (c:CargoCrate" in cypher:
                return [
                    {
                        "crate": "api",
                        "crate_name": "api",
                        "manifest_path": "crates/api/Cargo.toml",
                    }
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(self.mcp.tools["get_code_importance"]("/tmp/rustws"))
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("Crate: api", output)
        self.assertIn("crates/api/src/lib.rs", output)
        self.assertIn("[crate:api]", output)
        self.assertIn("architectural leverage", output)
        self.assertIn("Use this to decide where architectural leverage", output)

    def test_get_code_communities_groups_directory_fallback_by_cargo_crate(self):
        async def fake_executor(cypher, **kwargs):
            if "f.louvainCommunity IS NOT NULL" in cypher:
                return []
            if "top_dir AS dominant_dir" in cypher:
                return [
                    {
                        "comm": None,
                        "dominant_dir": "crates",
                        "file_count": 3,
                        "total_syms": 20,
                        "top_files": ["crates/api/src/lib.rs", "crates/api/src/routes.rs"],
                    },
                    {
                        "comm": None,
                        "dominant_dir": "crates",
                        "file_count": 2,
                        "total_syms": 12,
                        "top_files": ["crates/core/src/lib.rs", "crates/core/src/service.rs"],
                    },
                ]
            if "CALL db.labels()" in cypher:
                return [{"labels": ["CargoCrate"]}]
            if "MATCH (c:CargoCrate" in cypher:
                return [
                    {"crate": "api", "crate_name": "api", "manifest_path": "crates/api/Cargo.toml"},
                    {"crate": "core", "crate_name": "core", "manifest_path": "crates/core/Cargo.toml"},
                ]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(self.mcp.tools["get_code_communities"]("/tmp/rustws"))
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("crate `api`", output)
        self.assertIn("crate `core`", output)
        self.assertIn("crates/api/src/lib.rs", output)
        self.assertIn("Use this to decide which architectural area", output)
        self.assertIn("Priority exploration order:", output)

    def test_get_code_communities_hides_singleton_zero_symbol_noise(self):
        async def fake_executor(cypher, **kwargs):
            if "f.louvainCommunity IS NOT NULL" in cypher:
                return [
                    {
                        "comm": 20,
                        "file_count": 5,
                        "total_syms": 18,
                        "top_files": ["src/api/routes.ts", "src/services/ReportService.ts"],
                    },
                    {
                        "comm": 22,
                        "file_count": 1,
                        "total_syms": 0,
                        "top_files": ["README.md"],
                    },
                ]
            if "CALL db.labels()" in cypher:
                return [{"labels": []}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(self.mcp.tools["get_code_communities"]("/tmp/repo"))
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("cluster #20", output)
        self.assertNotIn("README.md", output)

    def test_get_code_communities_downweights_ui_public_clusters(self):
        async def fake_executor(cypher, **kwargs):
            if "f.louvainCommunity IS NOT NULL" in cypher:
                return [
                    {
                        "comm": 20,
                        "file_count": 31,
                        "total_syms": 301,
                        "top_files": [
                            "src/public/applications.html",
                            "src/public/payments.html",
                            "src/public/properties.html",
                        ],
                    },
                    {
                        "comm": 18,
                        "file_count": 22,
                        "total_syms": 32,
                        "top_files": [
                            "src/api/routes.ts",
                            "src/services/PaymentMonitoringService.ts",
                            "src/db/prisma.ts",
                        ],
                    },
                ]
            if "CALL db.labels()" in cypher:
                return [{"labels": []}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(self.mcp.tools["get_code_communities"]("/tmp/repo"))
            finally:
                CURRENT_EXECUTOR = None

        backend_index = output.index("cluster #18")
        ui_index = output.index("cluster #20")
        self.assertLess(backend_index, ui_index)
        self.assertIn("[backend/app]", output)
        self.assertIn("[ui/public]", output)

    def test_get_code_communities_labels_monorepo_sdk_generated_and_cli_clusters(self):
        async def fake_executor(cypher, **kwargs):
            if "get_code_communities_louvain" in kwargs.get("op", "") or "f.louvainCommunity IS NOT NULL" in cypher:
                return [
                    {
                        "comm": 10,
                        "file_count": 2,
                        "total_syms": 900,
                        "top_files": [
                            "packages/sdk/js/src/v2/gen/types.gen.ts",
                            "packages/sdk/js/src/gen/types.gen.ts",
                        ],
                    },
                    {
                        "comm": 11,
                        "file_count": 3,
                        "total_syms": 120,
                        "top_files": [
                            "packages/opencode/src/cli/cmd/run.ts",
                            "packages/opencode/src/project/instance.ts",
                            "packages/opencode/src/provider/provider.ts",
                        ],
                    },
                ]
            if "CALL db.labels()" in cypher:
                return [{"labels": []}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(self.mcp.tools["get_code_communities"]("/tmp/opencode"))
            finally:
                CURRENT_EXECUTOR = None

        cli_index = output.index("cluster #11")
        gen_index = output.index("cluster #10")
        self.assertLess(cli_index, gen_index)
        self.assertIn("[cli/runtime]", output)
        self.assertIn("[sdk/generated]", output)

    def test_get_code_communities_hides_small_mixed_singletons_in_long_tail(self):
        async def fake_executor(cypher, **kwargs):
            if "get_code_communities_louvain" in kwargs.get("op", "") or "f.louvainCommunity IS NOT NULL" in cypher:
                return [
                    {
                        "comm": 10,
                        "file_count": 3,
                        "total_syms": 120,
                        "top_files": [
                            "packages/opencode/src/cli/cmd/run.ts",
                            "packages/opencode/src/project/instance.ts",
                            "packages/opencode/src/provider/provider.ts",
                        ],
                    },
                    {
                        "comm": 99,
                        "file_count": 1,
                        "total_syms": 2,
                        "top_files": ["script/version.ts"],
                    },
                ]
            if "CALL db.labels()" in cypher:
                return [{"labels": []}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(self.mcp.tools["get_code_communities"]("/tmp/opencode"))
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("cluster #10", output)
        self.assertNotIn("cluster #99", output)

    def test_get_code_importance_downweights_vendor_files(self):
        async def fake_executor(cypher, **kwargs):
            if "get_code_importance_pr" in kwargs.get("op", "") or "f.pagerank IS NOT NULL" in cypher:
                return [
                    {
                        "file": "Vendors/ZIPFoundation/Sources/ZIPFoundation/Data+Compression.swift",
                        "sym_count": 3,
                        "sym_examples": ["CompressionMethod", "withUnsafeBytes"],
                        "top_pagerank": 4.3,
                        "score": 7.5,
                        "betweenness": 0.0,
                        "isolated": False,
                    },
                    {
                        "file": "Apps/DrawThingsCLI/DrawThingsCLI.swift",
                        "sym_count": 41,
                        "sym_examples": ["ModelsDirectoryResolver", "ModelResolver"],
                        "top_pagerank": 0.46,
                        "score": 7.9,
                        "betweenness": 0.0,
                        "isolated": False,
                    },
                ]
            if "CALL db.labels()" in cypher:
                return [{"labels": []}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(self.mcp.tools["get_code_importance"]("/tmp/draw-things-community"))
            finally:
                CURRENT_EXECUTOR = None

        app_index = output.index("Apps/DrawThingsCLI/DrawThingsCLI.swift")
        vendor_index = output.index("Vendors/ZIPFoundation/Sources/ZIPFoundation/Data+Compression.swift")
        self.assertLess(app_index, vendor_index)

    def test_get_related_files_prefers_cargo_crate_context(self):
        async def fake_executor(cypher, **kwargs):
            if "CALL db.labels()" in cypher:
                return [{"labels": ["CargoCrate"]}]
            if "MATCH (c:CargoCrate" in cypher and "manifest_path" in cypher:
                return [
                    {"crate": "api", "crate_name": "api", "manifest_path": "crates/api/Cargo.toml"},
                    {"crate": "core", "crate_name": "core", "manifest_path": "crates/core/Cargo.toml"},
                    {"crate": "cli", "crate_name": "cli", "manifest_path": "crates/cli/Cargo.toml"},
                ]
            if "same crate" not in cypher and "MATCH (c:CargoCrate {project_id:$pid, name:$crate})-[:DEFINED_IN_FILE]" in cypher:
                return [
                    {"related_file": "crates/api/src/routes.rs", "sym_count": 8},
                    {"related_file": "crates/api/src/http.rs", "sym_count": 4},
                ]
            if "MATCH (src:CargoCrate {project_id:$pid, name:$crate})-[:DEPENDS_ON_PACKAGE]->(tgt:CargoCrate" in cypher:
                return [{"crate": "core", "files": ["crates/core/src/service.rs"]}]
            if "MATCH (src:CargoCrate {project_id:$pid})-[:DEPENDS_ON_PACKAGE]->(tgt:CargoCrate {project_id:$pid, name:$crate})" in cypher:
                return [{"crate": "cli", "files": ["crates/cli/src/main.rs"]}]
            if "MATCH (f1:File {id: $fid})-[:CONTAINS]->(imp1:Import)" in cypher:
                return [{"related_file": "crates/shared/src/types.rs", "shared_imports": 2}]
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            global CURRENT_EXECUTOR
            CURRENT_EXECUTOR = fake_executor
            try:
                output = asyncio.run(
                    self.mcp.tools["get_related_files"]("/tmp/rustws", "crates/api/src/lib.rs")
                )
            finally:
                CURRENT_EXECUTOR = None

        self.assertIn("Crate: api", output)
        self.assertIn("crates/api/src/routes.rs", output)
        self.assertIn("depends on crate `core`", output)
        self.assertIn("used by crate `cli`", output)
        self.assertIn("Import graph:", output)


if __name__ == "__main__":
    unittest.main()

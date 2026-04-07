import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/code_intel/core.py"
HELPER_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/code_intel/symbol_graph.py"
FILE_DESCRIBE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/code_intel/file_describe.py"
REFERENCES_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/code_intel/references.py"


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

        self.assertIn("crates/api/src/lib.rs", output)
        self.assertIn("[crate:api]", output)


if __name__ == "__main__":
    unittest.main()

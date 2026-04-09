import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/cross_project.py"


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


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        return None

    async def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self.rows)


class FakePool:
    def __init__(self, rows):
        self.rows = rows

    def connection(self):
        return FakeConnection(self.rows)


class FakeMemoryStore:
    def __init__(self, rows):
        self._pg_pool = FakePool(rows)

    async def open_pool(self):
        return None


def load_module(memory_store):
    spec = importlib.util.spec_from_file_location("tools.brain.search.cross_project", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (memory_store, None, None, None, None)
    helpers_mod.get_project_id = lambda workspace_id: {"src": "srcid", "tgt": "tgtid"}.get(workspace_id, workspace_id)
    helpers_mod.get_workspace_path = lambda workspace_id: {
        "src": "/Users/michaelmarler/Projects/opencode",
        "tgt": "/Users/michaelmarler/Projects/rental",
    }.get(workspace_id, workspace_id)

    class WorkspaceRegistry:
        @staticmethod
        def resolve_id(workspace_id):
            return None

    helpers_mod.WorkspaceRegistry = WorkspaceRegistry

    search_core = types.ModuleType("tools.brain.search.core")
    search_core._execute_read = mock.AsyncMock(return_value=[])

    embedding_service = types.ModuleType("embedding_service")

    class FakeEmbeddingService:
        async def embed_batch_async(self, texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

    embedding_service.get_embedding_service = lambda: FakeEmbeddingService()

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    search_pkg = types.ModuleType("tools.brain.search")
    search_pkg.__path__ = []
    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.search": search_pkg,
            "tools.brain.search.core": search_core,
            "embedding_service": embedding_service,
            "mcp.server.fastmcp": mcp_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module, search_core


class CrossProjectToolTests(unittest.TestCase):
    def setUp(self):
        self.memory_store = FakeMemoryStore([])
        self.module, self.search_core = load_module(self.memory_store)
        self.mcp = FakeMCP()
        self.module.register(self.mcp)
        self.graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        self.graph_bootstrap_mod.require_driver = _require_driver
        self.graph_bootstrap_mod._NEO4J_DB = "neo4j"

    def test_trace_symbol_cross_project_resolves_export_alias_definition(self):
        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "trace_symbol_definition":
                return []
            if op == "trace_symbol_alias_definition":
                return [
                    {
                        "kind": "ExportAlias",
                        "filepath": "packages/sdk/js/src/client.ts",
                        "start_line": 12,
                        "end_line": 12,
                        "signature": "Config",
                        "target_name": "Config",
                    }
                ]
            if op == "trace_symbol_graph_usages":
                self.assertEqual(kwargs.get("names"), ["OpencodeClientConfig", "Config"])
                return []
            return []

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["trace_symbol_cross_project"](
                        "OpencodeClientConfig",
                        "src",
                        "tgt",
                    )
                )

        self.assertIn("Kind:      ExportAlias", output)
        self.assertIn("packages/sdk/js/src/client.ts", output)
        self.assertNotIn("not found in Neo4j", output)


if __name__ == "__main__":
    unittest.main()

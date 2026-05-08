import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "cross_project.py"


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
        self.last_params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        self.last_params = params
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


def fake_embedding_module():
    embedding_service = types.ModuleType("embedding_service")

    class FakeEmbeddingService:
        async def embed_batch_async(self, texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

    embedding_service.get_embedding_service = lambda: FakeEmbeddingService()
    return embedding_service


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
            "embedding_service": fake_embedding_module(),
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

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": self.graph_bootstrap_mod,
                "embedding_service": fake_embedding_module(),
            },
        ):
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

    def test_trace_symbol_cross_project_prefers_real_definition_over_stub(self):
        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "trace_symbol_definition":
                return [
                    {
                        "kind": "Function",
                        "filepath": "crates/ts-pack-python/python/tree_sitter_language_pack/__init__.pyi",
                        "start_line": 478,
                        "end_line": 487,
                        "signature": "build_semantic_payload(...)",
                    },
                    {
                        "kind": "Function",
                        "filepath": "crates/ts-pack-python/python/tree_sitter_language_pack/_semantic_payload.py",
                        "start_line": 1137,
                        "end_line": 1180,
                        "signature": "build_semantic_payload(...)",
                    },
                ]
            if op == "trace_symbol_alias_definition":
                return []
            if op == "trace_symbol_graph_usages":
                return []
            return []

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": self.graph_bootstrap_mod,
                "embedding_service": fake_embedding_module(),
            },
        ):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["trace_symbol_cross_project"](
                        "build_semantic_payload",
                        "src",
                        "tgt",
                    )
                )

        self.assertIn("_semantic_payload.py", output)
        self.assertNotIn("__init__.pyi  L478", output)

    def test_trace_symbol_cross_project_keeps_test_like_definition_when_roles_present_empty(self):
        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "trace_symbol_definition":
                return [
                    {
                        "kind": "Function",
                        "filepath": "tests/build_semantic_payload.py",
                        "start_line": 8,
                        "end_line": 16,
                        "signature": "build_semantic_payload(...)",
                        "file_roles": [],
                    },
                    {
                        "kind": "Function",
                        "filepath": "scripts/build_semantic_payload.py",
                        "start_line": 10,
                        "end_line": 18,
                        "signature": "build_semantic_payload(...)",
                        "file_roles": None,
                    },
                ]
            if op == "trace_symbol_alias_definition":
                return []
            if op == "trace_symbol_graph_usages":
                return []
            return []

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": self.graph_bootstrap_mod,
                "embedding_service": fake_embedding_module(),
            },
        ):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["trace_symbol_cross_project"](
                        "build_semantic_payload",
                        "src",
                        "tgt",
                    )
                )

        self.assertIn("tests/build_semantic_payload.py", output)
        self.assertNotIn("scripts/build_semantic_payload.py  L10", output)

    def test_trace_symbol_cross_project_legacy_test_definition_fallback_still_applies_when_roles_missing(self):
        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "trace_symbol_definition":
                return [
                    {
                        "kind": "Function",
                        "filepath": "src/tests/build_semantic_payload.py",
                        "start_line": 8,
                        "end_line": 16,
                        "signature": "build_semantic_payload(...)",
                        "file_roles": None,
                    },
                    {
                        "kind": "Function",
                        "filepath": "scripts/build_semantic_payload.py",
                        "start_line": 10,
                        "end_line": 18,
                        "signature": "build_semantic_payload(...)",
                        "file_roles": None,
                    },
                ]
            if op == "trace_symbol_alias_definition":
                return []
            if op == "trace_symbol_graph_usages":
                return []
            return []

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": self.graph_bootstrap_mod,
                "embedding_service": fake_embedding_module(),
            },
        ):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["trace_symbol_cross_project"](
                        "build_semantic_payload",
                        "src",
                        "tgt",
                    )
                )

        self.assertIn("scripts/build_semantic_payload.py", output)
        self.assertNotIn("src/tests/build_semantic_payload.py  L8", output)

    def test_trace_symbol_cross_project_demotes_docs_definition_when_roles_are_present(self):
        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "trace_symbol_definition":
                return [
                    {
                        "kind": "Function",
                        "filepath": "docs/build_semantic_payload.md",
                        "start_line": 8,
                        "end_line": 16,
                        "signature": "build_semantic_payload(...)",
                        "file_roles": ["docs_surface"],
                    },
                    {
                        "kind": "Function",
                        "filepath": "scripts/build_semantic_payload.py",
                        "start_line": 10,
                        "end_line": 18,
                        "signature": "build_semantic_payload(...)",
                        "file_roles": [],
                    },
                ]
            if op == "trace_symbol_alias_definition":
                return []
            if op == "trace_symbol_graph_usages":
                return []
            return []

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": self.graph_bootstrap_mod,
                "embedding_service": fake_embedding_module(),
            },
        ):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["trace_symbol_cross_project"](
                        "build_semantic_payload",
                        "src",
                        "tgt",
                    )
                )

        self.assertIn("scripts/build_semantic_payload.py", output)
        self.assertNotIn("docs/build_semantic_payload.md  L8", output)

    def test_trace_symbol_cross_project_legacy_docs_definition_fallback_still_applies_when_roles_missing(self):
        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "trace_symbol_definition":
                return [
                    {
                        "kind": "Function",
                        "filepath": "docs/build_semantic_payload.md",
                        "start_line": 8,
                        "end_line": 16,
                        "signature": "build_semantic_payload(...)",
                        "file_roles": None,
                    },
                    {
                        "kind": "Function",
                        "filepath": "scripts/build_semantic_payload.py",
                        "start_line": 10,
                        "end_line": 18,
                        "signature": "build_semantic_payload(...)",
                        "file_roles": None,
                    },
                ]
            if op == "trace_symbol_alias_definition":
                return []
            if op == "trace_symbol_graph_usages":
                return []
            return []

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": self.graph_bootstrap_mod,
                "embedding_service": fake_embedding_module(),
            },
        ):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["trace_symbol_cross_project"](
                        "build_semantic_payload",
                        "src",
                        "tgt",
                    )
                )

        self.assertIn("scripts/build_semantic_payload.py", output)
        self.assertNotIn("docs/build_semantic_payload.md  L8", output)

    def test_trace_symbol_cross_project_prefers_implementation_consumer_hits(self):
        self.memory_store = FakeMemoryStore(
            [
                (
                    "test_index_workspace.py",
                    9,
                    "def test_required_symbols_exist(): build_semantic_payload",
                    {"file_roles": ["test_surface"]},
                    1000.0,
                ),
                (
                    "scripts/index_workspace.py",
                    260,
                    "def _read_and_chunk(...): build_semantic_payload(source, lang)",
                    {"file_roles": None},
                    999.0,
                ),
                (
                    "scripts/index_workspace.py",
                    359,
                    "payload = build_semantic_payload(source, lang)",
                    {"file_roles": None},
                    998.0,
                ),
            ]
        )
        self.module, self.search_core = load_module(self.memory_store)
        self.mcp = FakeMCP()
        self.module.register(self.mcp)

        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "trace_symbol_definition":
                return [
                    {
                        "kind": "Function",
                        "filepath": "crates/ts-pack-python/python/tree_sitter_language_pack/_semantic_payload.py",
                        "start_line": 1137,
                        "end_line": 1180,
                        "signature": "build_semantic_payload(...)",
                    }
                ]
            if op == "trace_symbol_alias_definition":
                return []
            if op == "trace_symbol_graph_usages":
                return []
            return []

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": self.graph_bootstrap_mod,
                "embedding_service": fake_embedding_module(),
            },
        ):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["trace_symbol_cross_project"](
                        "build_semantic_payload",
                        "src",
                        "tgt",
                    )
                )

        self.assertIn("Inspect First", output)
        self.assertIn("scripts/index_workspace.py", output)
        self.assertIn("Implementation / consumer text hits", output)
        self.assertIn("Test / supporting text hits", output)
        self.assertIn("No direct call-graph edges found.", output)

    def test_trace_symbol_cross_project_keeps_test_like_path_when_roles_present_empty(self):
        self.memory_store = FakeMemoryStore(
            [
                (
                    "tests/build_semantic_payload.py",
                    9,
                    "def build_semantic_payload_wrapper(): build_semantic_payload(source, lang)",
                    {"file_roles": []},
                    1000.0,
                ),
            ]
        )
        self.module, self.search_core = load_module(self.memory_store)
        self.mcp = FakeMCP()
        self.module.register(self.mcp)

        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "trace_symbol_definition":
                return [
                    {
                        "kind": "Function",
                        "filepath": "crates/ts-pack-python/python/tree_sitter_language_pack/_semantic_payload.py",
                        "start_line": 1137,
                        "end_line": 1180,
                        "signature": "build_semantic_payload(...)",
                    }
                ]
            if op == "trace_symbol_alias_definition":
                return []
            if op == "trace_symbol_graph_usages":
                return []
            return []

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": self.graph_bootstrap_mod,
                "embedding_service": fake_embedding_module(),
            },
        ):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["trace_symbol_cross_project"](
                        "build_semantic_payload",
                        "src",
                        "tgt",
                    )
                )

        self.assertIn("Implementation / consumer text hits", output)
        self.assertNotIn("Test / supporting text hits", output)

    def test_trace_symbol_cross_project_legacy_test_path_fallback_still_applies_when_roles_missing(self):
        self.memory_store = FakeMemoryStore(
            [
                (
                    "tests/build_semantic_payload.py",
                    9,
                    "def build_semantic_payload_wrapper(): build_semantic_payload(source, lang)",
                    None,
                    1000.0,
                ),
            ]
        )
        self.module, self.search_core = load_module(self.memory_store)
        self.mcp = FakeMCP()
        self.module.register(self.mcp)

        async def fake_execute_read(session, cypher, **kwargs):
            op = kwargs.get("op")
            if op == "trace_symbol_definition":
                return [
                    {
                        "kind": "Function",
                        "filepath": "crates/ts-pack-python/python/tree_sitter_language_pack/_semantic_payload.py",
                        "start_line": 1137,
                        "end_line": 1180,
                        "signature": "build_semantic_payload(...)",
                    }
                ]
            if op == "trace_symbol_alias_definition":
                return []
            if op == "trace_symbol_graph_usages":
                return []
            return []

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": self.graph_bootstrap_mod,
                "embedding_service": fake_embedding_module(),
            },
        ):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(
                    self.mcp.tools["trace_symbol_cross_project"](
                        "build_semantic_payload",
                        "src",
                        "tgt",
                    )
                )

        self.assertNotIn("Implementation / consumer text hits", output)
        self.assertIn("Test / supporting text hits", output)


if __name__ == "__main__":
    unittest.main()

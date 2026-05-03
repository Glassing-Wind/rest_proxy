import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "semantic.py"
HELPERS_PATH = REPO_ROOT / "tools" / "brain" / "search" / "semantic_helpers.py"
FALLBACKS_PATH = REPO_ROOT / "tools" / "brain" / "search" / "fallbacks.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeCursor:
    def __init__(self, rows_by_pid):
        self.rows_by_pid = rows_by_pid
        self._active_rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params=None):
        if params and isinstance(params, dict) and params.get("pid") in self.rows_by_pid:
            self._active_rows = list(self.rows_by_pid[params["pid"]])
        return None

    async def fetchall(self):
        return list(self._active_rows)


class FakeConnection:
    def __init__(self, rows_by_pid):
        self.rows_by_pid = rows_by_pid

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query):
        return None

    def cursor(self):
        return FakeCursor(self.rows_by_pid)


class FakePool:
    def __init__(self, rows_by_pid):
        self.rows_by_pid = rows_by_pid

    def connection(self):
        return FakeConnection(self.rows_by_pid)


class FakeMemoryStore:
    def __init__(self, rows_by_pid):
        self._pg_pool = FakePool(rows_by_pid)

    async def open_pool(self):
        return None


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def fake_embedding_module():
    embedding_service = types.ModuleType("embedding_service")

    class FakeEmbeddingService:
        async def embed_batch_async(self, texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

    embedding_service.get_embedding_service = lambda: FakeEmbeddingService()
    return embedding_service


def fake_graph_bootstrap_module():
    graph_bootstrap = types.ModuleType("graph_bootstrap")

    async def _require_driver():
        return FakeDriver()

    graph_bootstrap.require_driver = _require_driver
    graph_bootstrap._NEO4J_DB = "neo4j"
    return graph_bootstrap


def fake_mcp_modules():
    mcp_pkg = types.ModuleType("mcp")
    mcp_pkg.__path__ = []
    mcp_server_pkg = types.ModuleType("mcp.server")
    mcp_server_pkg.__path__ = []
    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP
    return {
        "mcp": mcp_pkg,
        "mcp.server": mcp_server_pkg,
        "mcp.server.fastmcp": mcp_mod,
    }


def load_module(memory_store):
    helpers_spec = importlib.util.spec_from_file_location(
        "tools.brain.search.semantic_helpers", HELPERS_PATH
    )
    helpers_module = importlib.util.module_from_spec(helpers_spec)
    assert helpers_spec.loader is not None
    helpers_spec.loader.exec_module(helpers_module)

    fallbacks_spec = importlib.util.spec_from_file_location(
        "tools.brain.search.fallbacks", FALLBACKS_PATH
    )
    fallbacks_module = importlib.util.module_from_spec(fallbacks_spec)
    assert fallbacks_spec.loader is not None
    fallbacks_spec.loader.exec_module(fallbacks_module)
    fallbacks_module.run_definition_fallback_grep = mock.AsyncMock(return_value=([], {}))
    fallbacks_module.run_member_usage_fallback_grep = mock.AsyncMock(return_value=([], {}))
    fallbacks_module.run_fallback_grep = mock.AsyncMock(return_value=([], {}))

    spec = importlib.util.spec_from_file_location("tools.brain.search.semantic", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (memory_store, None, None, None, None)
    helpers_mod.get_project_id = lambda workspace_id: {"repo": "proj123"}.get(workspace_id, workspace_id)

    class WorkspaceRegistry:
        @staticmethod
        def resolve_id(workspace_id):
            return None

    helpers_mod.WorkspaceRegistry = WorkspaceRegistry
    helpers_mod.get_workspace_path = lambda workspace_id: {
        "repo": "/tmp/repo",
    }.get(workspace_id, workspace_id)

    search_core = types.ModuleType("tools.brain.search.core")
    search_core._execute_read = mock.AsyncMock(return_value=[])

    proxy_logging = types.ModuleType("proxy.logging")
    proxy_logging.debug_log = lambda *args, **kwargs: None
    graph_bootstrap = fake_graph_bootstrap_module()

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    search_pkg = types.ModuleType("tools.brain.search")
    search_pkg.__path__ = []
    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "embedding_service": fake_embedding_module(),
            "proxy.logging": proxy_logging,
            "graph_bootstrap": graph_bootstrap,
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.search": search_pkg,
            "tools.brain.search.core": search_core,
            "tools.brain.search.semantic_helpers": helpers_module,
            "tools.brain.search.fallbacks": fallbacks_module,
            **fake_mcp_modules(),
        },
    ):
        spec.loader.exec_module(module)
    return module


class SearchCodebaseToolTests(unittest.TestCase):
    def test_search_codebase_demotes_doc_like_hits_when_metadata_enabled(self):
        rows_by_pid = {
            "proj123": [
                (
                    "docs/reference/build_semantic_payload.md",
                    0,
                    "Reference docs for build_semantic_payload",
                    "proj123",
                    {"language": "markdown", "context_path": ["Docs"]},
                    0.95,
                ),
                (
                    "scripts/index_workspace.py",
                    0,
                    "def build_semantic_payload(source, lang): pass",
                    "proj123",
                    {"language": "python", "file_symbols": ["build_semantic_payload"]},
                    0.92,
                ),
            ]
        }
        module = load_module(FakeMemoryStore(rows_by_pid))
        mcp = FakeMCP()
        module.register(mcp)

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": fake_graph_bootstrap_module(),
                "embedding_service": fake_embedding_module(),
                **fake_mcp_modules(),
            },
        ):
            output = asyncio.run(
                mcp.tools["search_codebase"](
                    ["repo"],
                    "build_semantic_payload",
                    k=3,
                    include_metadata=True,
                )
            )

        self.assertIn("--- scripts/index_workspace.py", output)
        self.assertIn("--- docs/reference/build_semantic_payload.md", output)
        self.assertLess(
            output.index("--- scripts/index_workspace.py"),
            output.index("--- docs/reference/build_semantic_payload.md"),
        )

    def test_search_codebase_applies_language_filter_and_enables_metadata_output(self):
        rows_by_pid = {
            "proj123": [
                (
                    "src/service.py",
                    0,
                    "def run_service(): pass",
                    "proj123",
                    {"language": "python", "file_symbols": ["run_service"], "context_path": ["Service"]},
                    0.90,
                ),
                (
                    "src/service.ts",
                    0,
                    "export function runService() {}",
                    "proj123",
                    {"language": "typescript", "file_symbols": ["runService"], "context_path": ["Service"]},
                    0.89,
                ),
            ]
        }
        module = load_module(FakeMemoryStore(rows_by_pid))
        mcp = FakeMCP()
        module.register(mcp)

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": fake_graph_bootstrap_module(),
                "embedding_service": fake_embedding_module(),
                **fake_mcp_modules(),
            },
        ):
            output = asyncio.run(
                mcp.tools["search_codebase"](
                    ["repo"],
                    "service entrypoint",
                    k=3,
                    languages=["python"],
                )
            )

        self.assertIn("src/service.py", output)
        self.assertNotIn("src/service.ts", output)
        self.assertIn("lang=python", output)

    def test_search_codebase_accepts_singular_workspace_id(self):
        rows_by_pid = {
            "proj123": [
                (
                    "src/service.py",
                    0,
                    "def run_service(): pass",
                    "proj123",
                    {"language": "python", "file_symbols": ["run_service"]},
                    0.90,
                ),
            ]
        }
        module = load_module(FakeMemoryStore(rows_by_pid))
        mcp = FakeMCP()
        module.register(mcp)

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": fake_graph_bootstrap_module(),
                "embedding_service": fake_embedding_module(),
                **fake_mcp_modules(),
            },
        ):
            output = asyncio.run(
                mcp.tools["search_codebase"](
                    workspace_id="repo",
                    query="run service",
                    k=3,
                )
            )

        self.assertIn("--- src/service.py", output)

    def test_search_codebase_omits_misleading_scalar_scores_in_output(self):
        rows_by_pid = {
            "proj123": [
                (
                    "src/owner_controller.py",
                    0,
                    "def owner_route(): pass",
                    "proj123",
                    {"language": "python", "file_symbols": ["owner_route"]},
                    0.10,
                ),
                (
                    "src/visit_controller.py",
                    0,
                    "def visit_route(): pass",
                    "proj123",
                    {"language": "python", "file_symbols": ["visit_route"]},
                    0.40,
                ),
            ]
        }
        module = load_module(FakeMemoryStore(rows_by_pid))
        mcp = FakeMCP()
        module.register(mcp)

        def fake_enrich(result, **kwargs):
            if result["file_path"].endswith("owner_controller.py"):
                result["rank_score"] = 5.0
            else:
                result["rank_score"] = 4.0

        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": fake_graph_bootstrap_module(),
                "embedding_service": fake_embedding_module(),
                **fake_mcp_modules(),
            },
        ):
            with mock.patch.object(module.sem_helpers, "implementation_query_intent", return_value=True):
                with mock.patch.object(module.sem_helpers, "implementation_query_class", return_value="entrypoint"):
                    with mock.patch.object(module.sem_helpers, "implementation_query_member_exprs", return_value=set()):
                        with mock.patch.object(module.sem_helpers, "implementation_query_path_hints", return_value=[]):
                            with mock.patch.object(
                                module.sem_helpers,
                                "implementation_inferred_filename_hints",
                                return_value=[],
                            ):
                                with mock.patch.object(
                                    module.sem_helpers,
                                    "implementation_expected_runtime_entrypoint_paths",
                                    return_value=[],
                                ):
                                    with mock.patch.object(
                                        module.sem_helpers,
                                        "enrich_implementation_result",
                                        side_effect=fake_enrich,
                                    ):
                                        output = asyncio.run(
                                            mcp.tools["search_codebase"](
                                                workspace_id="repo",
                                                query="where is routing implemented",
                                                k=2,
                                            )
                                        )

        first_index = output.index("--- src/owner_controller.py ---")
        second_index = output.index("--- src/visit_controller.py ---")
        self.assertLess(first_index, second_index)
        self.assertNotIn("Score:", output)


if __name__ == "__main__":
    unittest.main()

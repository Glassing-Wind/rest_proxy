import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "semantic.py"


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
        self._core_module = None
        self._store_module = None

    async def open_pool(self):
        return None

    async def search_codebase_core(self, *args, **kwargs):
        assert self._core_module is not None
        assert self._store_module is not None
        with mock.patch.dict(sys.modules, {"memory.store": self._store_module}):
            return await self._core_module.search_codebase_core(*args, **kwargs)


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

    proxy_logging = types.ModuleType("proxy.logging")
    proxy_logging.debug_log = lambda *args, **kwargs: None
    graph_bootstrap = fake_graph_bootstrap_module()

    memory_store_mod = types.ModuleType("memory.store")
    memory_store_mod._pg_pool = memory_store._pg_pool
    memory_store_mod.open_pool = memory_store.open_pool
    memory_store._store_module = memory_store_mod
    patched_modules = {
        "_helpers": helpers_mod,
        "embedding_service": fake_embedding_module(),
        "proxy.logging": proxy_logging,
        "graph_bootstrap": graph_bootstrap,
        "memory.store": memory_store_mod,
        **fake_mcp_modules(),
    }
    previous_core = sys.modules.pop("memory.code_retrieval", None)
    try:
        with mock.patch.dict(sys.modules, patched_modules):
            import memory.code_retrieval as core_module

            memory_store._core_module = core_module
            module._core_module = core_module
            spec.loader.exec_module(module)
    finally:
        sys.modules.pop("memory.code_retrieval", None)
        if previous_core is not None:
            sys.modules["memory.code_retrieval"] = previous_core
    return module


class SearchCodebaseToolTests(unittest.TestCase):
    def test_how_does_query_maps_to_implementation_explanation(self):
        module = load_module(FakeMemoryStore({}))
        self.assertEqual(
            module.sem_helpers.implementation_query_class("how does interceptor chaining work in okhttp"),
            "implementation_explanation",
        )

    def test_provider_wiring_query_infers_provider_path_hints(self):
        module = load_module(FakeMemoryStore({}))
        hints = module.sem_helpers.implementation_inferred_filename_hints(
            "how does OpenAI provider wiring work"
        )
        self.assertIn("providers/openai.py", hints)

    def test_event_flow_query_infers_pipeline_filename_hint(self):
        module = load_module(FakeMemoryStore({}))
        hints = module.sem_helpers.implementation_inferred_filename_hints(
            "how does inbound event flow work in swift-nio"
        )
        self.assertIn("channelpipeline.swift", hints)
        self.assertIn("pipeline", hints)

    def test_grpc_routing_query_infers_serviceimpl_hints(self):
        module = load_module(FakeMemoryStore({}))
        hints = module.sem_helpers.implementation_inferred_filename_hints(
            "how does gRPC server request routing work"
        )
        self.assertIn("grpc/server", hints)
        self.assertIn("server/sources", hints)
        self.assertIn("serviceimpl", hints)
        self.assertIn("serviceimpl.swift", hints)

    def test_search_codebase_rescues_impl_from_path_hints_when_primary_search_is_empty(self):
        class RescueCursor(FakeCursor):
            async def execute(self, query, params=None):
                text = str(query)
                if "WITH semantic AS" in text:
                    self._active_rows = []
                elif "matched_files AS" in text:
                    self._active_rows = [
                        (
                            "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
                            0,
                            "func handleGenerateImage(request: Request) { generateImage() }",
                            "proj123",
                            {
                                "language": "swift",
                                "file_symbols": ["handleGenerateImage", "generateImage"],
                                "declared_symbols": ["handleGenerateImage", "generateImage"],
                                "node_types": ["class_declaration", "function_definition"],
                            },
                            1,
                        )
                    ]
                else:
                    self._active_rows = []
                return None

        class RescueConnection(FakeConnection):
            def cursor(self):
                return RescueCursor(self.rows_by_pid)

        class RescuePool(FakePool):
            def connection(self):
                return RescueConnection(self.rows_by_pid)

        class RescueMemoryStore(FakeMemoryStore):
            def __init__(self):
                self._pg_pool = RescuePool({})

        module = load_module(RescueMemoryStore())
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
                    workspace_id="/Users/michaelmarler/Projects/draw-things-community",
                    query="how does gRPC server request routing work",
                    k=5,
                    include_metadata=False,
                    mode="precise",
                    fallback="none",
                    exclude_tests=True,
                )
            )

        self.assertIn("--- Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift ---", output)
        self.assertIn("handleGenerateImage", output)

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
                    meta_boost=0.0,
                )
            )

        self.assertIn("--- scripts/index_workspace.py", output)
        self.assertIn("--- docs/reference/build_semantic_payload.md", output)
        self.assertIn("meta: lang=python", output)
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

    def test_search_codebase_explanation_prefers_internal_impl_over_samples_and_public_api(self):
        rows_by_pid = {
            "proj123": [
                (
                    "okhttp/src/commonJvmAndroid/kotlin/okhttp3/Interceptor.kt",
                    0,
                    "interface Chain",
                    "proj123",
                    {
                        "language": "kotlin",
                        "file_symbols": ["Interceptor", "Chain"],
                        "declared_symbols": ["Interceptor", "Chain"],
                        "node_types": ["interface_declaration"],
                    },
                    0.30,
                ),
                (
                    "samples/guide/src/main/java/okhttp3/recipes/LoggingInterceptors.java",
                    0,
                    "class LoggingInterceptors { Response intercept(Chain chain) { return chain.proceed(request); } }",
                    "proj123",
                    {
                        "language": "java",
                        "file_symbols": ["LoggingInterceptors", "intercept"],
                        "declared_symbols": ["LoggingInterceptors", "intercept"],
                        "node_types": ["class_declaration", "method_definition"],
                    },
                    0.40,
                ),
                (
                    "okhttp/src/commonJvmAndroid/kotlin/okhttp3/internal/connection/ConnectInterceptor.kt",
                    0,
                    "object ConnectInterceptor : Interceptor { override fun intercept(chain: Interceptor.Chain): Response { return connectedChain.proceed(realChain.request) } }",
                    "proj123",
                    {
                        "language": "kotlin",
                        "file_symbols": ["ConnectInterceptor", "intercept"],
                        "declared_symbols": ["ConnectInterceptor", "intercept"],
                        "node_types": ["object_declaration", "function_definition"],
                    },
                    0.20,
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
                    query="how does interceptor chaining work in okhttp",
                    k=3,
                )
            )

        connect_index = output.index(
            "--- okhttp/src/commonJvmAndroid/kotlin/okhttp3/internal/connection/ConnectInterceptor.kt ---"
        )
        api_index = output.index("--- okhttp/src/commonJvmAndroid/kotlin/okhttp3/Interceptor.kt ---")
        sample_index = output.index(
            "--- samples/guide/src/main/java/okhttp3/recipes/LoggingInterceptors.java ---"
        )
        self.assertLess(connect_index, api_index)
        self.assertLess(connect_index, sample_index)

    def test_search_codebase_explanation_demotes_proto_schema_below_runtime_impl(self):
        rows_by_pid = {
            "proj123": [
                (
                    "Libraries/GRPC/Models/Sources/controlPanel/controlPanel.proto",
                    0,
                    'service ControlPanelService { rpc ManageGPUServer(GPUServerRequest) returns (GPUServerResponse); }',
                    "proj123",
                    {
                        "language": "proto",
                        "file_symbols": ["ControlPanelService", "ManageGPUServer"],
                        "declared_symbols": ["ControlPanelService", "ManageGPUServer"],
                        "node_types": ["service", "rpc"],
                    },
                    0.35,
                ),
                (
                    "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
                    0,
                    "public func generateImage(request: ServerRequest<ImageGenerationRequest>, context: ServerContext) async throws { try await self.handleGenerateImage(request: request.message, context: context) }",
                    "proj123",
                    {
                        "language": "swift",
                        "file_symbols": ["generateImage", "handleGenerateImage"],
                        "declared_symbols": ["generateImage", "handleGenerateImage"],
                        "node_types": ["class_declaration", "function_definition"],
                    },
                    0.30,
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
                    query="how does gRPC server request routing work",
                    k=3,
                    include_metadata=False,
                    mode="precise",
                    fallback="none",
                    exclude_tests=True,
                )
            )

        self.assertIn("--- Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift ---", output)
        self.assertNotIn("--- Libraries/GRPC/Models/Sources/controlPanel/controlPanel.proto ---", output)

    def test_search_codebase_grpc_routing_demotes_discovery_and_signing_infra(self):
        rows_by_pid = {
            "proj123": [
                (
                    "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
                    0,
                    "public func generateImage(request: ServerRequest<ImageGenerationRequest>, context: ServerContext) async throws { try await self.handleGenerateImage(request: request.message, context: context) }",
                    "proj123",
                    {
                        "language": "swift",
                        "file_symbols": ["generateImage", "handleGenerateImage"],
                        "declared_symbols": ["generateImage", "handleGenerateImage"],
                        "node_types": ["class_declaration", "function_definition"],
                    },
                    0.30,
                ),
                (
                    "Libraries/GRPC/Server/Sources/GRPCServiceBrowser.swift",
                    0,
                    "public func netServiceDidResolveAddress(_ sender: NetService) { delegate?.didFindService(descriptor) }",
                    "proj123",
                    {
                        "language": "swift",
                        "file_symbols": ["netServiceDidResolveAddress"],
                        "declared_symbols": ["netServiceDidResolveAddress"],
                        "node_types": ["class_declaration", "function_definition"],
                    },
                    0.34,
                ),
                (
                    "Libraries/GRPC/Server/Sources/GRPCServerAdvertiser.swift",
                    0,
                    "public func startAdvertising(port: Int32, TLS: Bool) { netService.publish() }",
                    "proj123",
                    {
                        "language": "swift",
                        "file_symbols": ["startAdvertising"],
                        "declared_symbols": ["startAdvertising"],
                        "node_types": ["class_declaration", "function_definition"],
                    },
                    0.33,
                ),
                (
                    "Libraries/GRPC/ProxyServer/Sources/ProxyMessageSigner.swift",
                    0,
                    "public func completeBoost(action: CompletionCode, generationId: String, amount: Int, logger: Logger) async {}",
                    "proj123",
                    {
                        "language": "swift",
                        "file_symbols": ["completeBoost"],
                        "declared_symbols": ["completeBoost"],
                        "node_types": ["class_declaration", "function_definition"],
                    },
                    0.32,
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
                    query="how does gRPC server request routing work",
                    k=4,
                    include_metadata=False,
                    mode="precise",
                    fallback="none",
                    exclude_tests=True,
                )
        )

        impl_index = output.index("--- Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift ---")
        self.assertGreaterEqual(impl_index, 0)
        self.assertNotIn("--- Libraries/GRPC/Server/Sources/GRPCServiceBrowser.swift ---", output)
        self.assertNotIn("--- Libraries/GRPC/Server/Sources/GRPCServerAdvertiser.swift ---", output)
        self.assertNotIn("--- Libraries/GRPC/ProxyServer/Sources/ProxyMessageSigner.swift ---", output)

    def test_search_codebase_provider_wiring_rescues_openai_provider_path(self):
        rows_by_pid = {
            "proj123": [
                (
                    "pydantic_ai_slim/pydantic_ai/providers/__init__.py",
                    0,
                    "def infer_provider_class(provider: str): from .openai import OpenAIProvider; return OpenAIProvider",
                    "proj123",
                    {
                        "language": "python",
                        "file_symbols": ["infer_provider_class", "infer_provider"],
                        "declared_symbols": ["infer_provider_class", "infer_provider"],
                        "node_types": ["function_definition"],
                    },
                    0.60,
                ),
                (
                    "pydantic_ai_slim/pydantic_ai/providers/litellm.py",
                    0,
                    "class LiteLLMProvider: profile = openai_model_profile(model_name)",
                    "proj123",
                    {
                        "language": "python",
                        "file_symbols": ["LiteLLMProvider", "model_profile"],
                        "declared_symbols": ["LiteLLMProvider", "model_profile"],
                        "node_types": ["class_definition", "function_definition"],
                    },
                    0.55,
                ),
            ]
        }
        module = load_module(FakeMemoryStore(rows_by_pid))
        mcp = FakeMCP()
        module.register(mcp)
        rescue_rows = [
            {
                "file_path": "pydantic_ai_slim/pydantic_ai/providers/openai.py",
                "chunk_index": 0,
                "content": "class OpenAIProvider(Provider): pass",
                "project_id": "proj123",
                "metadata": {
                    "language": "python",
                    "file_symbols": ["OpenAIProvider"],
                    "declared_symbols": ["OpenAIProvider"],
                    "node_types": ["class_definition"],
                },
                "implementation_path_hint_hit": 1,
                "rrf": 0.0,
                "_definition_rescue": True,
            }
        ]

        cr = module._core_module
        with mock.patch.dict(
            sys.modules,
            {
                "graph_bootstrap": fake_graph_bootstrap_module(),
                "embedding_service": fake_embedding_module(),
                **fake_mcp_modules(),
            },
        ):
            with mock.patch.object(
                cr.retrieval_loaders,
                "load_path_hint_rows",
                mock.AsyncMock(return_value=rescue_rows),
            ):
                output = asyncio.run(
                    mcp.tools["search_codebase"](
                        workspace_id="repo",
                        query="how does OpenAI provider wiring work",
                        k=3,
                        include_metadata=False,
                        mode="precise",
                        fallback="none",
                        exclude_tests=True,
                    )
                )

        init_index = output.index("--- pydantic_ai_slim/pydantic_ai/providers/__init__.py ---")
        openai_index = output.index("--- pydantic_ai_slim/pydantic_ai/providers/openai.py ---")
        self.assertLess(init_index, openai_index)
        self.assertNotIn("--- pydantic_ai_slim/pydantic_ai/providers/litellm.py ---", output)

    def test_grpc_routing_query_adds_noise_exclude_patterns(self):
        module = load_module(FakeMemoryStore({}))
        patterns = module.sem_helpers.implementation_noise_exclude_patterns(
            "how does gRPC server request routing work"
        )
        self.assertIn("*Browser.swift", patterns)
        self.assertIn("*Advertiser.swift", patterns)
        self.assertIn("*Signer.swift", patterns)

    def test_sparse_exact_token_query_triggers_fallback_on_collapse(self):
        rows_by_pid = {
            "proj123": [
                (
                    "src/only_file.py",
                    0,
                    "def foo(): pass",
                    "proj123",
                    {"language": "python", "file_symbols": ["foo"]},
                    0.95,
                ),
                (
                    "src/only_file.py",
                    1,
                    "def bar(): pass",
                    "proj123",
                    {"language": "python", "file_symbols": ["bar"]},
                    0.94,
                )
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
            with (
                mock.patch.object(
                    module._core_module.search_fallbacks,
                    "run_fallback_grep",
                    mock.AsyncMock(
                        return_value=(
                            ["src/fallback_match.py"],
                            {"code": 0, "count": 1},
                        )
                    ),
                ),
                mock.patch.object(
                    module._core_module.search_fallbacks,
                    "extract_fallback_tokens",
                    return_value=["foo"],
                ),
            ):
                output = asyncio.run(
                    mcp.tools["search_codebase"](
                        workspace_id="repo",
                        query="foo",
                        k=2,
                        include_metadata=False,
                        mode="precise",
                        fallback="grep",
                    )
                )

        self.assertIn("Fallback (grep):", output)
        self.assertIn("- src/fallback_match.py", output)


if __name__ == "__main__":
    unittest.main()

import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from graphrag_core.ts_pack_facts import extract_file_facts


INDEX_WORKSPACE_PATH = "/Users/michaelmarler/Projects/rest_proxy/scripts/index_workspace.py"
ASSET_GRAPH_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/asset_graph.py"
ASSET_GRAPH_APPLE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/asset_graph_apple.py"
ASSET_GRAPH_WRITE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/asset_graph_write.py"
FLOW_SUMMARY_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/flow_summary.py"
FLOW_SUMMARY_APPLE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/graph/flow_summary_apple.py"


class FakeTsPackFileFacts:
    def __init__(self, facts_by_key):
        self._facts_by_key = facts_by_key

    def extract_file_facts(self, source, language, file_path):
        return self._facts_by_key.get((language, file_path), {})


def load_index_workspace_module():
    spec = importlib.util.spec_from_file_location("index_workspace_fixture", INDEX_WORKSPACE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *args, **kwargs: None

    memory_pkg = types.ModuleType("memory")
    memory_store_mod = types.ModuleType("memory.store")
    memory_store_mod._pg_pool_available = lambda: False
    memory_store_mod._pg_pool = None
    memory_store_mod.open_pool = lambda: None
    memory_store_mod.insert_embeddings_batch = lambda **kwargs: len(kwargs.get("batch") or [])

    memory_bootstrap_mod = types.ModuleType("memory.bootstrap")
    memory_bootstrap_mod.bootstrap_schema = lambda: None

    embedding_mod = types.ModuleType("embedding_service")
    embedding_mod.get_embedding_service = lambda: None
    embedding_mod._CONCURRENCY = 2

    diagnostics_mod = types.ModuleType("ts_diagnostics")
    diagnostics_mod.normalize_ts_pack_result = lambda source, lang, raw: raw

    graphrag_pkg = types.ModuleType("graphrag_core")
    ts_pack_facts_mod = types.ModuleType("graphrag_core.ts_pack_facts")
    ts_pack_facts_mod.extract_file_facts = extract_file_facts

    with mock.patch.dict(
        sys.modules,
        {
            "dotenv": dotenv_mod,
            "memory": memory_pkg,
            "memory.store": memory_store_mod,
            "memory.bootstrap": memory_bootstrap_mod,
            "embedding_service": embedding_mod,
            "ts_diagnostics": diagnostics_mod,
            "graphrag_core": graphrag_pkg,
            "graphrag_core.ts_pack_facts": ts_pack_facts_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


class FakeCursor:
    def __init__(self, facts_by_path):
        self._facts_by_path = facts_by_path

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        return None

    def __aiter__(self):
        self._iter = iter(
            [(path, facts) for path, facts in self._facts_by_path.items()]
        )
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class FakeConnection:
    def __init__(self, facts_by_path):
        self._facts_by_path = facts_by_path

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self._facts_by_path)


class FakePool:
    def __init__(self, facts_by_path):
        self._facts_by_path = facts_by_path

    def connection(self):
        return FakeConnection(self._facts_by_path)


class FakeMemoryStore:
    def __init__(self, facts_by_path):
        self._facts_by_path = facts_by_path
        self._pg_pool = FakePool(facts_by_path)

    async def open_pool(self):
        return None

    def _pg_pool_available(self):
        return True


def load_asset_graph_module(fake_memory_store):
    apple_spec = importlib.util.spec_from_file_location("tools.brain.graph.asset_graph_apple", ASSET_GRAPH_APPLE_PATH)
    apple_module = importlib.util.module_from_spec(apple_spec)
    assert apple_spec.loader is not None
    write_spec = importlib.util.spec_from_file_location("tools.brain.graph.asset_graph_write", ASSET_GRAPH_WRITE_PATH)
    write_module = importlib.util.module_from_spec(write_spec)
    assert write_spec.loader is not None

    spec = importlib.util.spec_from_file_location("tools.brain.graph.asset_graph", ASSET_GRAPH_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")
    graph_bootstrap_mod._NEO4J_DB = "neo4j"
    
    async def _require_driver():
        return FakeDriver()

    graph_bootstrap_mod.require_driver = _require_driver

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda path: "proj123"
    helpers_mod.get_memory_modules = lambda: (fake_memory_store, None, None, None, None)
    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    graph_pkg = types.ModuleType("tools.brain.graph")
    graph_pkg.__path__ = []

    with mock.patch.dict(
        sys.modules,
        {
            "graph_bootstrap": graph_bootstrap_mod,
            "_helpers": helpers_mod,
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.graph": graph_pkg,
        },
    ):
        apple_spec.loader.exec_module(apple_module)
        sys.modules["tools.brain.graph.asset_graph_apple"] = apple_module
        write_spec.loader.exec_module(write_module)
        sys.modules["tools.brain.graph.asset_graph_write"] = write_module
        spec.loader.exec_module(module)
    return module


def load_flow_summary_module():
    apple_spec = importlib.util.spec_from_file_location(
        "tools.brain.graph.flow_summary_apple", FLOW_SUMMARY_APPLE_PATH
    )
    apple_module = importlib.util.module_from_spec(apple_spec)
    assert apple_spec.loader is not None

    spec = importlib.util.spec_from_file_location("flow_summary_fixture", FLOW_SUMMARY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: "proj123"

    graph_pkg = types.ModuleType("tools")
    brain_pkg = types.ModuleType("tools.brain")
    graph_subpkg = types.ModuleType("tools.brain.graph")
    core_mod = types.ModuleType("tools.brain.graph.core")
    core_mod._execute_read = lambda *args, **kwargs: []

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "tools": graph_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.graph": graph_subpkg,
            "tools.brain.graph.core": core_mod,
            "tools.brain.graph.flow_summary_apple": apple_module,
        },
    ):
        apple_spec.loader.exec_module(apple_module)
        spec.loader.exec_module(module)
    return module


class GraphPipelineFixtureTests(unittest.TestCase):
    def test_rental_like_fixture_stays_stable_across_pipeline_layers(self):
        index_module = load_index_workspace_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)
            html_path = project_path / "src/public/properties.html"
            js_path = project_path / "src/public/assets/properties.js"
            route_path = project_path / "src/api/leaseRoutes.ts"
            html_path.parent.mkdir(parents=True, exist_ok=True)
            js_path.parent.mkdir(parents=True, exist_ok=True)
            route_path.parent.mkdir(parents=True, exist_ok=True)

            html_path.write_text(
                '<html><head><script src="/assets/properties.js"></script></head></html>\n',
                encoding="utf-8",
            )
            js_source = 'async function submit(){ return fetch("/api/leases", { method: "POST" }); }\n'
            js_path.write_text(js_source, encoding="utf-8")
            route_source = 'router.post("/api/leases", handler)\n'
            route_path.write_text(route_source, encoding="utf-8")

            ts_pack = FakeTsPackFileFacts(
                {
                    ("typescript", "src/public/assets/properties.js"): {
                        "http_calls": [{"client": "fetch", "method": "POST", "path": "/api/leases"}]
                    },
                    ("typescript", "src/api/leaseRoutes.ts"): {
                        "route_defs": [{"framework": "express", "method": "POST", "path": "/api/leases"}]
                    },
                }
            )

            parser_facts = extract_file_facts(
                ts_pack,
                js_source,
                "typescript",
                "src/public/assets/properties.js",
            )
            self.assertEqual(
                parser_facts["http_calls"],
                [{"client": "fetch", "method": "POST", "path": "/api/leases"}],
            )

            fake_result = {
                "imports": [],
                "exports": [],
                "symbols": [{"name": "submit"}],
                "diagnostics": [],
                "metrics": {"total_lines": 1, "code_lines": 1},
                "extractions": {},
                "chunks": [
                    {
                        "content": js_source.strip(),
                        "start_byte": 0,
                        "start_line": 0,
                        "end_line": 0,
                        "metadata": {
                            "symbols_defined": ["submit"],
                            "docstrings": [],
                            "context_path": ["submit"],
                            "node_types": ["call_expression"],
                            "comments": [],
                            "has_error_nodes": False,
                        },
                    }
                ],
            }

            class FakeProcessTsPack(FakeTsPackFileFacts):
                def has_language(self, language):
                    return True

                def download(self, languages):
                    return len(languages)

                def detect_language_from_extension(self, ext):
                    return "typescript" if ext == "js" else None

                def detect_language(self, path):
                    return "typescript"

                def ProcessConfig(self, language, **kwargs):
                    return {"language": language, **kwargs}

                def process(self, source, config):
                    return fake_result

            process_ts_pack = FakeProcessTsPack(ts_pack._facts_by_key)

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": process_ts_pack}):
                with mock.patch.object(index_module, "normalize_ts_pack_result", side_effect=lambda source, lang, raw: raw):
                    chunks, reason = index_module._read_and_chunk(
                        str(js_path),
                        "src/public/assets/properties.js",
                        "proj123",
                    )

            self.assertIsNone(reason)
            self.assertEqual(len(chunks), 1)
            self.assertEqual(
                chunks[0]["metadata"]["file_facts"],
                {"http_calls": [{"client": "fetch", "method": "POST", "path": "/api/leases"}]},
            )

            fake_memory_store = FakeMemoryStore(
                {
                    "src/public/assets/properties.js": {"http_calls": [{"client": "fetch", "method": "POST", "path": "/api/leases"}]},
                    "src/api/leaseRoutes.ts": {"route_defs": [{"framework": "express", "method": "POST", "path": "/api/leases"}]},
                }
            )
            asset_module = load_asset_graph_module(fake_memory_store)
            writes = []

            async def fake_execute_read(session, query, **kwargs):
                return [
                    {"fp": "src/public/properties.html", "fid": "ui-file"},
                    {"fp": "src/public/assets/properties.js", "fid": "js-file"},
                    {"fp": "src/api/leaseRoutes.ts", "fid": "api-file"},
                ]

            async def fake_execute_write(session, query, **kwargs):
                writes.append((query, kwargs))

            result = asyncio.run(
                asset_module.build_asset_graph(
                    str(project_path),
                    fake_execute_read,
                    fake_execute_write,
                    lambda *args, **kwargs: None,
                    asyncio.Semaphore(10),
                    batch_size=100,
                    write_timeout_s=5.0,
                )
            )
            self.assertIn("CALLS_API_ROUTE edges", result)

            route_batch = next(
                kwargs["batch"]
                for query, kwargs in writes
                if "CALLS_API_ROUTE" in query and "UNWIND $batch" in query
            )
            self.assertEqual(
                route_batch,
                [{"src": "js-file", "path": "/api/leases", "method": "POST", "project_id": "proj123"}],
            )

            handler_batch = next(
                kwargs["batch"]
                for query, kwargs in writes
                if "HANDLED_BY" in query and "UNWIND $batch" in query
            )
            self.assertEqual(
                handler_batch,
                [{"path": "/api/leases", "method": "POST", "tgt": "api-file", "project_id": "proj123"}],
            )

            flow_module = load_flow_summary_module()

            async def fake_flow_execute_read(session, query, **kwargs):
                op = kwargs.get("op")
                if op == "get_app_flow_summary":
                    return [
                        {
                            "ui": "src/public/properties.html",
                            "js": "src/public/assets/properties.js",
                            "route": "POST /api/leases",
                            "api": "src/api/leaseRoutes.ts",
                            "svc": None,
                            "model": None,
                            "schema": None,
                            "external": None,
                        },
                        {
                            "ui": "src/public/properties.html",
                            "js": "src/public/assets/properties.js",
                            "route": None,
                            "api": "src/api/leaseRoutes.ts",
                            "svc": None,
                            "model": None,
                            "schema": None,
                            "external": None,
                        },
                    ]
                return []

            with mock.patch.object(flow_module.graph_core, "_execute_read", side_effect=fake_flow_execute_read):
                summary = asyncio.run(
                    flow_module.get_app_flow_summary_impl(
                        driver=FakeDriver(),
                        neo4j_db="neo4j",
                        workspace_id=str(project_path),
                        include_coverage=False,
                        limit=20,
                        group_by_ui=True,
                    )
                )

            self.assertIn("POST /api/leases", summary)
            self.assertNotIn(
                "src/public/properties.html -> src/public/assets/properties.js -> src/api/leaseRoutes.ts",
                summary,
            )


if __name__ == "__main__":
    unittest.main()

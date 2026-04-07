import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/scripts/index_workspace.py"


def load_index_workspace_module():
    spec = importlib.util.spec_from_file_location("index_workspace_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *args, **kwargs: None

    memory_pkg = types.ModuleType("memory")
    memory_store_mod = types.ModuleType("memory.store")
    memory_store_mod._pg_pool_available = lambda: False
    memory_store_mod._pg_pool = None

    async def _open_pool():
        return None

    async def _insert_embeddings_batch(**kwargs):
        return len(kwargs.get("batch") or [])

    memory_store_mod.open_pool = _open_pool
    memory_store_mod.insert_embeddings_batch = _insert_embeddings_batch

    memory_bootstrap_mod = types.ModuleType("memory.bootstrap")

    async def _bootstrap_schema():
        return None

    memory_bootstrap_mod.bootstrap_schema = _bootstrap_schema

    embedding_mod = types.ModuleType("embedding_service")

    class _EmbeddingService:
        effective_batch_size = 2
        _device = "cpu"

        async def embed_batch_async(self, texts):
            return [[0.0] for _ in texts]

    embedding_mod.get_embedding_service = lambda: _EmbeddingService()
    embedding_mod._CONCURRENCY = 2

    diagnostics_mod = types.ModuleType("ts_diagnostics")
    diagnostics_mod.normalize_ts_pack_result = lambda source, lang, raw: raw

    graphrag_pkg = types.ModuleType("graphrag_core")
    ts_pack_facts_mod = types.ModuleType("graphrag_core.ts_pack_facts")
    ts_pack_facts_mod.extract_file_facts = lambda ts_pack, source, language, file_path: {}

    stub_modules = {
        "dotenv": dotenv_mod,
        "memory": memory_pkg,
        "memory.store": memory_store_mod,
        "memory.bootstrap": memory_bootstrap_mod,
        "embedding_service": embedding_mod,
        "ts_diagnostics": diagnostics_mod,
        "graphrag_core": graphrag_pkg,
        "graphrag_core.ts_pack_facts": ts_pack_facts_mod,
    }

    with mock.patch.dict(sys.modules, stub_modules):
        spec.loader.exec_module(module)
    return module


class FakeTsPack:
    def __init__(self, result=None, detected_language="typescript"):
        self._result = result or {}
        self._detected_language = detected_language

    def has_language(self, language):
        return True

    def download(self, languages):
        return len(languages)

    def detect_language_from_extension(self, ext):
        if ext in {"ts", "tsx", "js", "jsx"}:
            return self._detected_language
        return None

    def detect_language(self, path):
        return self._detected_language

    def ProcessConfig(self, language, **kwargs):
        return {"language": language, **kwargs}

    def process(self, source, config):
        return self._result


class FakeCursor:
    def __init__(self):
        self.calls = []
        self.rowcount = 2

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        self.calls.append((query, params))


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor


class FakePool:
    def __init__(self, cursor):
        self._cursor = cursor

    def connection(self):
        return FakeConnection(self._cursor)


class IndexWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.module = load_index_workspace_module()
        self.module._TS_PACK_INIT_DONE = True

    def test_collect_ts_pack_file_meta_includes_file_facts(self):
        result = {
            "imports": [{"source": "foo", "names": ["bar"]}],
            "exports": [{"name": "Baz", "kind": "named"}],
            "symbols": [{"name": "Widget"}],
            "diagnostics": [],
            "metrics": {"total_lines": 5, "code_lines": 4},
            "extractions": {"calls": {"matches": [{"captures": [{"text": "fetch"}]}]}},
        }
        fake_ts_pack = FakeTsPack(result=result)
        file_facts = {"http_calls": [{"client": "fetch", "method": "GET", "path": "/api/items"}]}

        with mock.patch.object(self.module, "normalize_ts_pack_result", side_effect=lambda source, lang, raw: raw):
            with mock.patch.object(self.module, "extract_file_facts", return_value=file_facts):
                _, meta = self.module._collect_ts_pack_file_meta(
                    fake_ts_pack,
                    "const x = 1;",
                    "typescript",
                    "src/index.ts",
                )

        self.assertEqual(meta["file_facts"], file_facts)
        self.assertEqual(meta["file_imports"], [{"source": "foo", "names": ["bar"]}])
        self.assertIn("Widget", meta["file_symbols"])
        self.assertEqual(meta["file_metrics"]["total_lines"], 5)

    def test_should_skip_diagnostic_file_honors_env(self):
        file_meta = {"file_diagnostics": {"count": 1, "items": [{"message": "bad"}]}}
        with mock.patch.dict(os.environ, {"LM_PROXY_SKIP_DIAGNOSTIC_FILES": "1"}, clear=False):
            self.assertTrue(self.module._should_skip_diagnostic_file(file_meta))
        with mock.patch.dict(os.environ, {"LM_PROXY_SKIP_DIAGNOSTIC_FILES": "0"}, clear=False):
            self.assertFalse(self.module._should_skip_diagnostic_file(file_meta))

    def test_read_and_chunk_line_window_fallback(self):
        fake_ts_pack = types.SimpleNamespace()
        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "settings.toml"
            rel_path = "config/settings.toml"
            abs_path.write_text("\n".join(f"line {i}" for i in range(85)), encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                chunks, reason = self.module._read_and_chunk(str(abs_path), rel_path, "proj123")

        self.assertIsNone(reason)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(chunks[0]["text"].startswith(f"// File: {rel_path}\n"))
        self.assertIsNone(chunks[0]["metadata"]["language"])

    def test_read_and_chunk_native_ts_pack_propagates_file_meta(self):
        fake_result = {
            "imports": [{"source": "./api", "names": ["client"]}],
            "exports": [{"name": "GET", "kind": "named"}],
            "symbols": [{"name": "GET"}],
            "diagnostics": [],
            "metrics": {"total_lines": 4, "code_lines": 4},
            "extractions": {"calls": {"matches": [{"captures": [{"text": "fetch"}]}]}},
            "chunks": [
                {
                    "content": "await fetch('/api/items')",
                    "start_byte": 0,
                    "start_line": 0,
                    "end_line": 0,
                    "metadata": {
                        "symbols_defined": ["GET"],
                        "docstrings": [],
                        "context_path": ["GET"],
                        "node_types": ["call_expression"],
                        "comments": [],
                        "has_error_nodes": False,
                    },
                }
            ],
        }
        fake_ts_pack = FakeTsPack(result=fake_result)
        file_facts = {"route_defs": [{"framework": "file_route", "method": "GET", "path": "/api/items"}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "route.ts"
            abs_path.write_text("export async function GET() {}", encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                with mock.patch.object(self.module, "normalize_ts_pack_result", side_effect=lambda source, lang, raw: raw):
                    with mock.patch.object(self.module, "extract_file_facts", return_value=file_facts):
                        chunks, reason = self.module._read_and_chunk(
                            str(abs_path), "src/api/items/route.ts", "proj123"
                        )

        self.assertIsNone(reason)
        self.assertEqual(len(chunks), 1)
        metadata = chunks[0]["metadata"]
        self.assertEqual(metadata["language"], "typescript")
        self.assertEqual(metadata["file_facts"], file_facts)
        self.assertEqual(metadata["symbols"], ["GET"])
        self.assertEqual(metadata["file_symbols"], ["GET"])

    def test_read_and_chunk_skips_diagnostic_files(self):
        fake_result = {
            "imports": [],
            "exports": [],
            "symbols": [],
            "diagnostics": [{"message": "bad parse", "start_line": 1, "start_col": 0}],
            "metrics": {"total_lines": 1, "code_lines": 1},
            "extractions": {},
            "chunks": [],
        }
        fake_ts_pack = FakeTsPack(result=fake_result)

        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "file.ts"
            abs_path.write_text("bad(", encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                with mock.patch.dict(os.environ, {"LM_PROXY_SKIP_DIAGNOSTIC_FILES": "1"}, clear=False):
                    with mock.patch.object(self.module, "normalize_ts_pack_result", side_effect=lambda source, lang, raw: raw):
                        with mock.patch.object(self.module, "extract_file_facts", return_value={}):
                            chunks, reason = self.module._read_and_chunk(
                                str(abs_path), "src/file.ts", "proj123"
                            )

        self.assertEqual(chunks, [])
        self.assertEqual(reason, "diagnostics")

    def test_prune_ghost_chunks_uses_current_chunk_ids(self):
        cursor = FakeCursor()
        fake_pool = FakePool(cursor)
        all_chunks = [
            [
                {
                    "ref_id": "chunk-1",
                    "metadata": {"file": "src/a.ts"},
                },
                {
                    "ref_id": "chunk-2",
                    "metadata": {"file": "src/a.ts"},
                },
            ]
        ]

        with mock.patch.object(self.module.memory_store, "_pg_pool_available", return_value=True):
            with mock.patch.object(self.module.memory_store, "_pg_pool", fake_pool):
                asyncio.run(self.module._prune_ghost_chunks("proj123", all_chunks))

        self.assertEqual(len(cursor.calls), 1)
        query, params = cursor.calls[0]
        self.assertIn("DELETE FROM codebase_embeddings", query)
        self.assertEqual(params[0], "proj123")
        self.assertEqual(params[1], "src/a.ts")
        self.assertEqual(params[2], ["chunk-1", "chunk-2"])


if __name__ == "__main__":
    unittest.main()

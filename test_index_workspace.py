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

    stub_modules = {
        "dotenv": dotenv_mod,
        "memory": memory_pkg,
        "memory.store": memory_store_mod,
        "memory.bootstrap": memory_bootstrap_mod,
        "embedding_service": embedding_mod,
    }

    with mock.patch.dict(sys.modules, stub_modules):
        spec.loader.exec_module(module)
    return module


class FakeTsPack:
    def __init__(
        self,
        result=None,
        detected_language="typescript",
        payload=None,
        swift_chunks=None,
        line_window_chunks=None,
    ):
        self._result = result or {}
        self._detected_language = detected_language
        self._payload = payload
        self._swift_chunks = swift_chunks
        self._line_window_chunks = line_window_chunks

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

    def build_semantic_payload(
        self,
        source,
        language,
        file_path,
        project_id,
        *,
        chunk_id_version="v6",
        chunk_max_size=4000,
        chunk_overlap=200,
    ):
        if self._payload is not None:
            return self._payload
        return {
            "result": self._result,
            "file_meta": {},
            "chunks": [],
        }

    def build_swift_chunks(
        self,
        source,
        file_path,
        project_id,
        *,
        file_meta=None,
        chunk_id_version="v6",
        chunk_max_size=4000,
        chunk_lines=60,
        overlap_lines=10,
    ):
        if self._swift_chunks is not None:
            return self._swift_chunks
        return []

    def build_line_window_chunks(
        self,
        source,
        file_path,
        project_id,
        *,
        language=None,
        file_meta=None,
        chunk_id_version="v6",
        chunk_lines=60,
        overlap_lines=10,
    ):
        if self._line_window_chunks is not None:
            return self._line_window_chunks
        lines = source.splitlines()
        chunks = []
        i = 0
        while i < len(lines):
            block = lines[i : i + chunk_lines]
            if not block:
                break
            text = f"// File: {file_path}\n" + "\n".join(block)
            chunks.append(
                {
                    "ref_id": f"{project_id}:{chunk_id_version}:{file_path}:line-window-{i}",
                    "text": text,
                    "metadata": {
                        "file": file_path,
                        "project_id": project_id,
                        "language": language,
                        **(file_meta or {}),
                    },
                }
            )
            i += chunk_lines - overlap_lines
        return chunks


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

    def test_build_semantic_payload_forwards_chunking_config(self):
        captured = {}

        class _BuildTsPack:
            def build_semantic_payload(
                self,
                source,
                language,
                file_path,
                project_id,
                *,
                chunk_id_version="v6",
                chunk_max_size=4000,
                chunk_overlap=200,
            ):
                captured.update(
                    {
                        "source": source,
                        "language": language,
                        "file_path": file_path,
                        "project_id": project_id,
                        "chunk_id_version": chunk_id_version,
                        "chunk_max_size": chunk_max_size,
                        "chunk_overlap": chunk_overlap,
                    }
                )
                return {"file_meta": {"file_symbols": ["Widget"]}, "chunks": []}

        payload = self.module._build_semantic_payload(
            _BuildTsPack(),
            "const x = 1;",
            "typescript",
            "src/index.ts",
            "proj123",
        )

        self.assertEqual(payload["file_meta"]["file_symbols"], ["Widget"])
        self.assertEqual(captured["source"], "const x = 1;")
        self.assertEqual(captured["language"], "typescript")
        self.assertEqual(captured["file_path"], "src/index.ts")
        self.assertEqual(captured["project_id"], "proj123")
        self.assertEqual(captured["chunk_id_version"], self.module.CHUNK_ID_VERSION)
        self.assertEqual(captured["chunk_max_size"], self.module.CHUNK_MAX_BYTES)
        self.assertEqual(captured["chunk_overlap"], self.module.CHUNK_OVERLAP_BYTES)

    def test_should_skip_diagnostic_file_honors_env(self):
        file_meta = {"file_diagnostics": {"count": 1, "items": [{"message": "bad"}]}}
        with mock.patch.dict(os.environ, {"LM_PROXY_SKIP_DIAGNOSTIC_FILES": "1"}, clear=False):
            self.assertTrue(self.module._should_skip_diagnostic_file(file_meta))
        with mock.patch.dict(os.environ, {"LM_PROXY_SKIP_DIAGNOSTIC_FILES": "0"}, clear=False):
            self.assertFalse(self.module._should_skip_diagnostic_file(file_meta))

    def test_read_and_chunk_line_window_fallback(self):
        fake_ts_pack = FakeTsPack(detected_language=None)
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

    def test_read_and_chunk_xcode_metadata_uses_line_window_fallback(self):
        fake_ts_pack = FakeTsPack(detected_language=None)
        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "project.pbxproj"
            rel_path = "App.xcodeproj/project.pbxproj"
            abs_path.write_text("// !$*UTF8*$!\narchiveVersion = 1;\n", encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                chunks, reason = self.module._read_and_chunk(str(abs_path), rel_path, "proj123")

        self.assertIsNone(reason)
        self.assertEqual(len(chunks), 1)
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
        file_facts = {"route_defs": [{"framework": "file_route", "method": "GET", "path": "/api/items"}]}
        payload = {
            "result": fake_result,
            "file_meta": {
                "file_imports": [{"source": "./api", "names": ["client"]}],
                "file_exports": [{"name": "GET", "kind": "named"}],
                "file_symbols": ["GET"],
                "file_diagnostics": {"count": 0, "items": []},
                "file_metrics": {"total_lines": 4, "code_lines": 4},
                "file_extractions": {"calls": ["fetch"]},
                "file_facts": file_facts,
            },
            "chunks": [
                {
                    "ref_id": "proj123:v6:src/api/items/route.ts:abc123",
                    "text": "// File: src/api/items/route.ts\nawait fetch('/api/items')",
                    "metadata": {
                        "file": "src/api/items/route.ts",
                        "project_id": "proj123",
                        "language": "typescript",
                        "symbols": ["GET"],
                        "start_line": 1,
                        "end_line": 1,
                        "docstrings": [],
                        "context_path": ["GET"],
                        "node_types": ["call_expression"],
                        "comments": [],
                        "has_error_nodes": False,
                        "file_imports": [{"source": "./api", "names": ["client"]}],
                        "file_exports": [{"name": "GET", "kind": "named"}],
                        "file_symbols": ["GET"],
                        "file_diagnostics": {"count": 0, "items": []},
                        "file_metrics": {"total_lines": 4, "code_lines": 4},
                        "file_extractions": {"calls": ["fetch"]},
                        "file_facts": file_facts,
                    },
                }
            ],
        }
        fake_ts_pack = FakeTsPack(result=fake_result, payload=payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "route.ts"
            abs_path.write_text("export async function GET() {}", encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
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

    def test_read_and_chunk_swift_uses_package_chunker(self):
        fake_ts_pack = FakeTsPack(
            detected_language="swift",
            payload={
                "file_meta": {
                    "file_symbols": ["SidebarView"],
                    "file_diagnostics": {"count": 0, "items": []},
                },
                "chunks": [],
            },
            swift_chunks=[
                {
                    "ref_id": "proj123:v6:SidebarView.swift:swift1",
                    "text": "// File: FrameCreator/SidebarView.swift\nstruct SidebarView: View {}",
                    "metadata": {
                        "file": "FrameCreator/SidebarView.swift",
                        "project_id": "proj123",
                        "language": "swift",
                        "symbols": ["SidebarView"],
                        "start_line": 1,
                        "end_line": 1,
                        "context_path": ["SidebarView"],
                        "file_symbols": ["SidebarView"],
                        "file_diagnostics": {"count": 0, "items": []},
                    },
                }
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "SidebarView.swift"
            abs_path.write_text("struct SidebarView: View {}", encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                chunks, reason = self.module._read_and_chunk(
                    str(abs_path), "FrameCreator/SidebarView.swift", "proj123"
                )

        self.assertIsNone(reason)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["metadata"]["language"], "swift")
        self.assertEqual(chunks[0]["metadata"]["file_symbols"], ["SidebarView"])

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
        fake_ts_pack = FakeTsPack(
            result=fake_result,
            payload={
                "result": fake_result,
                "file_meta": {
                    "file_diagnostics": {
                        "count": 1,
                        "items": [{"message": "bad parse", "start_line": 1, "start_col": 0}],
                    }
                },
                "chunks": [],
            },
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "file.ts"
            abs_path.write_text("bad(", encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                with mock.patch.dict(os.environ, {"LM_PROXY_SKIP_DIAGNOSTIC_FILES": "1"}, clear=False):
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

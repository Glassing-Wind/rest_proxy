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
        sync_plan=None,
        rounds_result=None,
    ):
        self._result = result or {}
        self._detected_language = detected_language
        self._payload = payload
        self._swift_chunks = swift_chunks
        self._line_window_chunks = line_window_chunks
        self._sync_plan = sync_plan
        self._rounds_result = rounds_result

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
        _chunk_overlap=None,
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

    def build_semantic_sync_plan(self, all_chunks, existing_ids=None):
        if self._sync_plan is not None:
            return self._sync_plan
        existing_ids = existing_ids or set()
        new_chunks = [
            chunk
            for file_chunks in all_chunks
            for chunk in file_chunks
            if chunk.get("ref_id") not in existing_ids
        ]
        prune_targets = []
        for file_chunks in all_chunks:
            if not file_chunks:
                continue
            file_path = file_chunks[0].get("metadata", {}).get("file")
            if file_path:
                prune_targets.append(
                    {
                        "file_path": file_path,
                        "chunk_ids": [chunk.get("ref_id") for chunk in file_chunks if chunk.get("ref_id")],
                    }
                )
        return {
            "new_chunks": new_chunks,
            "skipped_chunks": sum(len(cs) for cs in all_chunks) - len(new_chunks),
            "prune_targets": prune_targets,
            "total_chunks": sum(len(cs) for cs in all_chunks),
        }

    async def execute_semantic_sync(self, conn, project_id, all_chunks):
        if self._sync_plan is not None:
            return self._sync_plan
        return self.build_semantic_sync_plan(all_chunks, set())

    async def execute_semantic_index_prepare(
        self,
        conn,
        project_id,
        manifest_paths,
        all_chunks,
        *,
        rebuild=False,
    ):
        if self._sync_plan is not None:
            return self._sync_plan
        return {
            **self.build_semantic_sync_plan(all_chunks, set()),
            "wiped": rebuild,
            "orphan_pruned": 0,
            "existing_ids": set(),
        }

    async def execute_semantic_index_rounds(
        self,
        new_chunks,
        *,
        batch_size,
        concurrency,
        embed_batch_fn,
        write_batch_fn,
        progress_fn=None,
    ):
        if self._rounds_result is not None:
            return self._rounds_result
        total_written = 0
        if progress_fn is not None:
            await progress_fn(
                {
                    "round_index": 0,
                    "rounds": 1,
                    "group_size": len(new_chunks),
                    "batch_count": 1,
                    "written_so_far": 0,
                    "total_new": len(new_chunks),
                    "phase": "embed_start",
                }
            )
        embedded = await embed_batch_fn(new_chunks)
        written = await write_batch_fn(embedded)
        total_written += int(written or 0)
        if progress_fn is not None:
            await progress_fn(
                {
                    "round_index": 0,
                    "rounds": 1,
                    "group_size": len(new_chunks),
                    "batch_count": 1,
                    "written_so_far": total_written,
                    "total_new": len(new_chunks),
                    "phase": "round_done",
                    "round_written": total_written,
                }
            )
        return {"written": total_written, "rounds": 1}

    async def execute_semantic_index_driver(
        self,
        conn,
        project_id,
        manifest_paths,
        all_chunks,
        *,
        rebuild=False,
        batch_size,
        concurrency,
        embed_batch_fn,
        write_batch_fn,
        progress_fn=None,
    ):
        if self._sync_plan is not None or self._rounds_result is not None:
            sync_plan = await self.execute_semantic_index_prepare(
                conn,
                project_id,
                manifest_paths,
                all_chunks,
                rebuild=rebuild,
            )
            round_result = await self.execute_semantic_index_rounds(
                sync_plan.get("new_chunks") or [],
                batch_size=batch_size,
                concurrency=concurrency,
                embed_batch_fn=embed_batch_fn,
                write_batch_fn=write_batch_fn,
                progress_fn=progress_fn,
            )
            return {**sync_plan, **round_result}
        sync_plan = await self.execute_semantic_index_prepare(
            conn,
            project_id,
            manifest_paths,
            all_chunks,
            rebuild=rebuild,
        )
        round_result = await self.execute_semantic_index_rounds(
            sync_plan.get("new_chunks") or [],
            batch_size=batch_size,
            concurrency=concurrency,
            embed_batch_fn=embed_batch_fn,
            write_batch_fn=write_batch_fn,
            progress_fn=progress_fn,
        )
        return {**sync_plan, **round_result}


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

    def test_read_and_chunk_forwards_semantic_payload_config(self):
        captured = {}

        class _BuildTsPack:
            def detect_language_from_extension(self, ext):
                return "typescript" if ext == "ts" else None

            def detect_language(self, path):
                return "typescript"

            def has_language(self, language):
                return True

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
                _chunk_overlap=None,
            ):
                if _chunk_overlap is not None:
                    chunk_overlap = _chunk_overlap
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
                return {
                    "file_meta": {"file_symbols": ["Widget"]},
                    "chunks": [
                        {
                            "ref_id": "proj123:v6:src/index.ts:abc123",
                            "text": "// File: src/index.ts\nconst x = 1;",
                            "metadata": {"file": "src/index.ts"},
                        }
                    ],
                }

        fake_ts_pack = _BuildTsPack()
        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "index.ts"
            abs_path.write_text("const x = 1;", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                chunks, reason = self.module._read_and_chunk(
                    str(abs_path),
                    "src/index.ts",
                    "proj123",
                )

        self.assertIsNone(reason)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(captured["source"], "const x = 1;")
        self.assertEqual(captured["language"], "typescript")
        self.assertEqual(captured["file_path"], "src/index.ts")
        self.assertEqual(captured["project_id"], "proj123")
        self.assertEqual(captured["chunk_id_version"], self.module.CHUNK_ID_VERSION)
        self.assertEqual(captured["chunk_max_size"], self.module.CHUNK_MAX_BYTES)
        self.assertEqual(captured["chunk_overlap"], self.module.CHUNK_OVERLAP_BYTES)

    def test_index_project_delegates_to_package_driver(self):
        payload = {
            "new_chunks": [{"ref_id": "chunk-1", "text": "hello"}],
            "skipped_chunks": 1,
            "prune_targets": [{"file_path": "src/a.ts", "chunk_ids": ["chunk-1"]}],
            "total_chunks": 1,
            "existing_ids": {"chunk-1"},
            "pruned_total": 0,
            "wiped": True,
            "orphan_pruned": 2,
        }
        fake_ts_pack = FakeTsPack(sync_plan=payload, rounds_result={"written": 3, "rounds": 2})

        manifest = [{"abs_path": "/tmp/src/a.ts", "rel_path": "src/a.ts"}]

        class _PoolConnection:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _Pool:
            def connection(self):
                return _PoolConnection()

        self.module.memory_store._pg_pool_available = lambda: True
        self.module.memory_store._pg_pool = _Pool()

        async def _bootstrap():
            return None

        async def _open_pool():
            return None

        self.module.memory_bootstrap.bootstrap_schema = _bootstrap
        self.module.memory_store.open_pool = _open_pool

        svc = types.SimpleNamespace(effective_batch_size=2, _device="cpu")
        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            with mock.patch.object(self.module, "_preflight_ts_pack", return_value=None):
                with mock.patch.object(
                    self.module,
                    "chunk_file",
                    return_value=(
                        [{"ref_id": "chunk-1", "metadata": {"file": "src/a.ts"}, "text": "hello"}],
                        None,
                    ),
                ):
                    with mock.patch.object(
                        self.module,
                        "get_embedding_service",
                        return_value=svc,
                    ):
                        result = asyncio.run(
                            self.module.index_project(
                                "/tmp/project",
                                "proj123",
                                manifest,
                                rebuild=True,
                                cleanup_only=False,
                            )
                        )

        self.assertEqual(result, 3)

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

if __name__ == "__main__":
    unittest.main()

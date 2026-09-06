import asyncio
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "scripts" / "index_workspace.py"


def load_index_workspace_module():
    module = types.ModuleType("index_workspace_under_test")
    module.__file__ = str(MODULE_PATH)
    module.__package__ = ""
    module.__dict__["__name__"] = "index_workspace_under_test"
    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *args, **kwargs: None
    neo4j_mod = types.ModuleType("neo4j")
    neo4j_mod.GraphDatabase = types.SimpleNamespace(driver=lambda *args, **kwargs: None)

    runtime_mod = types.ModuleType("_runtime")
    runtime_mod.resolve_python_runtime = lambda: {"python": sys.executable}

    memory_pkg = types.ModuleType("memory")
    memory_store_mod = types.ModuleType("memory.store")
    memory_store_mod._pg_pool_available = lambda: False
    memory_store_mod._pg_pool = None

    async def _open_pool():
        return None

    async def _insert_embeddings_batch(**kwargs):
        return len(kwargs.get("batch") or [])

    async def _link_embedding_refs(*_args, **_kwargs):
        return 0

    memory_store_mod.open_pool = _open_pool
    memory_store_mod.insert_embeddings_batch = _insert_embeddings_batch
    memory_store_mod.link_embedding_refs = _link_embedding_refs

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

    local_embeddings_mod = types.ModuleType("local_embeddings")
    local_embeddings_mod.get_lmstudio_provider = lambda: None

    stub_modules = {
        "_runtime": runtime_mod,
        "dotenv": dotenv_mod,
        "neo4j": neo4j_mod,
        "memory": memory_pkg,
        "memory.store": memory_store_mod,
        "memory.bootstrap": memory_bootstrap_mod,
        "embedding_service": embedding_mod,
        "local_embeddings": local_embeddings_mod,
    }

    with mock.patch.dict(sys.modules, stub_modules):
        source = MODULE_PATH.read_text(encoding="utf-8")
        source = source.split("# ── CLI ", 1)[0]
        exec(compile(source, str(MODULE_PATH), "exec"), module.__dict__)
    return module


class FakeTsPack:
    REQUIRED_SEMANTIC_CHUNK_FIELDS = (
        "member_usages",
        "call_like_symbols",
        "declared_symbols",
        "contains_definition",
        "contains_entrypoint",
        "chunk_role",
    )

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

    def should_use_line_window_fallback(self, file_path):
        path = (file_path or "").replace("\\", "/")
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        filename = os.path.basename(path)
        return ext in {
            "yaml",
            "yml",
            "toml",
            "json",
            "pbxproj",
            "xcscheme",
            "xcworkspacedata",
            "plist",
            "md",
            "txt",
            "sh",
            "bash",
            "zsh",
            "fish",
            "sql",
            "graphql",
            "tf",
            "hcl",
            "r",
            "jl",
        } or filename in {
            ".env",
            ".env.example",
            ".gitignore",
            ".indexignore",
        }

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
                        "member_usages": [],
                        "call_like_symbols": [],
                        "declared_symbols": [],
                        "contains_definition": False,
                        "contains_entrypoint": False,
                        "chunk_role": "context",
                        **(file_meta or {}),
                    },
                }
            )
            i += chunk_lines - overlap_lines
        return chunks

    def build_indexing_chunks(
        self,
        source,
        file_path,
        project_id,
        *,
        language=None,
        chunk_id_version="v6",
        chunk_max_size=4000,
        chunk_overlap=200,
        chunk_lines=60,
        overlap_lines=10,
    ):
        file_meta = {}
        chunks = []
        if language == "swift":
            try:
                payload = self.build_semantic_payload(
                    source,
                    "swift",
                    file_path,
                    project_id,
                    chunk_id_version=chunk_id_version,
                    chunk_max_size=chunk_max_size,
                    chunk_overlap=chunk_overlap,
                )
                file_meta = payload.get("file_meta") or {}
            except Exception:
                file_meta = {}
            chunks = self.build_swift_chunks(
                source,
                file_path,
                project_id,
                file_meta=file_meta,
                chunk_id_version=chunk_id_version,
                chunk_max_size=chunk_max_size,
                chunk_lines=chunk_lines,
                overlap_lines=overlap_lines,
            )
            if chunks:
                return {"language": language, "file_meta": file_meta, "chunks": chunks}
        elif language:
            payload = self.build_semantic_payload(
                source,
                language,
                file_path,
                project_id,
                chunk_id_version=chunk_id_version,
                chunk_max_size=chunk_max_size,
                chunk_overlap=chunk_overlap,
            )
            file_meta = payload.get("file_meta") or {}
            chunks = payload.get("chunks") or []
            if chunks:
                return {"language": language, "file_meta": file_meta, "chunks": chunks}

        if language is None and not self.should_use_line_window_fallback(file_path):
            return {"language": language, "file_meta": file_meta, "chunks": []}

        chunks = self.build_line_window_chunks(
            source,
            file_path,
            project_id,
            language=language,
            file_meta=file_meta,
            chunk_id_version=chunk_id_version,
            chunk_lines=chunk_lines,
            overlap_lines=overlap_lines,
        )
        return {"language": language, "file_meta": file_meta, "chunks": chunks}

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

    def process_semantic_manifest_entries(
        self,
        manifest_entries,
        project_id,
        *,
        max_file_bytes=1_000_000,
        chunk_id_version="v6",
        chunk_max_size=4000,
        chunk_overlap=200,
        chunk_lines=60,
        overlap_lines=10,
        skip_diagnostic_files=False,
    ):
        results = []
        for entry in manifest_entries:
            results.append(
                {
                    "chunks": [
                        {
                            "ref_id": f"{project_id}:{chunk_id_version}:{entry['rel_path']}:native",
                            "text": "hello",
                            "metadata": {
                                "file": entry["rel_path"],
                                "project_id": project_id,
                                "member_usages": [],
                                "call_like_symbols": [],
                                "declared_symbols": [],
                                "contains_definition": False,
                                "contains_entrypoint": False,
                                "chunk_role": "context",
                            },
                        }
                    ],
                    "reason": None,
                }
            )
        return results

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
    def __init__(self, rows=None):
        self.calls = []
        self.rowcount = 2
        self._rows = list(rows or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        self.calls.append((query, params))

    async def fetchall(self):
        return list(self._rows)


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
    def test_refresh_semantic_chunk_metadata_updates_stale_rows_without_embeddings(self):
        class FakeCursor:
            def __init__(self):
                self.calls = []
                self.rowcount = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, query, params):
                payload = json.loads(params[0])
                self.calls.append((query, params, payload))
                self.rowcount = len(payload)

            async def fetchall(self):
                return [(f"chunk-{index}",) for index in range(self.rowcount)]

        class FakeConnection:
            def __init__(self):
                self.cursor_instance = FakeCursor()

            def cursor(self):
                return self.cursor_instance

        conn = FakeConnection()
        chunks = [
            [
                {
                    "ref_id": "proj:v6:a.py:1",
                    "metadata": {
                        "file_roles": ["test_surface"],
                        "semantic_contract_version": self.module.SEMANTIC_CONTRACT_VERSION,
                    },
                },
                {
                    "ref_id": "proj:v6:b.py:1",
                    "metadata": {
                        "file_roles": ["service_surface"],
                        "semantic_contract_version": self.module.SEMANTIC_CONTRACT_VERSION,
                    },
                },
            ],
            [
                {
                    "ref_id": "proj:v6:c.py:1",
                    "metadata": {
                        "file_roles": [],
                        "semantic_contract_version": self.module.SEMANTIC_CONTRACT_VERSION,
                    },
                }
            ],
        ]

        refreshed = asyncio.run(
            self.module._refresh_semantic_chunk_metadata(
                conn, "proj", chunks, batch_size=2
            )
        )

        self.assertEqual(refreshed, 3)
        self.assertEqual(len(conn.cursor_instance.calls), 2)
        first_query, first_params, first_payload = conn.cursor_instance.calls[0]
        self.assertIn("UPDATE codebase_embeddings", first_query)
        self.assertIn("RETURNING existing.chunk_id", first_query)
        self.assertEqual(first_params[1], "proj")
        self.assertEqual(len(first_params), 2)
        self.assertEqual(first_payload[0]["metadata"]["file_roles"], ["test_surface"])

    def setUp(self):
        self.module = load_index_workspace_module()
        self.module._TS_PACK_INIT_DONE = True

    def test_promote_semantic_file_roles_to_graph_skips_legacy_rows_without_emitted_roles(self):
        self.module.memory_store._pg_pool_available = lambda: True
        self.module.memory_store._pg_pool = FakePool(
            FakeCursor(
                [
                    ("src/generated.ts", True, ["Generated_Surface", " support_surface "]),
                    ("src/empty.ts", True, []),
                    ("src/legacy.ts", False, []),
                ]
            )
        )
        captured = {}

        class _Result:
            def single(self):
                return {"matched": 2}

        class _Session:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def run(self, query, **kwargs):
                captured["query"] = query
                captured["kwargs"] = kwargs
                return _Result()

        class _Driver:
            def session(self, database=None):
                captured["database"] = database
                return _Session()

            def close(self):
                captured["closed"] = True

        class _GraphDatabase:
            @staticmethod
            def driver(uri, auth=None):
                captured["uri"] = uri
                captured["auth"] = auth
                return _Driver()

        neo4j_mod = types.SimpleNamespace(GraphDatabase=_GraphDatabase)

        with mock.patch.dict(sys.modules, {"neo4j": neo4j_mod}):
            asyncio.run(
                self.module._promote_semantic_file_roles_to_graph(
                    "proj123",
                    ["src/generated.ts", "src/empty.ts", "src/legacy.ts"],
                )
            )

        self.assertEqual(
            captured["kwargs"]["batch"],
            [
                {"filepath": "src/generated.ts", "roles": ["Generated_Surface", "support_surface"]},
                {"filepath": "src/empty.ts", "roles": []},
            ],
        )
        self.assertEqual(captured["kwargs"]["pid"], "proj123")
        self.assertTrue(captured["closed"])

    def test_get_latest_successful_struct_run_id_ignores_stale_project_pointer(self):
        captured = {}

        class _Record(dict):
            pass

        class _Result:
            def single(self):
                return _Record(run_id="struct-new")

        class _Session:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def run(self, cypher, **params):
                captured["cypher"] = cypher
                captured["params"] = params
                return _Result()

        class _Driver:
            def session(self, database=None):
                captured["database"] = database
                return _Session()

            def close(self):
                captured["closed"] = True

        class _GraphDatabase:
            @staticmethod
            def driver(uri, auth=None):
                captured["uri"] = uri
                captured["auth"] = auth
                return _Driver()

        neo4j_mod = types.SimpleNamespace(GraphDatabase=_GraphDatabase)

        with mock.patch.dict(sys.modules, {"neo4j": neo4j_mod}):
            run_id = self.module._get_latest_successful_struct_run_id("proj123")

        self.assertEqual(run_id, "struct-new")
        self.assertEqual(captured["params"], {"pid": "proj123"})
        self.assertIn("MATCH (sr:IndexRun {project_id:$pid, phase:'struct'})", captured["cypher"])
        self.assertIn("WHERE sr.status = 'done'", captured["cypher"])
        self.assertIn("ORDER BY coalesce(sr.finished_at, sr.started_at, 0) DESC, sr.id DESC", captured["cypher"])
        self.assertNotIn("struct_active_run_id", captured["cypher"])
        self.assertTrue(captured["closed"])

    def test_read_and_chunk_forwards_semantic_payload_config(self):
        captured = {}

        class _BuildTsPack:
            REQUIRED_SEMANTIC_CHUNK_FIELDS = FakeTsPack.REQUIRED_SEMANTIC_CHUNK_FIELDS

            def detect_language_from_extension(self, ext):
                return "typescript" if ext == "ts" else None

            def detect_language(self, path):
                return "typescript"

            def has_language(self, language):
                return True

            def should_use_line_window_fallback(self, file_path):
                return False

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
                            "metadata": {
                                "file": "src/index.ts",
                                "member_usages": [],
                                "call_like_symbols": [],
                                "declared_symbols": [],
                                "contains_definition": False,
                                "contains_entrypoint": False,
                                "chunk_role": "context",
                            },
                        }
                    ],
                }

            def build_indexing_chunks(
                self,
                source,
                file_path,
                project_id,
                *,
                language=None,
                chunk_id_version="v6",
                chunk_max_size=4000,
                chunk_overlap=200,
                chunk_lines=60,
                overlap_lines=10,
            ):
                return self.build_semantic_payload(
                    source,
                    language,
                    file_path,
                    project_id,
                    chunk_id_version=chunk_id_version,
                    chunk_max_size=chunk_max_size,
                    chunk_overlap=chunk_overlap,
                )

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
            "new_chunks": [
                {"ref_id": "chunk-1", "text": "hello"},
                {"ref_id": "chunk-2", "text": "world"},
                {"ref_id": "chunk-3", "text": "!"},
            ],
            "skipped_chunks": 0,
            "prune_targets": [{"file_path": "src/a.ts", "chunk_ids": ["chunk-1", "chunk-2", "chunk-3"]}],
            "total_chunks": 3,
            "existing_ids": set(),
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
        self.module._get_latest_successful_struct_run_id = lambda _pid: "struct-new"

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

    def test_index_project_marks_partial_semantic_completion_failed(self):
        payload = {
            "new_chunks": [
                {"ref_id": "chunk-1", "text": "hello"},
                {"ref_id": "chunk-2", "text": "world"},
            ],
            "skipped_chunks": 0,
            "prune_targets": [{"file_path": "src/a.ts", "chunk_ids": ["chunk-1", "chunk-2"]}],
            "total_chunks": 2,
            "existing_ids": set(),
            "pruned_total": 0,
            "wiped": False,
            "orphan_pruned": 0,
        }
        fake_ts_pack = FakeTsPack(sync_plan=payload, rounds_result={"written": 1, "rounds": 1})
        manifest = [{"abs_path": "/tmp/src/a.ts", "rel_path": "src/a.ts"}]
        statuses = []

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
        self.module.memory_store.link_embedding_refs = mock.AsyncMock(return_value=1)

        async def _bootstrap():
            return None

        async def _open_pool():
            return None

        self.module.memory_bootstrap.bootstrap_schema = _bootstrap
        self.module.memory_store.open_pool = _open_pool
        self.module._get_latest_successful_struct_run_id = lambda _pid: "struct-new"

        svc = types.SimpleNamespace(effective_batch_size=2, _device="cpu")

        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            with mock.patch.object(self.module, "_preflight_ts_pack", return_value=None):
                with mock.patch.object(
                    self.module,
                    "chunk_file",
                    return_value=(
                        [
                            {"ref_id": "chunk-1", "metadata": {"file": "src/a.ts"}, "text": "hello"},
                            {"ref_id": "chunk-2", "metadata": {"file": "src/a.ts"}, "text": "world"},
                        ],
                        None,
                    ),
                ):
                    with mock.patch.object(self.module, "get_embedding_service", return_value=svc):
                        with mock.patch.object(
                            self.module,
                            "_set_semantic_run_status",
                            side_effect=lambda *args, **kwargs: statuses.append((args, kwargs)),
                        ):
                            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                                result = asyncio.run(
                                    self.module.index_project(
                                        "/tmp/project",
                                        "proj123",
                                        manifest,
                                        rebuild=False,
                                        cleanup_only=False,
                                    )
                                )

        self.assertEqual(result, 1)
        self.assertFalse(self.module._LAST_INDEX_PROJECT_OK)
        self.assertTrue(statuses)
        self.assertEqual(statuses[-1][0][2], "failed")
        self.assertIn("semantic_partial_completion", statuses[-1][1]["error"])
        self.assertIn("semantic_partial_completion", stderr.getvalue())

    def test_index_project_preserves_failed_status_on_semantic_driver_error(self):
        payload = {
            "new_chunks": [{"ref_id": "chunk-1", "text": "hello"}],
            "skipped_chunks": 0,
            "prune_targets": [{"file_path": "src/a.ts", "chunk_ids": ["chunk-1"]}],
            "total_chunks": 1,
            "existing_ids": set(),
            "pruned_total": 0,
            "wiped": True,
            "orphan_pruned": 0,
        }
        fake_ts_pack = FakeTsPack(sync_plan=payload)
        manifest = [{"abs_path": "/tmp/src/a.ts", "rel_path": "src/a.ts"}]
        statuses = []

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
        self.module._get_latest_successful_struct_run_id = lambda _pid: "struct-new"

        svc = types.SimpleNamespace(effective_batch_size=2, _device="cpu")

        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            with mock.patch.object(self.module, "_preflight_ts_pack", return_value=None):
                with mock.patch.object(
                    fake_ts_pack,
                    "execute_semantic_index_driver",
                    side_effect=RuntimeError("LM Studio request failed"),
                ):
                    with mock.patch.object(
                        self.module,
                        "chunk_file",
                        return_value=(
                            [{"ref_id": "chunk-1", "metadata": {"file": "src/a.ts"}, "text": "hello"}],
                            None,
                        ),
                    ):
                        with mock.patch.object(self.module, "get_embedding_service", return_value=svc):
                            with mock.patch.object(
                                self.module,
                                "_set_semantic_run_status",
                                side_effect=lambda *args, **kwargs: statuses.append((args, kwargs)),
                            ):
                                with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                                    result = asyncio.run(
                                        self.module.index_project(
                                            "/tmp/project",
                                            "proj123",
                                            manifest,
                                            rebuild=True,
                                            cleanup_only=False,
                                        )
                                    )

        self.assertEqual(result, 0)
        self.assertTrue(statuses)
        self.assertEqual(statuses[-1][0][2], "failed")
        self.assertIn("LM Studio request failed", statuses[-1][1]["error"])
        self.assertIn("LM Studio request failed", stderr.getvalue())

    def test_should_skip_diagnostic_file_honors_env(self):
        file_meta = {"file_diagnostics": {"count": 1, "items": [{"message": "bad"}]}}
        with mock.patch.dict(os.environ, {"LM_PROXY_SKIP_DIAGNOSTIC_FILES": "1"}, clear=False):
            self.assertTrue(self.module._should_skip_diagnostic_file(file_meta))
        with mock.patch.dict(os.environ, {"LM_PROXY_SKIP_DIAGNOSTIC_FILES": "0"}, clear=False):
            self.assertFalse(self.module._should_skip_diagnostic_file(file_meta))

    def test_write_buffer_can_defer_neo4j_links(self):
        captured = {}

        async def _insert_embeddings_batch(**kwargs):
            captured["kwargs"] = kwargs
            return len(kwargs.get("batch") or [])

        self.module.memory_store.insert_embeddings_batch = _insert_embeddings_batch
        deferred_ref_ids = []

        written = asyncio.run(
            self.module._write_buffer(
                [{"ref_id": "chunk-1", "text": "hello", "vector": [0.0], "metadata": {}}],
                "/tmp/project",
                "proj123",
                defer_link_refs=True,
                deferred_ref_ids=deferred_ref_ids,
            )
        )

        self.assertEqual(written, 1)
        self.assertEqual(deferred_ref_ids, ["chunk-1"])
        self.assertFalse(captured["kwargs"]["link_refs"])

    def test_index_project_uses_larger_write_batch_than_embed_batch(self):
        fake_ts_pack = FakeTsPack(sync_plan={"new_chunks": [], "skipped_chunks": 0, "prune_targets": [], "total_chunks": 0, "existing_ids": set(), "wiped": False, "orphan_pruned": 0}, rounds_result={"written": 0, "rounds": 0})
        manifest = [{"abs_path": "/tmp/src/a.ts", "rel_path": "src/a.ts"}]
        captured = {}

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
        self.module.memory_store.link_embedding_refs = mock.AsyncMock(return_value=0)

        async def _bootstrap():
            return None

        async def _open_pool():
            return None

        self.module.memory_bootstrap.bootstrap_schema = _bootstrap
        self.module.memory_store.open_pool = _open_pool
        self.module._get_latest_successful_struct_run_id = lambda _pid: "struct-new"

        svc = types.SimpleNamespace(effective_batch_size=2, _device="cpu")

        async def _driver(conn, project_id, manifest_paths, all_chunks, **kwargs):
            captured["batch_size"] = kwargs["batch_size"]
            return {"new_chunks": [], "skipped_chunks": 0, "pruned_total": 0, "existing_ids": set(), "wiped": False, "orphan_pruned": 0, "written": 0, "rounds": 0}

        fake_ts_pack.execute_semantic_index_driver = _driver

        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            with mock.patch.dict(os.environ, {"LM_PROXY_PG_WRITE_BATCH_SIZE": "16"}, clear=False):
                with mock.patch.object(self.module, "_preflight_ts_pack", return_value=None):
                    with mock.patch.object(
                        self.module,
                        "chunk_file",
                        return_value=([{"ref_id": "chunk-1", "metadata": {"file": "src/a.ts"}, "text": "hello"}], None),
                    ):
                        with mock.patch.object(self.module, "get_embedding_service", return_value=svc):
                            result = asyncio.run(
                                self.module.index_project(
                                    "/tmp/project",
                                    "proj123",
                                    manifest,
                                    rebuild=False,
                                    cleanup_only=False,
                                )
                            )

        self.assertEqual(result, 0)
        self.assertEqual(captured["batch_size"], 2)

    def test_index_project_prefers_native_manifest_chunk_processor(self):
        fake_ts_pack = FakeTsPack(
            sync_plan={
                "new_chunks": [{"ref_id": "chunk-1", "text": "hello"}],
                "skipped_chunks": 0,
                "prune_targets": [{"file_path": "src/a.ts", "chunk_ids": ["chunk-1"]}],
                "total_chunks": 1,
                "existing_ids": set(),
                "wiped": False,
                "orphan_pruned": 0,
            },
            rounds_result={"written": 1, "rounds": 1},
        )
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
        self.module.memory_store.link_embedding_refs = mock.AsyncMock(return_value=0)

        async def _bootstrap():
            return None

        async def _open_pool():
            return None

        self.module.memory_bootstrap.bootstrap_schema = _bootstrap
        self.module.memory_store.open_pool = _open_pool
        self.module._get_latest_successful_struct_run_id = lambda _pid: "struct-new"

        svc = types.SimpleNamespace(effective_batch_size=2, _device="cpu")

        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            with mock.patch.object(self.module, "_preflight_ts_pack", return_value=None):
                with mock.patch.object(
                    self.module,
                    "chunk_file",
                    side_effect=AssertionError("legacy chunk_file should not run"),
                ):
                    with mock.patch.object(self.module, "get_embedding_service", return_value=svc):
                        result = asyncio.run(
                            self.module.index_project(
                                "/tmp/project",
                                "proj123",
                                manifest,
                                rebuild=False,
                                cleanup_only=False,
                            )
                        )

        self.assertEqual(result, 1)

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
                        "member_usages": [],
                        "call_like_symbols": ["fetch"],
                        "declared_symbols": [],
                        "contains_definition": False,
                        "contains_entrypoint": False,
                        "chunk_role": "usage",
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
        self.assertGreaterEqual(len(chunks), 1)
        usage_chunks = [chunk for chunk in chunks if chunk["metadata"].get("chunk_role") == "usage"]
        self.assertEqual(len(usage_chunks), 1)
        metadata = usage_chunks[0]["metadata"]
        self.assertEqual(metadata["language"], "typescript")
        self.assertEqual(metadata["file_facts"], file_facts)
        self.assertEqual(metadata["symbols"], ["GET"])
        self.assertEqual(metadata["file_symbols"], ["GET"])
        self.assertEqual(metadata["call_like_symbols"], ["fetch"])
        self.assertEqual(metadata["chunk_role"], "usage")

    def test_read_and_chunk_accepts_ts_pack_usage_metadata(self):
        payload = {
            "file_meta": {"file_symbols": ["run_example"]},
            "chunks": [
                {
                    "ref_id": "proj123:v6:examples/python_smoke/main.py:abc123",
                    "text": "// File: examples/python_smoke/main.py\nresult = parser.parse(source, None)",
                    "metadata": {
                        "file": "examples/python_smoke/main.py",
                        "project_id": "proj123",
                        "language": "python",
                        "symbols": ["run_example"],
                        "start_line": 1,
                        "end_line": 1,
                        "node_types": ["call_expression"],
                        "member_usages": ["parser.parse"],
                        "call_like_symbols": ["parser.parse", "parse"],
                        "declared_symbols": [],
                        "contains_definition": False,
                        "contains_entrypoint": False,
                        "chunk_role": "example_usage",
                    },
                }
            ],
        }
        fake_ts_pack = FakeTsPack(detected_language="python", payload=payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "main.py"
            abs_path.write_text("result = parser.parse(source, None)", encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                chunks, reason = self.module._read_and_chunk(
                    str(abs_path), "examples/python_smoke/main.py", "proj123"
                )

        self.assertIsNone(reason)
        metadata = chunks[0]["metadata"]
        self.assertEqual(metadata["member_usages"], ["parser.parse"])
        self.assertIn("parse", metadata["call_like_symbols"])
        self.assertEqual(metadata["chunk_role"], "example_usage")

    def test_read_and_chunk_accepts_ts_pack_definition_metadata(self):
        payload = {
            "file_meta": {"file_symbols": ["GET"]},
            "chunks": [
                {
                    "ref_id": "proj123:v6:src/api/items/route.ts:abc123",
                    "text": "// File: src/api/items/route.ts\nexport async function GET() {}",
                    "metadata": {
                        "file": "src/api/items/route.ts",
                        "project_id": "proj123",
                        "language": "typescript",
                        "symbols": ["GET"],
                        "start_line": 1,
                        "end_line": 1,
                        "node_types": ["function_declaration"],
                        "member_usages": [],
                        "call_like_symbols": ["GET"],
                        "declared_symbols": ["GET"],
                        "contains_definition": True,
                        "contains_entrypoint": False,
                        "chunk_role": "definition",
                    },
                }
            ],
        }
        fake_ts_pack = FakeTsPack(detected_language="typescript", payload=payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "route.ts"
            abs_path.write_text("export async function GET() {}", encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                chunks, reason = self.module._read_and_chunk(
                    str(abs_path), "src/api/items/route.ts", "proj123"
                )

        self.assertIsNone(reason)
        metadata = chunks[0]["metadata"]
        self.assertEqual(metadata["declared_symbols"], ["GET"])
        self.assertTrue(metadata["contains_definition"])
        self.assertFalse(metadata["contains_entrypoint"])

    def test_read_and_chunk_accepts_ts_pack_test_usage_metadata(self):
        payload = {
            "file_meta": {"file_symbols": ["test_parse"]},
            "chunks": [
                {
                    "ref_id": "proj123:v6:e2e/python/tests/test_parsing.py:abc123",
                    "text": "// File: e2e/python/tests/test_parsing.py\nresult = parser.parse(source, None)",
                    "metadata": {
                        "file": "e2e/python/tests/test_parsing.py",
                        "project_id": "proj123",
                        "language": "python",
                        "symbols": ["test_parse"],
                        "start_line": 1,
                        "end_line": 1,
                        "node_types": ["call_expression"],
                        "member_usages": ["parser.parse"],
                        "call_like_symbols": ["parser.parse", "parse"],
                        "declared_symbols": [],
                        "contains_definition": False,
                        "contains_entrypoint": False,
                        "chunk_role": "test_usage",
                    },
                }
            ],
        }
        fake_ts_pack = FakeTsPack(detected_language="python", payload=payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "test_parsing.py"
            abs_path.write_text("result = parser.parse(source, None)", encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                chunks, reason = self.module._read_and_chunk(
                    str(abs_path), "e2e/python/tests/test_parsing.py", "proj123"
                )

        self.assertIsNone(reason)
        metadata = chunks[0]["metadata"]
        self.assertEqual(metadata["chunk_role"], "test_usage")

    def test_read_and_chunk_accepts_ts_pack_entrypoint_anchor_chunk(self):
        payload = {
            "file_meta": {"file_symbols": ["configure_display_backend", "main"]},
            "chunks": [
                {
                    "ref_id": "proj123:v6:packages/desktop/src-tauri/src/main.rs:abc123",
                    "text": (
                        "// File: packages/desktop/src-tauri/src/main.rs\n"
                        "#![cfg_attr(not(debug_assertions), windows_subsystem = \"windows\")]\n"
                        "\n"
                        "fn configure_display_backend() -> Option<String> { None }\n"
                    ),
                    "metadata": {
                        "file": "packages/desktop/src-tauri/src/main.rs",
                        "project_id": "proj123",
                        "language": "rust",
                        "symbols": ["configure_display_backend"],
                        "start_line": 1,
                        "end_line": 4,
                        "node_types": ["function_item"],
                        "member_usages": [],
                        "call_like_symbols": ["configure_display_backend"],
                        "declared_symbols": ["configure_display_backend"],
                        "contains_definition": True,
                        "contains_entrypoint": False,
                        "chunk_role": "definition",
                    },
                },
                {
                    "ref_id": "proj123:v6:packages/desktop/src-tauri/src/main.rs:def-main",
                    "text": (
                        "// File: packages/desktop/src-tauri/src/main.rs\n"
                        "fn main() {\n"
                        "    configure_display_backend();\n"
                        "}\n"
                    ),
                    "metadata": {
                        "file": "packages/desktop/src-tauri/src/main.rs",
                        "project_id": "proj123",
                        "language": "rust",
                        "symbols": ["main"],
                        "start_line": 5,
                        "end_line": 7,
                        "node_types": ["function_item"],
                        "member_usages": [],
                        "call_like_symbols": ["main", "configure_display_backend"],
                        "declared_symbols": ["main"],
                        "contains_definition": True,
                        "contains_entrypoint": True,
                        "chunk_role": "definition",
                    },
                }
            ],
        }
        fake_ts_pack = FakeTsPack(detected_language="rust", payload=payload)
        source = (
            "#![cfg_attr(not(debug_assertions), windows_subsystem = \"windows\")]\n"
            "\n"
            "fn configure_display_backend() -> Option<String> { None }\n"
            "\n"
            "fn main() {\n"
            "    configure_display_backend();\n"
            "}\n"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "main.rs"
            abs_path.write_text(source, encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                chunks, reason = self.module._read_and_chunk(
                    str(abs_path), "packages/desktop/src-tauri/src/main.rs", "proj123"
                )

        self.assertIsNone(reason)
        self.assertGreaterEqual(len(chunks), 2)
        anchor_chunks = [
            chunk for chunk in chunks if chunk["metadata"].get("contains_entrypoint")
        ]
        self.assertEqual(len(anchor_chunks), 1)
        anchor = anchor_chunks[0]
        self.assertIn("fn main()", anchor["text"])
        self.assertEqual(anchor["metadata"]["declared_symbols"], ["main"])
        self.assertTrue(anchor["metadata"]["contains_definition"])
        self.assertEqual(anchor["metadata"]["chunk_role"], "definition")

    def test_read_and_chunk_rejects_missing_semantic_chunk_metadata(self):
        payload = {
            "file_meta": {"file_symbols": ["run_example"]},
            "chunks": [
                {
                    "ref_id": "proj123:v6:examples/python_smoke/main.py:abc123",
                    "text": "// File: examples/python_smoke/main.py\nresult = parser.parse(source, None)",
                    "metadata": {
                        "file": "examples/python_smoke/main.py",
                        "project_id": "proj123",
                        "language": "python",
                    },
                }
            ],
        }
        fake_ts_pack = FakeTsPack(detected_language="python", payload=payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = Path(tmpdir) / "main.py"
            abs_path.write_text("result = parser.parse(source, None)", encoding="utf-8")

            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
                with self.assertRaisesRegex(ValueError, "semantic chunk contract violation"):
                    self.module._read_and_chunk(
                        str(abs_path), "examples/python_smoke/main.py", "proj123"
                    )

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
                        "member_usages": [],
                        "call_like_symbols": [],
                        "declared_symbols": ["SidebarView"],
                        "contains_definition": True,
                        "contains_entrypoint": False,
                        "chunk_role": "definition",
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

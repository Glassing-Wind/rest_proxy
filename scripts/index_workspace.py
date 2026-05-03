#!/usr/bin/env python3
"""Semantic indexing worker for GraphRAG pipeline.

Consumes a JSON manifest of source files, chunks them with ts-pack-owned
helpers, calls LM Studio for embeddings, and drives semantic Postgres sync
through the native ts-pack semantic index driver.

Usage:
    python scripts/index_workspace.py <target_dir> <project_id> --manifest-file <path>
"""

import os
import sys
import asyncio
import inspect
import json
import time
import threading
import uuid
from collections import Counter
from typing import List, Dict, Tuple

# Import memory/embedding modules AFTER env vars are set
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from _runtime import resolve_python_runtime


def _ensure_runtime_dependencies() -> None:
    try:
        import dotenv  # noqa: F401
        import neo4j  # noqa: F401
    except ModuleNotFoundError:
        if os.environ.get("LM_PROXY_RUNTIME_REEXECED") == "1":
            raise
        runtime = resolve_python_runtime()
        preferred = str(runtime.get("python") or "")
        if not preferred or os.path.realpath(preferred) == os.path.realpath(sys.executable):
            raise
        os.environ["LM_PROXY_RUNTIME_REEXECED"] = "1"
        os.execv(preferred, [preferred, __file__, *sys.argv[1:]])


_ensure_runtime_dependencies()

from dotenv import load_dotenv

load_dotenv(os.path.join(REPO_ROOT, ".env"))

# This worker's only job is codebase semantic indexing. Keep the proxy-level
# default conservative, but make direct indexing writes explicit in this process.
os.environ.setdefault("LM_PROXY_MEMORY_ENABLE_EMBEDDINGS", "1")

import memory.store as memory_store
import memory.bootstrap as memory_bootstrap
from embedding_service import get_embedding_service
from local_embeddings import get_lmstudio_provider
# AST-chunk size: target upper bound for native ts_pack chunks.
CHUNK_MAX_BYTES = 4_000  # bytes — passed through to ts_pack helpers
# Overlap between adjacent AST chunks (bytes). Keep small to avoid duplication.
CHUNK_OVERLAP_BYTES = 200
# Chunk id version for forward-compatible re-indexing.
CHUNK_ID_VERSION = "v6"
# Line-window fallback for files ts_pack cannot parse (config/docs/data).
CHUNK_LINES = 60  # lines per window
OVERLAP_LINES = 10  # overlap between windows
MANIFEST_BATCH = 50  # files per interleaved cycle
MAX_FILE_BYTES = 1_000_000  # skip source files > 1 MB
CHUNK_CONCURRENCY = max(1, int(os.getenv("LM_PROXY_CHUNK_CONCURRENCY", "64")))
USE_NATIVE_SEMANTIC_DRIVER = os.getenv("LM_PROXY_NATIVE_SEMANTIC_DRIVER", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEFER_NEO4J_EMBED_LINKS = os.getenv("LM_PROXY_DEFER_NEO4J_EMBED_LINKS", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEFER_NEO4J_LINK_BATCH_SIZE = max(
    1, int(os.getenv("LM_PROXY_DEFER_NEO4J_LINK_BATCH_SIZE", "4096"))
)
TS_PACK_AUTO_DOWNLOAD = os.getenv("LM_PROXY_TS_PACK_AUTO_DOWNLOAD", "1") == "1"
TS_PACK_CACHE_DIR = os.getenv("LM_PROXY_TS_PACK_CACHE_DIR")

_TS_PACK_INIT_DONE = False
_TS_PACK_INIT_LOCK = threading.Lock()
_LAST_INDEX_PROJECT_OK = True

# Minimal extraction patterns for languages where queries are stable.
# Extractions: keep small and stable (query syntax varies by grammar).
_EXTRACTIONS_BY_LANG = {
    "python": {
        "calls": {
            "query": "(call function: (identifier) @name)",
            "capture_output": "Text",
        },
        "decorators": {
            "query": "(decorator (identifier) @name)",
            "capture_output": "Text",
        },
    },
    "javascript": {
        "calls": {
            "query": "(call_expression function: (identifier) @name)",
            "capture_output": "Text",
        },
        "decorators": {
            "query": "(decorator (identifier) @name)",
            "capture_output": "Text",
        },
    },
    "typescript": {
        "calls": {
            "query": "(call_expression function: (identifier) @name)",
            "capture_output": "Text",
        },
        "decorators": {
            "query": "(decorator (identifier) @name)",
            "capture_output": "Text",
        },
    },
    "tsx": {
        "calls": {
            "query": "(call_expression function: (identifier) @name)",
            "capture_output": "Text",
        },
        "decorators": {
            "query": "(decorator (identifier) @name)",
            "capture_output": "Text",
        },
    },
}

def _semantic_chunk_required_fields(ts_pack) -> set[str]:
    fields = getattr(ts_pack, "REQUIRED_SEMANTIC_CHUNK_FIELDS", None)
    if not fields:
        raise ValueError("ts_pack semantic contract export missing REQUIRED_SEMANTIC_CHUNK_FIELDS")
    return {str(field) for field in fields}


def _validate_semantic_chunk_contract(chunks: List[Dict], file_path: str, ts_pack) -> None:
    if not chunks:
        return
    required_fields = _semantic_chunk_required_fields(ts_pack)
    for index, chunk in enumerate(chunks):
        metadata = chunk.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError(
                f"ts_pack semantic chunk contract violation for {file_path} chunk {index}: missing metadata"
            )
        missing = sorted(required_fields - set(metadata.keys()))
        if missing:
            raise ValueError(
                f"ts_pack semantic chunk contract violation for {file_path} chunk {index}: "
                f"missing fields {', '.join(missing)}"
            )


def _skip_diagnostic_files_enabled() -> bool:
    return os.getenv("LM_PROXY_SKIP_DIAGNOSTIC_FILES", "").lower() in (
        "1",
        "true",
        "yes",
    )


def _ensure_ts_pack_initialized() -> None:
    global _TS_PACK_INIT_DONE
    if _TS_PACK_INIT_DONE:
        return
    with _TS_PACK_INIT_LOCK:
        if _TS_PACK_INIT_DONE:
            return
        if TS_PACK_CACHE_DIR:
            try:
                import tree_sitter_language_pack as ts_pack

                ts_pack.init({"cache_dir": TS_PACK_CACHE_DIR})
            except Exception as exc:
                print(
                    f"[lm-proxy:indexer] ts_pack init failed ({exc})",
                    file=sys.stderr,
                    flush=True,
                )
        _TS_PACK_INIT_DONE = True


def _preflight_ts_pack(manifest: List[Dict]) -> None:
    if not TS_PACK_AUTO_DOWNLOAD:
        return
    try:
        import tree_sitter_language_pack as ts_pack
    except Exception as exc:
        print(
            f"[lm-proxy:indexer] ts_pack import failed ({exc})",
            file=sys.stderr,
            flush=True,
        )
        return

    _ensure_ts_pack_initialized()

    detected: set[str] = set()
    for entry in manifest:
        ext = (entry.get("ext") or "").lower().lstrip(".")
        rel_path = entry.get("rel_path") or entry.get("abs_path") or ""
        if getattr(ts_pack, "should_use_line_window_fallback", None) and ts_pack.should_use_line_window_fallback(rel_path):
            continue
        lang = None
        try:
            lang = ts_pack.detect_language_from_extension(ext)
        except Exception:
            lang = None
        if not lang:
            abs_path = entry.get("abs_path")
            if abs_path:
                try:
                    lang = ts_pack.detect_language(abs_path)
                except Exception:
                    lang = None
        if lang:
            detected.add(lang)

    if not detected:
        return

    missing = [lang for lang in sorted(detected) if not ts_pack.has_language(lang)]
    if not missing:
        return

    print(
        f"[lm-proxy:indexer] ts_pack preflight — downloading {len(missing)} languages",
        file=sys.stderr,
        flush=True,
    )
    try:
        ts_pack.download(missing)
    except Exception as exc:
        print(
            f"[lm-proxy:indexer] ts_pack download failed ({exc})",
            file=sys.stderr,
            flush=True,
        )
        return

    still_missing = [lang for lang in missing if not ts_pack.has_language(lang)]
    if still_missing:
        print(
            f"[lm-proxy:indexer] ts_pack missing after download: {', '.join(still_missing)}",
            file=sys.stderr,
            flush=True,
        )


# ── Chunk-ID helper ───────────────────────────────────────────────────────────


def _should_skip_diagnostic_file(file_meta: dict) -> bool:
    if not _skip_diagnostic_files_enabled():
        return False
    return file_meta.get("file_diagnostics", {}).get("count", 0) > 0


def _read_and_chunk(
    abs_path: str, rel_path: str, project_id: str
) -> Tuple[List[Dict], str | None]:
    """Read *abs_path* and return chunk dicts with stable ref_ids.

    Uses ts_pack's native chunker (process() with chunk_max_size) for all
    languages ts_pack can detect.  Chunks with parse errors (has_error_nodes)
    are skipped.  Falls back to a line-window for config/doc files or when
    ts_pack returns no chunks.

    Each returned dict:
      ref_id   — content-hash stable ID (project_id:version:rel_path:sha256[:14])
      text     — chunk content with "// File: ..." header prepended
      metadata — {file, project_id, symbols, start_line, end_line, docstrings,
                   context_path}
    """
    import tree_sitter_language_pack as ts_pack

    _ensure_ts_pack_initialized()

    def _ensure_language_available(lang_name: str) -> bool:
        try:
            if ts_pack.has_language(lang_name):
                return True
            if not TS_PACK_AUTO_DOWNLOAD:
                return False
            ts_pack.download([lang_name])
            return ts_pack.has_language(lang_name)
        except Exception as exc:
            print(
                f"[lm-proxy:indexer] ts_pack download failed: {lang_name} ({exc})",
                file=sys.stderr,
                flush=True,
            )
            return False

    ext = abs_path.rsplit(".", 1)[-1].lower() if "." in abs_path else ""
    fallback_allowed = False
    if getattr(ts_pack, "should_use_line_window_fallback", None):
        try:
            fallback_allowed = bool(ts_pack.should_use_line_window_fallback(rel_path))
        except Exception:
            fallback_allowed = False

    # Use ts_pack.detect_language for language detection — covers 156 languages.
    # Prefer extension-based detection when available to avoid mis-detection.
    lang: str | None = None
    if not fallback_allowed:
        if ext == "svg":
            lang = "xml"
        else:
            try:
                lang = ts_pack.detect_language_from_extension(ext)
            except Exception:
                lang = None
            if not lang:
                lang = ts_pack.detect_language(abs_path)

    parser_missing = False
    if lang and not _ensure_language_available(lang):
        parser_missing = True
        lang = None

    # Runtime outcome, not manifest policy: the file reached the semantic worker
    # but we still could not determine a supported language or fallback path.
    if lang is None and not fallback_allowed:
        reason = "missing_parser" if parser_missing else "unknown_language"
        return [], reason

    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            source = fh.read()
    except OSError:
        return [], "read_error"

    # Runtime safety outcome, not manifest policy.
    if len(source) > MAX_FILE_BYTES:
        return [], "too_large"
    # Runtime file-content outcome, not manifest policy.
    if not source.strip():
        return [], "empty"

    chunks: List[Dict] = []
    file_meta: dict = {}

    if getattr(ts_pack, "build_indexing_chunks", None):
        try:
            payload = ts_pack.build_indexing_chunks(
                source,
                rel_path,
                project_id,
                language=lang,
                chunk_id_version=CHUNK_ID_VERSION,
                chunk_max_size=CHUNK_MAX_BYTES,
                chunk_overlap=CHUNK_OVERLAP_BYTES,
                chunk_lines=CHUNK_LINES,
                overlap_lines=OVERLAP_LINES,
            )
            file_meta = payload.get("file_meta") or {}
            if _should_skip_diagnostic_file(file_meta):
                return [], "diagnostics"
            chunks = payload.get("chunks") or []
        except Exception:
            chunks = []
            file_meta = {}
    else:
        # Compatibility fallback while editable installs/tests catch up to the
        # producer-owned helper surface.
        def _build_semantic_payload_compat(source_text: str, language_name: str) -> dict:
            payload_kwargs = {
                "chunk_id_version": CHUNK_ID_VERSION,
                "chunk_max_size": CHUNK_MAX_BYTES,
            }
            payload_sig = inspect.signature(ts_pack.build_semantic_payload)
            if "chunk_overlap" in payload_sig.parameters:
                payload_kwargs["chunk_overlap"] = CHUNK_OVERLAP_BYTES
            elif "_chunk_overlap" in payload_sig.parameters:
                payload_kwargs["_chunk_overlap"] = CHUNK_OVERLAP_BYTES
            return ts_pack.build_semantic_payload(
                source_text,
                language_name,
                rel_path,
                project_id,
                **payload_kwargs,
            )

        if lang == "swift":
            try:
                payload = _build_semantic_payload_compat(source, "swift")
                file_meta = payload.get("file_meta") or {}
                if _should_skip_diagnostic_file(file_meta):
                    return [], "diagnostics"
            except Exception:
                file_meta = {}

            chunks = ts_pack.build_swift_chunks(
                source,
                rel_path,
                project_id,
                file_meta=file_meta,
                chunk_id_version=CHUNK_ID_VERSION,
                chunk_max_size=CHUNK_MAX_BYTES,
                chunk_lines=CHUNK_LINES,
                overlap_lines=OVERLAP_LINES,
            )
        elif lang:
            try:
                payload = _build_semantic_payload_compat(source, lang)
                file_meta = payload.get("file_meta") or {}
                if _should_skip_diagnostic_file(file_meta):
                    return [], "diagnostics"
                chunks = payload.get("chunks") or []
            except Exception:
                chunks = []

        if not chunks:
            chunks = ts_pack.build_line_window_chunks(
                source,
                rel_path,
                project_id,
                language=lang,
                file_meta=file_meta,
                chunk_id_version=CHUNK_ID_VERSION,
                chunk_lines=CHUNK_LINES,
                overlap_lines=OVERLAP_LINES,
            )

    _validate_semantic_chunk_contract(chunks, rel_path, ts_pack)
    return chunks, None


async def chunk_file(
    abs_path: str, rel_path: str, project_id: str
) -> Tuple[List[Dict], str | None]:
    """Async wrapper — runs blocking read+chunk in a worker thread."""
    return await asyncio.to_thread(_read_and_chunk, abs_path, rel_path, project_id)


def _chunk_manifest_native(manifest: List[Dict], project_id: str) -> List[Tuple[List[Dict], str | None]]:
    import tree_sitter_language_pack as ts_pack

    native_manifest = getattr(ts_pack, "process_semantic_manifest_entries", None)
    if native_manifest is None:
        raise RuntimeError("ts_pack native manifest chunk processor unavailable")

    payload = native_manifest(
        manifest,
        project_id,
        max_file_bytes=MAX_FILE_BYTES,
        chunk_id_version=CHUNK_ID_VERSION,
        chunk_max_size=CHUNK_MAX_BYTES,
        chunk_overlap=CHUNK_OVERLAP_BYTES,
        chunk_lines=CHUNK_LINES,
        overlap_lines=OVERLAP_LINES,
        skip_diagnostic_files=_skip_diagnostic_files_enabled(),
    )
    all_results: List[Tuple[List[Dict], str | None]] = []
    for entry, item in zip(manifest, payload or []):
        chunks = item.get("chunks") or []
        reason = item.get("reason")
        _validate_semantic_chunk_contract(chunks, entry.get("rel_path") or "", ts_pack)
        all_results.append((chunks, reason))
    if len(all_results) != len(manifest):
        raise ValueError(
            f"ts_pack native manifest processor returned {len(all_results)} result(s) for {len(manifest)} file(s)"
        )
    return all_results


# ── Main indexing coroutine ───────────────────────────────────────────────────


async def _embed_buffer(buffer: list, embedding_svc) -> list:
    """Encode texts via LM Studio → attach .vector to each chunk."""
    if not buffer:
        return buffer
    texts = [it["text"] for it in buffer]
    vectors = await embedding_svc.embed_batch_async(
        texts,
        batch_size=embedding_svc.effective_batch_size,
    )
    for k, vec in enumerate(vectors):
        buffer[k]["vector"] = vec
    return buffer


async def _write_buffer(
    buffer: list,
    target_dir: str,
    project_id: str,
    *,
    defer_link_refs: bool = False,
    deferred_ref_ids: list[str] | None = None,
) -> int:
    """Write a pre-embedded buffer to Postgres. Returns chunk count written."""
    if not buffer:
        return 0
    if defer_link_refs and deferred_ref_ids is not None:
        deferred_ref_ids.extend(
            item.get("ref_id") for item in buffer if item.get("ref_id")
        )
    return await memory_store.insert_embeddings_batch(
        session_id=project_id,
        project_id=project_id,
        batch=buffer,
        project_path=target_dir,
        link_refs=not defer_link_refs,
    )


def _report_chunking_results(
    manifest: List[Dict], all_results: List[Tuple[List[Dict], str | None]], elapsed: float
) -> tuple[list[list[dict]], int, int]:
    all_chunks: List[List[Dict]] = [result[0] for result in all_results]
    skipped_reasons: Counter[str] = Counter(
        (reason or "unknown") for chunks, reason in all_results if not chunks
    )
    skipped_samples: dict[str, list[str]] = {}
    for entry, (chunks, reason) in zip(manifest, all_results):
        if chunks:
            continue
        reason_key = reason or "unknown"
        bucket = skipped_samples.setdefault(reason_key, [])
        if len(bucket) < 5:
            bucket.append(entry.get("rel_path") or "")

    total_files = len(manifest)
    parsed_files = sum(1 for chunks in all_chunks if chunks)
    skipped_files = total_files - parsed_files
    print(
        f"[lm-proxy:indexer] Chunked {total_files} files in {elapsed:.2f}s",
        file=sys.stderr,
        flush=True,
    )
    reason_bits = ""
    if skipped_files:
        reason_bits = (
            " ("
            + ", ".join(
                f"{reason}={count}" for reason, count in skipped_reasons.most_common()
            )
            + ")"
        )
    print(
        f"[lm-proxy:indexer] File parse summary — parsed={parsed_files} "
        f"skipped={skipped_files}{reason_bits}",
        file=sys.stderr,
        flush=True,
    )
    if skipped_samples:
        for reason, samples in skipped_samples.items():
            if not samples:
                continue
            sample_text = ", ".join(s for s in samples if s)
            if not sample_text:
                continue
            print(
                f"[lm-proxy:indexer] Skipped samples ({reason}): {sample_text}",
                file=sys.stderr,
                flush=True,
            )
    return all_chunks, parsed_files, skipped_files


def _semantic_run_id(project_id: str) -> str:
    return f"{project_id}:semantic:{uuid.uuid4().hex[:12]}"


def _get_latest_successful_struct_run_id(project_id: str) -> str | None:
    try:
        import neo4j
    except Exception:
        return None

    neo4j_uri = os.getenv("LM_PROXY_NEO4J_URI", "bolt://127.0.0.1:7687")
    neo4j_user = os.getenv("LM_PROXY_NEO4J_USER", "neo4j")
    neo4j_pass = os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")
    neo4j_db = os.getenv("LM_PROXY_NEO4J_DB", "proxy")
    driver = neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            record = session.run(
                """
                MATCH (sr:IndexRun {project_id:$pid, phase:'struct'})
                WHERE sr.status = 'done'
                RETURN sr.id AS run_id
                ORDER BY coalesce(sr.finished_at, sr.started_at, 0) DESC, sr.id DESC
                LIMIT 1
                """,
                pid=project_id,
            ).single()
            return record["run_id"] if record and record["run_id"] else None
    except Exception:
        return None
    finally:
        driver.close()


def _set_semantic_run_status(
    project_id: str,
    run_id: str,
    status: str,
    *,
    struct_run_id: str | None = None,
    error: str | None = None,
) -> None:
    try:
        import neo4j
    except Exception:
        return

    neo4j_uri = os.getenv("LM_PROXY_NEO4J_URI", "bolt://127.0.0.1:7687")
    neo4j_user = os.getenv("LM_PROXY_NEO4J_USER", "neo4j")
    neo4j_pass = os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")
    neo4j_db = os.getenv("LM_PROXY_NEO4J_DB", "proxy")
    driver = neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            session.run(
                """
                MERGE (p:Project {id:$pid})
                SET p.project_id = $pid,
                    p.semantic_index_status = $status,
                    p.semantic_index_run_id = $run_id,
                    p.semantic_index_finished_at = timestamp()
                FOREACH (_ IN CASE WHEN $struct_run_id IS NULL THEN [] ELSE [1] END |
                    SET p.semantic_target_struct_run_id = $struct_run_id
                )
                FOREACH (_ IN CASE WHEN $status = 'done' THEN [1] ELSE [] END |
                    SET p.semantic_active_run_id = $run_id,
                        p.semantic_last_successful_run_id = $run_id,
                        p.semantic_last_successful_finished_at = timestamp(),
                        p.semantic_active_struct_run_id = coalesce($struct_run_id, p.semantic_active_struct_run_id)
                )
                FOREACH (_ IN CASE WHEN $error IS NULL THEN [1] ELSE [] END |
                    REMOVE p.semantic_index_error
                )
                FOREACH (_ IN CASE WHEN $error IS NULL THEN [] ELSE [1] END |
                    SET p.semantic_index_error = $error
                )
                MERGE (r:IndexRun {id:$run_id})
                SET r.project_id = $pid,
                    r.phase = 'semantic',
                    r.status = $status,
                    r.finished_at = timestamp()
                FOREACH (_ IN CASE WHEN $struct_run_id IS NULL THEN [] ELSE [1] END |
                    SET r.target_struct_run_id = $struct_run_id
                )
                FOREACH (_ IN CASE WHEN $status = 'done' THEN [1] ELSE [] END |
                    SET r.promoted_at = timestamp()
                )
                FOREACH (_ IN CASE WHEN $error IS NULL THEN [1] ELSE [] END |
                    REMOVE r.error
                )
                FOREACH (_ IN CASE WHEN $error IS NULL THEN [] ELSE [1] END |
                    SET r.error = $error
                )
                """,
                pid=project_id,
                run_id=run_id,
                status=status,
                struct_run_id=struct_run_id,
                error=(error[:2000] if error else None),
            ).consume()
    except Exception:
        return
    finally:
        driver.close()


async def index_project(
    target_dir: str,
    project_id: str,
    manifest: List[Dict],
    rebuild: bool = False,
    cleanup_only: bool = False,
) -> int:
    """
    Chunk, embed, and write Chunk nodes for every file in *manifest*.
    If rebuild=True, wipes all project embeddings first.
    If cleanup_only=True, only deletes chunks for files not in manifest.

    Rolling-buffer design:
    - Files are chunked in parallel (asyncio.gather over I/O-bound reads).
    - Existing chunk_ids are fetched from Postgres in one query — unchanged
      chunks are skipped entirely (no re-embed, no re-write).
    - Only NEW chunks go through embed_batch + executemany.
    - embed_batch() fires only when the buffer hits full batch size (128),
      ensuring BLAS on the M3 Max's 12 p-cores is always saturated.
    - Postgres writes use a single executemany per batch (one round-trip).

    Returns total new chunks written.
    """
    global _LAST_INDEX_PROJECT_OK
    _LAST_INDEX_PROJECT_OK = True
    t0 = time.time()
    semantic_run_id = _semantic_run_id(project_id)
    struct_run_id = _get_latest_successful_struct_run_id(project_id)
    _set_semantic_run_status(
        project_id,
        semantic_run_id,
        "in_progress",
        struct_run_id=struct_run_id,
    )
    await memory_bootstrap.bootstrap_schema()
    await memory_store.open_pool()

    # ── Database Preparation & Garbage Collection ──────────────────────────
    if not memory_store._pg_pool_available():
        print(
            "[lm-proxy:indexer] ERROR: PG pool unavailable — semantic indexing skipped",
            file=sys.stderr,
        )
        _set_semantic_run_status(
            project_id,
            semantic_run_id,
            "failed",
            struct_run_id=struct_run_id,
            error="pg_pool_unavailable",
        )
        _LAST_INDEX_PROJECT_OK = False
        return 0

    embedding_svc = get_embedding_service()

    embed_bs = embedding_svc.effective_batch_size
    write_bs = max(embed_bs, int(os.getenv("LM_PROXY_PG_WRITE_BATCH_SIZE", str(max(embed_bs, 1024)))))
    total_files = len(manifest)
    print(
        f"[lm-proxy:indexer] Semantic phase — {total_files} files "
        f"(device={embedding_svc._device}, embed_batch={embed_bs}, write_batch={write_bs}, "
        f"chunk_concurrency={CHUNK_CONCURRENCY})",
        file=sys.stderr,
        flush=True,
    )

    _preflight_ts_pack(manifest)
    deferred_ref_ids: list[str] = []
    ingest_stats = {
        "embed_calls": 0,
        "embed_chunks": 0,
        "embed_seconds": 0.0,
        "write_calls": 0,
        "write_chunks": 0,
        "write_seconds": 0.0,
        "deferred_link_seconds": 0.0,
        "deferred_link_count": 0,
    }

    import tree_sitter_language_pack as ts_pack

    native_manifest_processor = getattr(ts_pack, "process_semantic_manifest_entries", None)
    t_chunk = time.time()
    if native_manifest_processor is not None:
        print(
            "[lm-proxy:indexer] Semantic chunking — native ts-pack manifest path",
            file=sys.stderr,
            flush=True,
        )
        try:
            all_results = await asyncio.to_thread(_chunk_manifest_native, manifest, project_id)
        except Exception as exc:
            print(
                f"[lm-proxy:indexer] Native manifest chunking failed ({exc}) — falling back",
                file=sys.stderr,
                flush=True,
            )
            native_manifest_processor = None
    if native_manifest_processor is None:
        # ── Parallel chunking (I/O-bound reads) ─────────────────────────────
        # Bound concurrent file reads/parses so large manifests do not exhaust the
        # file descriptor limit on hosts with lower per-process limits.
        chunk_sem = asyncio.Semaphore(CHUNK_CONCURRENCY)

        async def _chunk_manifest_entry(entry: Dict) -> Tuple[List[Dict], str | None]:
            async with chunk_sem:
                return await chunk_file(
                    entry["abs_path"], entry["rel_path"], project_id
                )

        all_results = await asyncio.gather(
            *[_chunk_manifest_entry(e) for e in manifest]
        )
    all_chunks, parsed_files, skipped_files = _report_chunking_results(
        manifest, all_results, time.time() - t_chunk
    )

    manifest_paths = [entry.get("rel_path") or "" for entry in manifest]
    try:
        async with memory_store._pg_pool.connection() as conn:  # type: ignore[union-attr]
            from embedding_service import _CONCURRENCY as CONCURRENCY

            async def _embed(batch):
                started = time.perf_counter()
                result = await _embed_buffer(batch, embedding_svc)
                ingest_stats["embed_calls"] += 1
                ingest_stats["embed_chunks"] += len(batch)
                ingest_stats["embed_seconds"] += time.perf_counter() - started
                return result

            async def _write(batch):
                started = time.perf_counter()
                written = await _write_buffer(
                    batch,
                    target_dir,
                    project_id,
                    defer_link_refs=DEFER_NEO4J_EMBED_LINKS,
                    deferred_ref_ids=deferred_ref_ids,
                )
                ingest_stats["write_calls"] += 1
                ingest_stats["write_chunks"] += int(written or 0)
                ingest_stats["write_seconds"] += time.perf_counter() - started
                return written

            async def _progress(event: dict) -> None:
                phase = event.get("phase")
                if phase == "prepare_done":
                    print(
                        f"[lm-proxy:indexer] Semantic prepare — "
                        f"{event.get('prepare_seconds', 0.0):.2f}s "
                        f"(existing={event.get('existing_count', 0)} "
                        f"orphans={event.get('orphan_pruned', 0)} "
                        f"pruned={event.get('pruned_total', 0)} "
                        f"new={event.get('total_new', 0)})",
                        file=sys.stderr,
                        flush=True,
                    )
                elif phase == "embed_start":
                    print(
                        f"[lm-proxy:indexer] Embedding round {event['round_index'] + 1}/{event['rounds']} "
                        f"— {event['batch_count']} concurrent batches — "
                        f"{event['written_so_far'] + event['group_size']}/{event['total_new']} chunks…",
                        file=sys.stderr,
                        flush=True,
                    )
                elif phase == "round_done":
                    print(
                        f"[lm-proxy:indexer]   wrote {event.get('round_written', 0)} chunks "
                        f"(embed={event.get('embed_seconds', 0.0):.2f}s "
                        f"write={event.get('write_seconds', 0.0):.2f}s "
                        f"round={event.get('round_seconds', 0.0):.2f}s)",
                        file=sys.stderr,
                        flush=True,
                    )

            native_driver = getattr(ts_pack, "execute_semantic_index_driver_native", None)
            pg_dsn = os.getenv("LM_PROXY_PG_DSN", "").strip()
            fake_embeddings = os.getenv("LM_PROXY_FAKE_EMBEDDINGS", "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            lmstudio_provider = None
            if (
                USE_NATIVE_SEMANTIC_DRIVER
                and not fake_embeddings
                and native_driver is not None
                and pg_dsn
            ):
                try:
                    lmstudio_provider = get_lmstudio_provider()
                except Exception:
                    lmstudio_provider = None
            if (
                USE_NATIVE_SEMANTIC_DRIVER
                and not fake_embeddings
                and native_driver is not None
                and pg_dsn
                and lmstudio_provider is not None
            ):
                print(
                    "[lm-proxy:indexer] Semantic driver — native Rust LM Studio + Postgres path",
                    file=sys.stderr,
                    flush=True,
                )
                index_result = await native_driver(
                    project_id,
                    manifest_paths,
                    all_chunks,
                    pg_dsn,
                    lmstudio_provider.config.base_url,
                    lmstudio_provider.config.embed_model,
                    rebuild=rebuild,
                    # Keep round planning tied to embed_bs so native execution
                    # preserves the intended LM Studio fan-out.
                    batch_size=embed_bs,
                    concurrency=CONCURRENCY,
                    write_batch_size=write_bs,
                    timeout_s=lmstudio_provider.config.embed_timeout_s,
                    progress_fn=_progress,
                )
                native_stats = index_result.get("ingest_stats") or {}
                for key in (
                    "embed_calls",
                    "embed_chunks",
                    "embed_seconds",
                    "write_calls",
                    "write_chunks",
                    "write_seconds",
                ):
                    if key in native_stats:
                        ingest_stats[key] = native_stats[key]
                if DEFER_NEO4J_EMBED_LINKS:
                    deferred_ref_ids.extend(index_result.get("written_ref_ids") or [])
            else:
                index_result = await ts_pack.execute_semantic_index_driver(
                    conn,
                    project_id,
                    manifest_paths,
                    all_chunks,
                    rebuild=rebuild,
                    # This batch_size shapes semantic round sub-batches before they
                    # reach embed_batch_fn. Keep it aligned to embed_bs so we do not
                    # multiply LM Studio fan-out (sub-batches * provider concurrency).
                    batch_size=embed_bs,
                    concurrency=CONCURRENCY,
                    embed_batch_fn=_embed,
                    write_batch_fn=_write,
                    progress_fn=_progress,
                )
    except Exception as exc:
        print(
            f"[lm-proxy:indexer] WARN: semantic index driver failed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        _set_semantic_run_status(
            project_id,
            semantic_run_id,
            "failed",
            struct_run_id=struct_run_id,
            error=str(exc),
        )
        _LAST_INDEX_PROJECT_OK = False
        index_result = {
            "new_chunks": [],
            "skipped_chunks": 0,
            "pruned_total": 0,
            "existing_ids": set(),
            "wiped": False,
            "orphan_pruned": 0,
            "written": 0,
            "rounds": 0,
        }

    if rebuild and index_result.get("wiped"):
        print(
            f"[lm-proxy:indexer] Total rebuild requested — wiped project '{project_id}'",
            file=sys.stderr,
            flush=True,
        )
    orphan_pruned = int(index_result.get("orphan_pruned") or 0)
    if orphan_pruned > 0:
        print(
            f"[lm-proxy:indexer] Pruned {orphan_pruned} orphaned files (ghosts)",
            file=sys.stderr,
            flush=True,
        )
    if cleanup_only:
        print("[lm-proxy:indexer] Cleanup only requested — done.", file=sys.stderr)
        _set_semantic_run_status(
            project_id,
            semantic_run_id,
            "done",
            struct_run_id=struct_run_id,
        )
        return 0

    existing_count = int(index_result.get("existing_count") or len(index_result.get("existing_ids") or set()))
    print(
        f"[lm-proxy:indexer] {existing_count} chunks already indexed — skipping unchanged",
        file=sys.stderr,
        flush=True,
    )
    pruned_total = int(index_result.get("pruned_total") or 0)
    if pruned_total > 0:
        print(
            f"[lm-proxy:indexer] Surgically pruned {pruned_total} ghost chunks",
            file=sys.stderr,
            flush=True,
        )

    skipped = int(index_result.get("skipped_chunks") or 0)
    total_indexed = int(index_result.get("written") or 0)
    expected_new = int(index_result.get("new_chunk_count") or len(index_result.get("new_chunks") or []))

    if DEFER_NEO4J_EMBED_LINKS and deferred_ref_ids:
        started = time.perf_counter()
        linked = await memory_store.link_embedding_refs(
            project_id,
            project_id,
            deferred_ref_ids,
            batch_size=DEFER_NEO4J_LINK_BATCH_SIZE,
        )
        ingest_stats["deferred_link_seconds"] += time.perf_counter() - started
        ingest_stats["deferred_link_count"] = linked
        if linked:
            print(
                f"[lm-proxy:indexer] Linked {linked} embedding refs in deferred Neo4j pass",
                file=sys.stderr,
                flush=True,
            )

    elapsed = time.time() - t0
    print(
        f"[lm-proxy:indexer] Done — {total_indexed} new / {skipped} skipped / "
        f"{total_files} files in {elapsed:.2f}s "
        f"(parsed={parsed_files} skipped_files={skipped_files})",
        file=sys.stderr,
        flush=True,
    )
    print(
        f"[lm-proxy:indexer] Ingest timing — "
        f"embed={ingest_stats['embed_seconds']:.2f}s/{ingest_stats['embed_calls']} calls/{ingest_stats['embed_chunks']} chunks, "
        f"pg_write={ingest_stats['write_seconds']:.2f}s/{ingest_stats['write_calls']} calls/{ingest_stats['write_chunks']} chunks, "
        f"neo4j_link={ingest_stats['deferred_link_seconds']:.2f}s/{ingest_stats['deferred_link_count']} refs",
        file=sys.stderr,
        flush=True,
    )
    if total_indexed != expected_new:
        error = (
            "semantic_partial_completion: "
            f"wrote={total_indexed} expected_new={expected_new} "
            f"skipped={skipped} parsed_files={parsed_files} skipped_files={skipped_files}"
        )
        print(
            f"[lm-proxy:indexer] ERROR: {error}",
            file=sys.stderr,
            flush=True,
        )
        _set_semantic_run_status(
            project_id,
            semantic_run_id,
            "failed",
            struct_run_id=struct_run_id,
            error=error,
        )
        _LAST_INDEX_PROJECT_OK = False
        return total_indexed
    _set_semantic_run_status(
        project_id,
        semantic_run_id,
        "done",
        struct_run_id=struct_run_id,
    )
    return total_indexed


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GraphRAG semantic indexer")
    parser.add_argument("target", help="Absolute project root path")
    parser.add_argument("project_id", help="12-char project hash ID")
    parser.add_argument("--manifest-file", required=True, help="Path to JSON manifest")
    parser.add_argument(
        "--rebuild", action="store_true", help="Wipe all project embeddings first"
    )
    parser.add_argument(
        "--cleanup-only", action="store_true", help="Only prune dead files from index"
    )
    args = parser.parse_args()

    print(
        "[lm-proxy:indexer] NOTE: For aligned indexing, run the MCP tool "
        "index_workspace() which generates a shared manifest for struct/semantic.",
        file=sys.stderr,
        flush=True,
    )

    if not os.path.exists(args.manifest_file):
        print(
            f"[lm-proxy:indexer] ERROR: manifest not found: {args.manifest_file}",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(args.manifest_file, "r") as fh:
        manifest_data: List[Dict] = json.load(fh)

    print(
        f"[lm-proxy:indexer] Manifest loaded — {len(manifest_data)} entries",
        file=sys.stderr,
        flush=True,
    )

    indexed = asyncio.run(
        index_project(
            args.target,
            args.project_id,
            manifest_data,
            rebuild=args.rebuild,
            cleanup_only=args.cleanup_only,
        )
    )
    sys.exit(0 if _LAST_INDEX_PROJECT_OK else 1)

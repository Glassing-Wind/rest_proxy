#!/usr/bin/env python3
"""Semantic indexing worker for GraphRAG pipeline.

Consumes a JSON manifest of source files, chunks them with a pure-Python
strategy, calls LM Studio for embeddings, and writes chunk rows to Postgres.

Design:
- Chunks files in parallel (asyncio.gather over I/O-bound reads).
- Skips chunks already present in Postgres (skip-unchanged optimisation).
- Embeds CONCURRENCY batches concurrently via async HTTP to LM Studio.
- Pipelines Postgres writes concurrently with next embed group.
- Single process — no native-lib conflicts.

Usage:
    python scripts/index_workspace.py <target_dir> <project_id> --manifest-file <path>
"""

import os
import sys
import asyncio
import json
import time
import threading
from collections import Counter
from typing import List, Dict, Tuple
from dotenv import load_dotenv

# Import memory/embedding modules AFTER env vars are set
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO_ROOT, ".env"))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import memory.store as memory_store
import memory.bootstrap as memory_bootstrap
from embedding_service import get_embedding_service
from graphrag_core.ts_pack_facts import extract_file_facts
from ts_diagnostics import normalize_ts_pack_result

# AST-chunk size: target upper bound for native ts_pack chunks.
CHUNK_MAX_BYTES = 4_000  # bytes — passed as chunk_max_size to ProcessConfig
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
TS_PACK_AUTO_DOWNLOAD = os.getenv("LM_PROXY_TS_PACK_AUTO_DOWNLOAD", "1") == "1"
TS_PACK_CACHE_DIR = os.getenv("LM_PROXY_TS_PACK_CACHE_DIR")

_TS_PACK_INIT_DONE = False
_TS_PACK_INIT_LOCK = threading.Lock()

# Extensions that always use the line-window fallback (no AST structure).
_FALLBACK_EXTS = {
    "yaml",
    "yml",
    "toml",
    "json",
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
}

# Dotfiles that should still be chunked with the line-window fallback.
_FALLBACK_FILENAMES = {
    ".env",
    ".env.example",
    ".gitignore",
    ".indexignore",
}

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
        if not ext or ext in _FALLBACK_EXTS:
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


def _chunk_id(project_id: str, rel_path: str, start_byte: int, text: str) -> str:
    """Content-addressable chunk ID — stable across re-indexes.

    Encodes the file path + start byte (for uniqueness) + chunk text (for
    change detection). Unchanged chunks keep the same ID on re-index →
    skip-unchanged optimization remains effective even after file edits that
    shift chunk boundaries.
    """
    import hashlib

    digest = hashlib.sha256(f"{rel_path}:{start_byte}:{text}".encode()).hexdigest()[:14]
    return f"{project_id}:{CHUNK_ID_VERSION}:{rel_path}:{digest}"


def _compact_list(items: list, limit: int) -> list:
    if not items:
        return []
    if len(items) <= limit:
        return items
    return items[:limit]


def _compact_imports(imports: list) -> list:
    out: list = []
    for item in imports or []:
        source = item.get("source") or item.get("module")
        names = item.get("names") or []
        if source:
            out.append({"source": source, "names": _compact_list(names, 10)})
    return _compact_list(out, 80)


def _compact_exports(exports: list) -> list:
    out: list = []
    for item in exports or []:
        name = None
        kind = None
        if isinstance(item, dict):
            name = item.get("name")
            kind = item.get("kind")
        if name:
            out.append({"name": name, "kind": kind})
    return _compact_list(out, 80)


def _compact_symbols(symbols: list) -> list:
    if not symbols:
        return []
    unique: list[str] = []
    seen: set[str] = set()
    for s in symbols:
        name = None
        if isinstance(s, dict):
            name = s.get("name") or s.get("symbol") or s.get("text")
        elif isinstance(s, str):
            name = s
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append(name)
        if len(unique) >= 200:
            break
    return unique


def _compact_diagnostics(diagnostics: list) -> dict:
    if not diagnostics:
        return {"count": 0, "items": []}
    items = []
    for d in diagnostics[:10]:
        items.append(
            {
                "message": d.get("message"),
                "start_line": d.get("start_line") or d.get("span", {}).get("start_row"),
                "start_col": d.get("start_col") or d.get("span", {}).get("start_col"),
            }
        )
    return {"count": len(diagnostics), "items": items}


def _extract_metrics(metrics: dict) -> dict:
    if not metrics:
        return {}
    return {
        "total_lines": metrics.get("total_lines") or metrics.get("totalLines"),
        "code_lines": metrics.get("code_lines") or metrics.get("codeLines"),
        "comment_lines": metrics.get("comment_lines") or metrics.get("commentLines"),
        "blank_lines": metrics.get("blank_lines") or metrics.get("blankLines"),
        "error_count": metrics.get("error_count") or metrics.get("errorCount"),
        "complexity": metrics.get("complexity"),
    }


def _compact_extractions(extractions: dict) -> dict:
    if not extractions:
        return {}
    out: dict = {}
    for name, payload in (extractions or {}).items():
        matches = payload.get("matches") or []
        values: list = []
        for m in matches:
            for cap in m.get("captures", []):
                text = cap.get("text") or cap.get("name")
                if text:
                    values.append(text)
                if len(values) >= 200:
                    break
            if len(values) >= 200:
                break
        if values:
            out[name] = _compact_symbols(values)
    return out


def _chunk_swift(source: str, rel_path: str, project_id: str) -> List[Dict]:
    """Chunk Swift source at declaration boundaries using a direct AST walk.

    ts_pack's process(chunk_max_size=N) recurses into child AST nodes when a
    declaration body exceeds N.  For SwiftUI trailing-closure DSL this produces
    hundreds of brace/expression micro-fragments (6-80 bytes each).

    Instead, we walk the tree-sitter AST directly:
    - Collect computed_property / function_declaration / variable_declaration
      nodes that are direct members of type containers.
    - Emit one chunk per member.  Members that exceed CHUNK_MAX_BYTES are
      split with a line-window (never by recursing into sub-expressions).
    - Type containers (struct/class/extension/enum/protocol) are NOT emitted
      as a single chunk; we recurse into their members so each gets its own
      semantic context.

    Returns [] on any error so _read_and_chunk falls through to line-window.
    """
    import tree_sitter_language_pack as ts_pack

    # Member nodes: one chunk each.
    # Note: Swift var/let are wrapped in property_declaration (which contains
    # computed_property as a child).  We chunk at property_declaration level
    # to capture the name from the sibling `pattern` node.
    _MEMBER_TYPES = {
        "property_declaration",  # var x: T { ... } and var x: T = value
        "function_declaration",
        "subscript_declaration",
        "typealias_declaration",
        "init_declaration",
        "deinit_declaration",
        "protocol_function_declaration",
        "protocol_property_declaration",
        "enum_entry",  # case first, case second(Int)
    }
    # Type containers: recurse into children, don't emit as a single block.
    _CONTAINER_TYPES = {
        "class_declaration",
        "struct_declaration",
        "enum_declaration",
        "protocol_declaration",
        "extension_declaration",
    }

    try:
        parser = ts_pack.get_parser("swift")
        src_b = source.encode("utf-8")
        tree = parser.parse(src_b)
    except Exception:
        return []

    file_header = f"// File: {rel_path}\n"
    chunks: List[Dict] = []

    def _name_of(node) -> str:
        for child in node.children:
            # Swift property names live in a `pattern` child node;
            # function names live in a bare `simple_identifier` child.
            if child.type == "pattern":
                return src_b[child.start_byte : child.end_byte].decode(
                    "utf-8", errors="replace"
                )
            if child.type in ("simple_identifier", "type_identifier"):
                return src_b[child.start_byte : child.end_byte].decode(
                    "utf-8", errors="replace"
                )
        return ""

    def _emit_text(
        text: str, sb: int, name: str, sl: int, el: int, ctx_path: List[str]
    ) -> None:
        text = text.strip()
        if not text:
            return
        if len(text.encode("utf-8")) <= CHUNK_MAX_BYTES:
            cid = _chunk_id(project_id, rel_path, sb, text)
            chunks.append(
                {
                    "ref_id": cid,
                    "text": file_header + text,
                    "metadata": {
                        "file": rel_path,
                        "project_id": project_id,
                        "language": "swift",
                        "symbols": [name] if name else [],
                        "start_line": sl,
                        "end_line": el,
                        "context_path": ctx_path,
                    },
                }
            )
        else:
            # Too large — line-window, keeping symbol context.
            lines = text.splitlines()
            i = 0
            while i < len(lines):
                block = "\n".join(lines[i : i + CHUNK_LINES])
                if block.strip():
                    cid = _chunk_id(project_id, rel_path, sb + i, block)
                    chunks.append(
                        {
                            "ref_id": cid,
                            "text": file_header + block,
                            "metadata": {
                                "file": rel_path,
                                "project_id": project_id,
                                "language": "swift",
                                "symbols": [name] if name else [],
                                "start_line": sl + i,
                                "end_line": sl + min(i + CHUNK_LINES, len(lines)) - 1,
                                "context_path": ctx_path,
                            },
                        }
                    )
                i += CHUNK_LINES - OVERLAP_LINES

    def _walk(node, ctx_path: List[str]) -> None:
        if node.type in _MEMBER_TYPES:
            name = _name_of(node)
            text = src_b[node.start_byte : node.end_byte].decode(
                "utf-8", errors="replace"
            )
            _emit_text(
                text,
                node.start_byte,
                name,
                node.start_point[0] + 1,
                node.end_point[0] + 1,
                ctx_path + ([name] if name else []),
            )
            # Don't recurse into member bodies — avoids sub-expression chunks.

        elif node.type in _CONTAINER_TYPES:
            name = _name_of(node)
            new_ctx = ctx_path + ([name] if name else [])
            for child in node.children:
                _walk(child, new_ctx)

        else:
            # Transparent node — pass through (source_file, statements, etc.)
            for child in node.children:
                _walk(child, ctx_path)

    _walk(tree.root_node, [])
    return chunks


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

    # Use ts_pack.detect_language for language detection — covers 156 languages.
    # Prefer extension-based detection when available to avoid mis-detection.
    lang: str | None = None
    if (
        ext not in _FALLBACK_EXTS
        and os.path.basename(abs_path) not in _FALLBACK_FILENAMES
    ):
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

    # Nothing to do: unknown file type and not a line-window fallback extension.
    if (
        lang is None
        and ext not in _FALLBACK_EXTS
        and os.path.basename(abs_path) not in _FALLBACK_FILENAMES
    ):
        reason = "missing_parser" if parser_missing else "unknown_language"
        return [], reason

    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            source = fh.read()
    except OSError:
        return [], "read_error"

    if len(source) > MAX_FILE_BYTES:
        return [], "too_large"
    if not source.strip():
        return [], "empty"

    file_header = f"// File: {rel_path}\n"
    chunks: List[Dict] = []
    file_meta: dict = {}

    # ── Swift: declaration-boundary chunker (avoids sub-expression atomization)
    if lang == "swift":
        # Try to enrich file-level metadata from ts_pack.
        try:
            try:
                cfg = ts_pack.ProcessConfig(
                    "swift",
                    structure=True,
                    imports=True,
                    exports=True,
                    comments=True,
                    docstrings=True,
                    symbols=True,
                    diagnostics=True,
                )
            except TypeError:
                cfg = ts_pack.ProcessConfig("swift")
            result = normalize_ts_pack_result(
                source, "swift", ts_pack.process(source, cfg)
            )
            file_meta = {
                "file_imports": _compact_imports(result.get("imports", [])),
                "file_exports": _compact_exports(result.get("exports", [])),
                "file_symbols": _compact_symbols(result.get("symbols", [])),
                "file_diagnostics": _compact_diagnostics(result.get("diagnostics", [])),
                "file_metrics": _extract_metrics(result.get("metrics", {})),
                "file_extractions": _compact_extractions(result.get("extractions", {})),
            }
            file_facts = extract_file_facts(ts_pack, source, "swift", rel_path)
            if file_facts:
                file_meta["file_facts"] = file_facts
            if os.getenv("LM_PROXY_SKIP_DIAGNOSTIC_FILES", "").lower() in (
                "1",
                "true",
                "yes",
            ):
                if file_meta.get("file_diagnostics", {}).get("count", 0) > 0:
                    return [], "diagnostics"
        except Exception:
            file_meta = {}

        swift_chunks = _chunk_swift(source, rel_path, project_id)
        if swift_chunks:
            for ch in swift_chunks:
                if isinstance(ch.get("metadata"), dict):
                    ch["metadata"].update(file_meta)
            return swift_chunks, None
        # fall through to ts_pack / line-window if structure[] was empty

    # ── Native ts_pack chunking ───────────────────────────────────────────────
    if lang and lang != "swift":
        try:
            try:
                config = ts_pack.ProcessConfig(
                    lang,
                    structure=True,
                    imports=True,
                    exports=True,
                    comments=True,
                    docstrings=True,
                    symbols=True,
                    diagnostics=True,
                    chunk_max_size=CHUNK_MAX_BYTES,
                    chunk_overlap=CHUNK_OVERLAP_BYTES,
                    extractions=_EXTRACTIONS_BY_LANG.get(lang),
                )
            except TypeError:
                config = ts_pack.ProcessConfig(
                    lang,
                    structure=True,
                    imports=True,
                    exports=True,
                    comments=True,
                    docstrings=True,
                    symbols=True,
                    diagnostics=True,
                    chunk_max_size=CHUNK_MAX_BYTES,
                )
            result = normalize_ts_pack_result(
                source, lang, ts_pack.process(source, config)
            )
            file_meta = {
                "file_imports": _compact_imports(result.get("imports", [])),
                "file_exports": _compact_exports(result.get("exports", [])),
                "file_symbols": _compact_symbols(result.get("symbols", [])),
                "file_diagnostics": _compact_diagnostics(result.get("diagnostics", [])),
                "file_metrics": _extract_metrics(result.get("metrics", {})),
                "file_extractions": _compact_extractions(result.get("extractions", {})),
            }
            file_facts = extract_file_facts(ts_pack, source, lang, rel_path)
            if file_facts:
                file_meta["file_facts"] = file_facts
            if os.getenv("LM_PROXY_SKIP_DIAGNOSTIC_FILES", "").lower() in (
                "1",
                "true",
                "yes",
            ):
                if file_meta.get("file_diagnostics", {}).get("count", 0) > 0:
                    return [], "diagnostics"
            for chunk in result.get("chunks", []):
                cmeta = chunk.get("metadata", {})
                # Skip chunks that contain parse errors — embeddings for broken
                # syntax are low-quality and waste embedding budget.
                if cmeta.get("has_error_nodes"):
                    continue
                content = chunk.get("content", "")
                if not content.strip():
                    continue
                text = file_header + content
                cid = _chunk_id(
                    project_id, rel_path, chunk.get("start_byte", 0), content
                )
                chunks.append(
                    {
                        "ref_id": cid,
                        "text": text,
                        "metadata": {
                            "file": rel_path,
                            "project_id": project_id,
                            "language": lang,
                            "symbols": cmeta.get("symbols_defined", []),
                            "start_line": chunk.get("start_line", 0) + 1,
                            "end_line": chunk.get("end_line", 0) + 1,
                            "docstrings": cmeta.get("docstrings", []),
                            "context_path": cmeta.get("context_path", []),
                            "node_types": cmeta.get("node_types", []),
                            "comments": cmeta.get("comments", []),
                            "has_error_nodes": bool(cmeta.get("has_error_nodes")),
                            **file_meta,
                        },
                    }
                )
        except Exception:
            pass  # Fall through to line-window below

    # ── Line-window fallback (unsupported lang or empty result) ──────────────
    if not chunks:
        lines = source.splitlines()
        i = 0
        while i < len(lines):
            block = lines[i : i + CHUNK_LINES]
            if not block:
                break
            text = file_header + "\n".join(block)
            cid = _chunk_id(project_id, rel_path, i, text)
            chunks.append(
                {
                    "ref_id": cid,
                    "text": text,
                    "metadata": {
                        "file": rel_path,
                        "project_id": project_id,
                        "language": lang,
                        **file_meta,
                    },
                }
            )
            i += CHUNK_LINES - OVERLAP_LINES

    return chunks, None


async def chunk_file(
    abs_path: str, rel_path: str, project_id: str
) -> Tuple[List[Dict], str | None]:
    """Async wrapper — runs blocking read+chunk in a worker thread."""
    return await asyncio.to_thread(_read_and_chunk, abs_path, rel_path, project_id)


# ── Main indexing coroutine ───────────────────────────────────────────────────


async def _embed_buffer(buffer: list, embedding_svc) -> list:
    """Encode texts via LM Studio → attach .vector to each chunk."""
    if not buffer:
        return buffer
    texts = [it["text"] for it in buffer]
    vectors = await embedding_svc.embed_batch_async(texts)
    for k, vec in enumerate(vectors):
        buffer[k]["vector"] = vec
    return buffer


async def _write_buffer(
    buffer: list,
    target_dir: str,
    project_id: str,
) -> int:
    """Write a pre-embedded buffer to Postgres. Returns chunk count written."""
    if not buffer:
        return 0
    return await memory_store.insert_embeddings_batch(
        session_id=project_id,
        project_id=project_id,
        batch=buffer,
        project_path=target_dir,
    )


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
    t0 = time.time()
    await memory_bootstrap.bootstrap_schema()
    await memory_store.open_pool()

    # ── Database Preparation & Garbage Collection ──────────────────────────
    if not memory_store._pg_pool_available():
        print(
            "[lm-proxy:indexer] ERROR: PG pool unavailable — semantic indexing skipped",
            file=sys.stderr,
        )
        return 0

    # 1. Total Rebuild (Wipe project clean)
    if rebuild:
        try:
            print(
                f"[lm-proxy:indexer] Total rebuild requested — wiping project '{project_id}'...",
                file=sys.stderr,
                flush=True,
            )
            async with memory_store._pg_pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM codebase_embeddings WHERE project_id = %s",
                    (project_id,),
                )
            print("[lm-proxy:indexer]   Project wiped.", file=sys.stderr, flush=True)
        except Exception as exc:
            print(
                f"[lm-proxy:indexer] ERROR: rebuild wipe failed: {exc}", file=sys.stderr
            )
            return 0

    # 2. Prune Orphans (Files that existed in past index but are gone from manifest)
    try:
        t_prune = time.time()
        async with memory_store._pg_pool.connection() as conn:
            # Get all filepaths currently in DB
            rows_cursor = await conn.execute(
                "SELECT DISTINCT file_path FROM codebase_embeddings WHERE project_id = %s",
                (project_id,),
            )
            db_paths = {r[0] async for r in rows_cursor}

            if db_paths:
                manifest_paths = {entry.get("rel_path") for entry in manifest}
                orphans = db_paths - manifest_paths
                if orphans:
                    print(
                        f"[lm-proxy:indexer] Pruning {len(orphans)} orphaned files (ghosts)...",
                        file=sys.stderr,
                        flush=True,
                    )
                    for path in orphans:
                        await conn.execute(
                            "DELETE FROM codebase_embeddings WHERE project_id = %s AND file_path = %s",
                            (project_id, path),
                        )
                    print(
                        f"[lm-proxy:indexer]   Pruned in {(time.time() - t_prune) * 1000:.0f}ms",
                        file=sys.stderr,
                        flush=True,
                    )

        if cleanup_only:
            print("[lm-proxy:indexer] Cleanup only requested — done.", file=sys.stderr)
            return 0

    except Exception as exc:
        print(
            f"[lm-proxy:indexer] WARN: orphan pruning failed: {exc}",
            file=sys.stderr,
            flush=True,
        )
    embedding_svc = get_embedding_service()

    bs = embedding_svc.effective_batch_size
    total_files = len(manifest)
    total_indexed = 0
    skipped = 0
    buffer: list = []

    print(
        f"[lm-proxy:indexer] Semantic phase — {total_files} files "
        f"(device={embedding_svc._device}, embed_batch={bs}, chunk_concurrency={CHUNK_CONCURRENCY})",
        file=sys.stderr,
        flush=True,
    )

    _preflight_ts_pack(manifest)

    # ── Parallel chunking (I/O-bound reads) ─────────────────────────────────
    # Bound concurrent file reads/parses so large manifests do not exhaust the
    # file descriptor limit on hosts with lower per-process limits.
    chunk_sem = asyncio.Semaphore(CHUNK_CONCURRENCY)

    async def _chunk_manifest_entry(entry: Dict) -> Tuple[List[Dict], str | None]:
        async with chunk_sem:
            return await chunk_file(
                entry["abs_path"], entry["rel_path"], project_id
            )

    t_chunk = time.time()
    all_results: List[Tuple[List[Dict], str | None]] = await asyncio.gather(
        *[_chunk_manifest_entry(e) for e in manifest]
    )
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
    parsed_files = sum(1 for chunks in all_chunks if chunks)
    skipped_files = total_files - parsed_files
    print(
        f"[lm-proxy:indexer] Chunked {total_files} files in "
        f"{time.time() - t_chunk:.2f}s",
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

    # ── Fetch already-indexed chunk_ids (one Postgres round-trip) ────────────
    existing_ids: set = set()
    if memory_store._pg_pool_available():
        try:
            async with memory_store._pg_pool.connection() as conn:  # type: ignore[union-attr]
                cur = await conn.execute(
                    "SELECT chunk_id FROM codebase_embeddings WHERE project_id = %s",
                    [project_id],
                )
                existing_ids = {row[0] for row in await cur.fetchall()}
        except Exception as exc:
            print(
                f"[lm-proxy:indexer] WARN: could not fetch existing ids: {exc}",
                file=sys.stderr,
                flush=True,
            )
    print(
        f"[lm-proxy:indexer] {len(existing_ids)} chunks already indexed — skipping unchanged",
        file=sys.stderr,
        flush=True,
    )

    # ── Surgical Pruning: Remove ghost chunks for modified files ────────────────
    # For every file in the manifest, we must ensure Postgres only contains the
    # chunks we just generated. This removes "orphaned" chunks from old versions.
    if memory_store._pg_pool_available() and all_chunks:
        try:
            t_prune = time.time()
            pruned_total = 0
            async with memory_store._pg_pool.connection() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cur:
                    for file_chunks in all_chunks:
                        if not file_chunks:
                            continue
                        rel_path = file_chunks[0]["metadata"].get("file")
                        if not rel_path:
                            continue
                        
                        # Current valid chunk IDs for this file
                        valid_ids = [c["ref_id"] for c in file_chunks]
                        
                        # Delete any chunks for this file NOT in current manifest
                        await cur.execute(
                            """
                            DELETE FROM codebase_embeddings
                            WHERE project_id = %s 
                              AND file_path = %s
                              AND NOT (chunk_id = ANY(%s))
                            """,
                            (project_id, rel_path, valid_ids),
                        )
                        pruned_total += cur.rowcount
            if pruned_total > 0:
                print(
                    f"[lm-proxy:indexer] Surgically pruned {pruned_total} ghost chunks in "
                    f"{(time.time() - t_prune) * 1000:.0f}ms",
                    file=sys.stderr,
                    flush=True,
                )
        except Exception as exc:
            print(
                f"[lm-proxy:indexer] WARN: surgical pruning failed: {exc}",
                file=sys.stderr,
                flush=True,
            )

    # ── Filter to only new chunks (already have stable content-hash ref_ids) ──
    all_new_chunks: List[Dict] = [
        chunk
        for file_chunks in all_chunks
        for chunk in file_chunks
        if chunk["ref_id"] not in existing_ids
    ]
    skipped = sum(len(cs) for cs in all_chunks) - len(all_new_chunks)
    total_new = len(all_new_chunks)
    batch_num = 0

    # ── Concurrent embed + write (CONCURRENCY groups at a time) ──────────────
    # Split all_new_chunks into groups of CONCURRENCY*bs chunks.
    # For each group: asyncio.gather all embed calls (CONCURRENCY parallel
    # HTTP requests to LM Studio), then asyncio.gather all Postgres writes.
    from embedding_service import _CONCURRENCY as CONCURRENCY

    window = bs * CONCURRENCY  # e.g. 64 * 4 = 256 chunks per round
    n_rounds = (total_new + window - 1) // window

    for round_idx in range(n_rounds):
        group = all_new_chunks[round_idx * window : (round_idx + 1) * window]
        sub_bufs = [group[i : i + bs] for i in range(0, len(group), bs)]
        actual_concurrent = len(sub_bufs)

        # ── Embed phase: all sub-buffers sent to LM Studio concurrently ───
        t_embed = time.time()
        print(
            f"[lm-proxy:indexer] Embedding round {round_idx + 1}/{n_rounds} "
            f"— {actual_concurrent} concurrent batches — "
            f"{total_indexed + len(group)}/{total_new} chunks…",
            file=sys.stderr,
            flush=True,
        )
        embedded_bufs = await asyncio.gather(
            *[_embed_buffer(buf, embedding_svc) for buf in sub_bufs]
        )
        embed_ms = (time.time() - t_embed) * 1000

        # ── Write phase: all embedded sub-buffers written to Postgres concurrently
        write_counts = await asyncio.gather(
            *[_write_buffer(buf, target_dir, project_id) for buf in embedded_bufs]
        )
        n_written = sum(write_counts)
        total_indexed += n_written
        print(
            f"[lm-proxy:indexer]   embed={embed_ms:.0f}ms  wrote {n_written} chunks",
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

    asyncio.run(
        index_project(
            args.target,
            args.project_id,
            manifest_data,
            rebuild=args.rebuild,
            cleanup_only=args.cleanup_only,
        )
    )

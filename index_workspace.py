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
    python index_workspace.py <target_dir> <project_id> --manifest-file <path>
"""
import sys
import os

import asyncio
import json
import time
from typing import List, Dict
from dotenv import load_dotenv

_base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_base_dir, ".env"))
sys.path.insert(0, _base_dir)

# Import memory/embedding modules AFTER env vars are set
import memory_store
import memory_bootstrap
from embedding_service import get_embedding_service

# AST-chunk size: target upper bound for native ts_pack chunks.
CHUNK_MAX_BYTES = 4_000  # bytes — passed as chunk_max_size to ProcessConfig
# Line-window fallback for files ts_pack cannot parse (config/docs/data).
CHUNK_LINES     = 60    # lines per window
OVERLAP_LINES   = 10    # overlap between windows
MANIFEST_BATCH  = 50    # files per interleaved cycle
MAX_FILE_BYTES  = 1_000_000  # skip source files > 1 MB

# Extensions that always use the line-window fallback (no AST structure).
_FALLBACK_EXTS = {
    "yaml", "yml", "toml", "json", "md", "txt",
    "sh", "bash", "zsh", "fish", "sql", "graphql",
    "tf", "hcl", "r", "jl",
}


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
    return f"{project_id}:{rel_path}:{digest}"


def _read_and_chunk(abs_path: str, rel_path: str, project_id: str) -> List[Dict]:
    """Read *abs_path* and return chunk dicts with stable ref_ids.

    Uses ts_pack's native chunker (process() with chunk_max_size) for all
    languages ts_pack can detect.  Chunks with parse errors (has_error_nodes)
    are skipped.  Falls back to a line-window for config/doc files or when
    ts_pack returns no chunks.

    Each returned dict:
      ref_id   — content-hash stable ID (project_id:rel_path:sha256[:14])
      text     — chunk content with "// File: ..." header prepended
      metadata — {file, project_id, symbols, start_line, end_line, docstrings,
                   context_path}
    """
    import tree_sitter_language_pack as ts_pack

    ext = abs_path.rsplit(".", 1)[-1].lower() if "." in abs_path else ""

    # Use ts_pack.detect_language for language detection — covers 156 languages.
    # Skip extension-only fallback files unless no language detected.
    lang: str | None = None
    if ext not in _FALLBACK_EXTS:
        lang = ts_pack.detect_language(abs_path)

    # Nothing to do: unknown file type and not a line-window fallback extension.
    if lang is None and ext not in _FALLBACK_EXTS:
        return []

    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            source = fh.read()
    except OSError:
        return []

    if len(source) > MAX_FILE_BYTES or not source.strip():
        return []

    file_header = f"// File: {rel_path}\n"
    chunks: List[Dict] = []

    # ── Native ts_pack chunking ───────────────────────────────────────────────
    if lang:
        try:
            config = ts_pack.ProcessConfig(lang, chunk_max_size=CHUNK_MAX_BYTES)
            result = ts_pack.process(source, config)
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
                cid  = _chunk_id(project_id, rel_path, chunk.get("start_byte", 0), content)
                chunks.append({
                    "ref_id":   cid,
                    "text":     text,
                    "metadata": {
                        "file":         rel_path,
                        "project_id":   project_id,
                        "symbols":      cmeta.get("symbols_defined", []),
                        "start_line":   chunk.get("start_line", 0) + 1,
                        "end_line":     chunk.get("end_line", 0) + 1,
                        "docstrings":   cmeta.get("docstrings", []),
                        "context_path": cmeta.get("context_path", []),
                    },
                })
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
            cid  = _chunk_id(project_id, rel_path, i, text)
            chunks.append({
                "ref_id":   cid,
                "text":     text,
                "metadata": {"file": rel_path, "project_id": project_id},
            })
            i += CHUNK_LINES - OVERLAP_LINES

    return chunks



async def chunk_file(abs_path: str, rel_path: str, project_id: str) -> List[Dict]:
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


async def index_project(target_dir: str, project_id: str, manifest: List[Dict]) -> int:
    """
    Chunk, embed, and write Chunk nodes for every file in *manifest*.

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
    embedding_svc = get_embedding_service()

    bs            = embedding_svc.effective_batch_size
    total_files   = len(manifest)
    total_indexed = 0
    skipped       = 0
    buffer: list  = []

    print(
        f"[lm-proxy:indexer] Semantic phase — {total_files} files "
        f"(device={embedding_svc._device}, embed_batch={bs})",
        file=sys.stderr, flush=True,
    )

    # ── Parallel chunking (I/O-bound reads) ─────────────────────────────────
    t_chunk = time.time()
    all_chunks: List[List[Dict]] = await asyncio.gather(
        *[chunk_file(e["abs_path"], e["rel_path"], project_id) for e in manifest]
    )
    print(
        f"[lm-proxy:indexer] Chunked {total_files} files in "
        f"{time.time() - t_chunk:.2f}s",
        file=sys.stderr, flush=True,
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
            print(f"[lm-proxy:indexer] WARN: could not fetch existing ids: {exc}",
                  file=sys.stderr, flush=True)
    print(
        f"[lm-proxy:indexer] {len(existing_ids)} chunks already indexed — skipping unchanged",
        file=sys.stderr, flush=True,
    )

    # ── Filter to only new chunks (already have stable content-hash ref_ids) ─
    all_new_chunks: List[Dict] = [
        chunk
        for file_chunks in all_chunks
        for chunk in file_chunks
        if chunk["ref_id"] not in existing_ids
    ]
    skipped = sum(len(cs) for cs in all_chunks) - len(all_new_chunks)
    total_new = len(all_new_chunks)
    batch_num  = 0

    # ── Concurrent embed + write (CONCURRENCY groups at a time) ──────────────
    # Split all_new_chunks into groups of CONCURRENCY*bs chunks.
    # For each group: asyncio.gather all embed calls (CONCURRENCY parallel
    # HTTP requests to LM Studio), then asyncio.gather all Postgres writes.
    from embedding_service import _CONCURRENCY as CONCURRENCY
    window = bs * CONCURRENCY          # e.g. 64 * 4 = 256 chunks per round
    n_rounds = (total_new + window - 1) // window

    for round_idx in range(n_rounds):
        group = all_new_chunks[round_idx * window : (round_idx + 1) * window]
        sub_bufs = [group[i : i + bs] for i in range(0, len(group), bs)]
        actual_concurrent = len(sub_bufs)

        # ── Embed phase: all sub-buffers sent to LM Studio concurrently ───
        t_embed = time.time()
        print(
            f"[lm-proxy:indexer] Embedding round {round_idx+1}/{n_rounds} "
            f"— {actual_concurrent} concurrent batches — "
            f"{total_indexed + len(group)}/{total_new} chunks…",
            file=sys.stderr, flush=True,
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
            f"[lm-proxy:indexer]   embed={embed_ms:.0f}ms  "
            f"wrote {n_written} chunks",
            file=sys.stderr, flush=True,
        )

    elapsed = time.time() - t0
    print(
        f"[lm-proxy:indexer] Done — {total_indexed} new / {skipped} skipped / "
        f"{total_files} files in {elapsed:.2f}s",
        file=sys.stderr, flush=True,
    )
    return total_indexed




# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GraphRAG semantic indexer")
    parser.add_argument("target",          help="Absolute project root path")
    parser.add_argument("project_id",      help="12-char project hash ID")
    parser.add_argument("--manifest-file", required=True, help="Path to JSON manifest")
    args = parser.parse_args()

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
        file=sys.stderr, flush=True,
    )

    asyncio.run(index_project(args.target, args.project_id, manifest_data))

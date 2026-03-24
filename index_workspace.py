#!/usr/bin/env python3
"""Semantic indexing worker for GraphRAG pipeline.

Consumes a JSON manifest of source files, chunks them with a pure-Python
strategy, generates local embeddings (MPS-preferred, CPU fallback), and
writes Chunk nodes to Neo4j.

Design constraints:
- Does NOT import tree_sitter_language_pack. The Rust extension owns a
  tokio runtime + rayon thread pool whose allocators conflict with torch's
  Metal (MPS) command queue, causing SIGSEGV. Chunking here is pure-Python.
- Single process. model.encode() is never called concurrently.
- sentence_transformers is the very first import (before any other native lib)
  to own Metal context initialisation.

Usage:
    python index_workspace.py <target_dir> <project_id> --manifest-file <path>
"""
import sys
import os

# sentence_transformers must be the first native-lib import to own
# the process's tokenizer/torch state before any other module loads.
from sentence_transformers import SentenceTransformer  # noqa: E402

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

# ── Tuning ──────────────────────────────────────────────────────────────────
CHUNK_LINES    = 60   # Fixed-size fallback chunk (lines)
OVERLAP_LINES  = 10   # Line overlap between fallback chunks
MANIFEST_BATCH = 50   # Files consumed per interleaved cycle
MAX_FILE_BYTES = 1_000_000  # Skip source files > 1 MB

# EmbeddingService owns batch size (16 on MPS, 64 on CPU).


# ── Pure-Python chunking (no Rust extension) ─────────────────────────────────

# Known code file extensions → keep for semantic chunking
_CODE_EXTS = {
    "py", "js", "ts", "jsx", "tsx", "swift", "rs", "go", "java", "kt",
    "cpp", "c", "h", "hpp", "cs", "rb", "php", "scala", "m", "mm",
    "sh", "bash", "zsh", "fish", "lua", "r", "jl", "sql", "graphql",
    "tf", "hcl", "yaml", "yml", "toml", "json", "md", "txt",
}


def _read_and_chunk(abs_path: str, rel_path: str) -> List[str]:
    """Read *abs_path* and return a list of text chunk strings.

    Uses fixed-size line-based chunking with overlap. No Rust dependency.
    """
    ext = abs_path.rsplit(".", 1)[-1].lower() if "." in abs_path else ""
    if ext not in _CODE_EXTS:
        return []

    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            source = fh.read()
    except OSError:
        return []

    if len(source) > MAX_FILE_BYTES or not source.strip():
        return []

    basename = os.path.basename(abs_path)
    lines    = source.splitlines()
    chunks   = []
    i = 0
    while i < len(lines):
        block = lines[i : i + CHUNK_LINES]
        if not block:
            break
        header = f"// File: {rel_path}\n"
        chunks.append(header + "\n".join(block))
        i += CHUNK_LINES - OVERLAP_LINES

    return chunks


async def chunk_file(abs_path: str, rel_path: str) -> List[str]:
    """Async wrapper — runs blocking read+chunk in a worker thread."""
    return await asyncio.to_thread(_read_and_chunk, abs_path, rel_path)


# ── Main indexing coroutine ───────────────────────────────────────────────────

async def _flush_buffer(
    buffer: list,
    embedding_svc,
    target_dir: str,
    project_id: str,
) -> int:
    """Embed and write one full (or final partial) buffer. Returns chunk count written."""
    if not buffer:
        return 0
    texts   = [it["text"] for it in buffer]
    vectors = embedding_svc.embed_batch(texts)
    for k, vec in enumerate(vectors):
        buffer[k]["vector"] = vec
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
    - Files are chunked one at a time (bounded memory, no Rust ext in this process).
    - Chunks are accumulated in a cross-file rolling buffer.
    - embed_batch() fires only when the buffer hits full batch size (128),
      ensuring BLAS on the M3 Max's 12 p-cores is always saturated.
    - The final partial buffer is flushed at the end.

    Returns total chunks written to Neo4j.
    """
    t0 = time.time()
    await memory_bootstrap.bootstrap_schema()
    await memory_store.open_pool()   # initialize runtime Postgres pool for writes
    embedding_svc = get_embedding_service()


    bs            = embedding_svc.effective_batch_size
    total_files   = len(manifest)
    total_indexed = 0
    files_done    = 0
    buffer: list  = []   # rolling cross-file chunk accumulator

    print(
        f"[lm-proxy:indexer] Semantic phase — {total_files} files "
        f"(device={embedding_svc._device}, embed_batch={bs})",
        file=sys.stderr, flush=True,
    )

    for entry in manifest:
        chunks = await chunk_file(entry["abs_path"], entry["rel_path"])
        files_done += 1

        if not chunks:
            continue

        rel = entry["rel_path"]
        for idx, text in enumerate(chunks):
            buffer.append({
                "ref_id":   f"{project_id}:{rel}::{idx}",
                "text":     text,
                "metadata": {"file": rel, "project_id": project_id},
            })

            # Flush when we have a full BLAS batch
            if len(buffer) >= bs:
                total_indexed += await _flush_buffer(buffer, embedding_svc, target_dir, project_id)
                buffer = []

        if files_done % 50 == 0 or files_done == total_files:
            elapsed = time.time() - t0
            print(
                f"[lm-proxy:indexer] {files_done}/{total_files} files — "
                f"{total_indexed} chunks — {elapsed:.1f}s",
                file=sys.stderr, flush=True,
            )

    # Flush any remaining chunks (final partial batch)
    if buffer:
        total_indexed += await _flush_buffer(buffer, embedding_svc, target_dir, project_id)

    elapsed = time.time() - t0
    print(
        f"[lm-proxy:indexer] Done — {total_indexed} chunks / "
        f"{files_done} files in {elapsed:.2f}s",
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

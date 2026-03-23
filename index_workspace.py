#!/usr/bin/env python3
"""
index_workspace.py - Semantic Codebase Search Indexer

Parses a workspace directory, splits source files into manageable chunks,
fetches vector embeddings using the proxy's `memory_retrieval` logic,
and saves them to the `codebase_embeddings` Postgres table.

Usage:
  python index_workspace.py /path/to/project [project_id]
"""

import asyncio
import os
import sys
import hashlib
from typing import List
from dotenv import load_dotenv

# Load env very first so imported proxy modules resolve correctly
load_dotenv("/Users/michaelmarler/Projects/rest_proxy/.env")

# Ensure we can import proxy modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Crucial: Override the embedding base URL to point directly to LM Studio
# since the proxy itself may not be running while indexing.
import memory_retrieval
from memory_retrieval import get_embedding
memory_retrieval._EMBEDDING_BASE_URL = os.getenv("LM_BASE", "http://127.0.0.1:1234")
memory_retrieval._ENABLE_EMBEDDINGS = True

import memory_store
memory_store._ENABLE_EMBEDDINGS = True
memory_store._ENABLE_PERSISTENCE = True

import memory_bootstrap
memory_bootstrap._ENABLE_EMBEDDINGS = True
memory_bootstrap._ENABLE_PERSISTENCE = True

# We'll use skeleton_extractor's SourceKitten hooks to try to chunk Swift smartly
# if we wanted, but for V1 we can also just do line-based chunking with overlap
# to ensure we don't drop context.
CHUNK_LINES = 60
OVERLAP_LINES = 15

async def chunk_file(filepath: str) -> List[str]:
    """Reads a file and yields chunks of lines."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Skipping {filepath}: {e}", file=sys.stderr)
        return []

    chunks = []
    i = 0
    while i < len(lines):
        chunk = lines[i:i + CHUNK_LINES]
        if not chunk:
            break
        # Add basic file path context to the top of EVERY chunk so the embedding
        # knows *where* this code lives.
        header = f"// File: {os.path.basename(filepath)}\n"
        chunks.append(header + "".join(chunk))
        i += (CHUNK_LINES - OVERLAP_LINES)
        
    return chunks

async def index_project(target_dir: str, project_id: str):
    print(f"Bootstrapping Graph database...", file=sys.stderr)
    await memory_bootstrap.bootstrap_schema()

    files_to_index = []
    for root, dirs, files in os.walk(target_dir):
        # basic skips
        # Skip common non-source and data directories
        dirs[:] = [d for d in dirs if d not in {".git", "build", "Pods", ".build", "models", "weights", "checkpoints", "venv", "node_modules", "DerivedData"}]
            
        for name in files:
            # Expanded coverage for config, docs, and diverse source types
            if name.endswith((".swift", ".py", ".js", ".ts", ".jsx", ".tsx", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".txt", ".c", ".cpp", ".h", ".hpp", ".sh", ".sql")):
                filepath = os.path.join(root, name)
                try:
                    # Skip files larger than 1MB (likely datasets, binaries, or large generated files)
                    if os.path.getsize(filepath) > 1 * 1024 * 1024:
                        continue
                    files_to_index.append(filepath)
                except OSError:
                    continue

    print(f"Found {len(files_to_index)} files to index.", file=sys.stderr)
    
    total_chunks = 0
    # Process up to 10 chunks concurrently so we don't overwhelm LM Studio
    # but still get a massive throughput boost
    sem = asyncio.Semaphore(10)
    
    async def process_chunk(project_id: str, rel_path: str, chunk_idx: int, chunk_text: str):
        async with sem:
            vector = await get_embedding(chunk_text)
            if not vector:
                print(f"  Warning: Failed to get embedding for {rel_path} chunk {chunk_idx}", file=sys.stderr)
                return False
                
            # Use Neo4j vector store instead of Postgres
            await memory_store.insert_embedding(
                session_id=project_id,
                ref_id=f"{project_id}:{rel_path}::{chunk_idx}",
                ref_type="code_chunk",
                compact_text=chunk_text,
                vector=vector,
                project_path=target_dir,
                metadata={"file": rel_path, "project_id": project_id}
            )
            return True

    tasks = []
    print(f"Queueing {len(files_to_index)} files for parallel embedding...", file=sys.stderr)
    for filepath in files_to_index:
        chunks = await chunk_file(filepath)
        if not chunks:
            continue
            
        rel_path = os.path.relpath(filepath, target_dir)
        for i, chunk in enumerate(chunks):
            tasks.append(process_chunk(project_id, rel_path, i, chunk))

    print(f"Executing {len(tasks)} embedding tasks concurrently...", file=sys.stderr)
    results = await asyncio.gather(*tasks)
    total_chunks = sum(1 for r in results if r)
            
    print(f"Done! Indexed {total_chunks} total chunks into codebase_embeddings for project {project_id}.", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python index_workspace.py <target_directory> [project_id]", file=sys.stderr)
        sys.exit(1)
        
    target = os.path.abspath(sys.argv[1])
    # Compute a deterministic project ID based on the directory path if not provided
    pid = sys.argv[2] if len(sys.argv) > 2 else hashlib.md5(target.encode()).hexdigest()[:12]
    
    asyncio.run(index_project(target, pid))

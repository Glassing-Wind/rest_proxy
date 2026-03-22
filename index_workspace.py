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
memory_store._PG_DSN = os.getenv("LM_PROXY_PG_DSN", "")

import memory_bootstrap
memory_bootstrap._ENABLE_EMBEDDINGS = True
memory_bootstrap._ENABLE_PERSISTENCE = True
memory_bootstrap._PG_DSN = os.getenv("LM_PROXY_PG_DSN", "")

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
        print(f"Skipping {filepath}: {e}")
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
    print(f"Bootstrapping database...")
    await memory_bootstrap.bootstrap_schema()
    
    # We must open the connection pool explicitly since we're not running the FastAPI app
    print("Opening postgres pool...")
    await memory_store.open_pool()

    files_to_index = []
    for root, _, files in os.walk(target_dir):
        # basic skips
        if ".git" in root or "build" in root or "Pods" in root or ".build" in root:
            continue
            
        for name in files:
            if name.endswith((".swift", ".py", ".js", ".ts", ".jsx", ".tsx", ".md")):
                files_to_index.append(os.path.join(root, name))

    print(f"Found {len(files_to_index)} files to index.")
    
    total_chunks = 0
    # Process up to 10 chunks concurrently so we don't overwhelm LM Studio
    # but still get a massive throughput boost
    sem = asyncio.Semaphore(10)
    
    async def process_chunk(project_id: str, rel_path: str, chunk_idx: int, chunk_text: str):
        async with sem:
            vector = await get_embedding(chunk_text)
            if not vector:
                print(f"  Warning: Failed to get embedding for {rel_path} chunk {chunk_idx}")
                return False
                
            await memory_store.insert_codebase_embedding(
                project_id=project_id,
                file_path=rel_path,
                chunk_index=chunk_idx,
                content=chunk_text,
                vector=vector,
            )
            return True

    tasks = []
    print(f"Queueing {len(files_to_index)} files for parallel embedding...")
    for filepath in files_to_index:
        chunks = await chunk_file(filepath)
        if not chunks:
            continue
            
        rel_path = os.path.relpath(filepath, target_dir)
        for i, chunk in enumerate(chunks):
            tasks.append(process_chunk(project_id, rel_path, i, chunk))

    print(f"Executing {len(tasks)} embedding tasks concurrently...")
    results = await asyncio.gather(*tasks)
    total_chunks = sum(1 for r in results if r)
            
    print(f"Done! Indexed {total_chunks} total chunks into codebase_embeddings for project {project_id}.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python index_workspace.py <target_directory> [project_id]")
        sys.exit(1)
        
    target = os.path.abspath(sys.argv[1])
    # Compute a deterministic project ID based on the directory path if not provided
    pid = sys.argv[2] if len(sys.argv) > 2 else hashlib.md5(target.encode()).hexdigest()[:12]
    
    asyncio.run(index_project(target, pid))

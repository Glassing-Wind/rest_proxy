#!/usr/bin/env python3
import asyncio
import os
import sys
import hashlib
import json
import time
from typing import List, Dict
from pathlib import Path
from dotenv import load_dotenv

# Load env from rest_proxy root
base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(base_dir, ".env"))
sys.path.append(base_dir)

import tree_sitter_language_pack as ts_pack
import memory_store
import memory_bootstrap
from embedding_service import get_embedding_service

# Configuration
CHUNK_LINES = 60
OVERLAP_LINES = 15
EMBEDDING_BATCH_SIZE = 64

async def chunk_file(filepath: str) -> List[str]:
    """Reads a file and yields chunks using ts_pack for syntax-aware segmentation."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
    except Exception as e:
        return []

    lang_name = ts_pack.detect_language(filepath)
    if lang_name:
        try:
            config = ts_pack.ProcessConfig(language=lang_name, chunk_max_size=1000)
            result = ts_pack.process(source, config)
            chunks = []
            for c in result.get("chunks", []):
                text = c.get("content", "")
                meta = c.get("metadata", {})
                symbols = meta.get("symbols_defined", [])
                context = meta.get("context_path", [])
                
                symbols_str = f"\n[Symbols: {', '.join(symbols)}]" if symbols else ""
                context_str = f"\n[Context: {' > '.join(context)}]" if context else ""
                file_header = f"// File: {os.path.basename(filepath)}\n"
                chunks.append(f"{file_header}{text}{symbols_str}{context_str}")
            if chunks: return chunks
        except Exception: pass

    # Fallback: Line-based chunking
    lines = source.splitlines()
    chunks = []
    i = 0
    while i < len(lines):
        chunk_lines = lines[i:i + CHUNK_LINES]
        if not chunk_lines: break
        header = f"// File: {os.path.basename(filepath)}\n"
        chunks.append(header + "\n".join(chunk_lines))
        i += CHUNK_LINES - OVERLAP_LINES
    return chunks

async def index_project(target_dir: str, project_id: str, manifest: List[Dict]):
    """Semantic indexing phase consuming a pre-built manifest."""
    start_time = time.time()
    await memory_bootstrap.bootstrap_schema()
    
    embedding_svc = get_embedding_service()
    all_chunks = []
    
    # 1. Chunking Phase
    for i, entry in enumerate(manifest):
        abs_path = entry["abs_path"]
        rel_path = entry["rel_path"]
        
        chunks = await chunk_file(abs_path)
        for idx, text in enumerate(chunks):
            all_chunks.append({
                "ref_id": f"{project_id}:{rel_path}::{idx}",
                "text": text,
                "metadata": {"file": rel_path, "project_id": project_id}
            })
            
    # 2. Embedding & Ingestion Phase (Batched)
    total_indexed = 0
    for i in range(0, len(all_chunks), EMBEDDING_BATCH_SIZE):
        batch = all_chunks[i : i + EMBEDDING_BATCH_SIZE]
        batch_texts = [c["text"] for c in batch]
        
        # Local In-Process Embedding
        embeddings = embedding_svc.embed_batch(batch_texts, batch_size=EMBEDDING_BATCH_SIZE)
        
        for idx, vector in enumerate(embeddings):
            batch[idx]["vector"] = vector
            
        # Batched Neo4j Injection
        count = await memory_store.insert_embeddings_batch(
            session_id=project_id,
            project_id=project_id,
            batch=batch,
            project_path=target_dir
        )
        total_indexed += count
        
    embed_time = time.time() - start_time
    print(f"Semantic phase complete: {total_indexed} chunks in {embed_time:.2f}s", file=sys.stderr)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("project_id")
    parser.add_argument("--manifest-file", required=True)
    args = parser.parse_args()
    
    if os.path.exists(args.manifest_file):
        with open(args.manifest_file, "r") as f:
            manifest_data = json.load(f)
        asyncio.run(index_project(args.target, args.project_id, manifest_data))
    else:
        sys.exit(1)

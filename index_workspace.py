#!/usr/bin/env python3
"""
index_workspace.py - Native Semantic Codebase Search Indexer

Uses tree-sitter-language-pack for syntax-aware code chunking.
Each chunk is enriched with defined symbols and context path metadata.
"""

import asyncio
import os
import sys
import hashlib
from typing import List, Dict
from dotenv import load_dotenv
import tree_sitter_language_pack as ts_pack

# Load env
load_dotenv("/Users/michaelmarler/Projects/rest_proxy/.env")
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import memory_retrieval
from memory_retrieval import get_embedding
import memory_store
import memory_bootstrap

memory_retrieval._EMBEDDING_BASE_URL = os.getenv("LM_BASE", "http://127.0.0.1:1234")
memory_retrieval._ENABLE_EMBEDDINGS = True
memory_store._ENABLE_EMBEDDINGS = True
memory_store._ENABLE_PERSISTENCE = True
memory_bootstrap._ENABLE_EMBEDDINGS = True
memory_bootstrap._ENABLE_PERSISTENCE = True

TS_LANG_MAP = {
    ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".hh": "cpp",
    ".c": "c", ".h": "c",
    ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".rb": "ruby", 
    ".java": "java", ".php": "php", ".sh": "bash", ".sql": "sql",
    ".md": "markdown", ".json": "json", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml",
    ".lua": "lua", ".proto": "proto", ".graphql": "graphql", ".css": "css", ".py": "python", ".xml": "xml"
}

CHUNK_LINES = 60
OVERLAP_LINES = 15

async def chunk_file(filepath: str) -> List[str]:
    """Reads a file and yields chunks using ts_pack for syntax-aware segmentation."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
    except Exception as e:
        print(f"Skipping {filepath}: {e}", file=sys.stderr)
        return []

    ext = os.path.splitext(filepath)[1].lower()
    lang_name = TS_LANG_MAP.get(ext)
    
    if lang_name:
        try:
            # Native syntax-aware chunking
            config = ts_pack.ProcessConfig(language=lang_name, chunk_max_size=1000)
            result = ts_pack.process(source, config)
            
            chunks = []
            # result is a dict with 'chunks' list
            for c in result.get("chunks", []):
                text = c.get("content", "")
                meta = c.get("metadata", {})
                
                symbols = meta.get("symbols_defined", [])
                context = meta.get("context_path", [])
                
                symbols_str = f"\n[Symbols: {', '.join(symbols)}]" if symbols else ""
                context_str = f"\n[Context: {' > '.join(context)}]" if context else ""
                file_header = f"// File: {os.path.basename(filepath)}\n"
                
                chunks.append(f"{file_header}{text}{symbols_str}{context_str}")
                
            if chunks:
                return chunks
        except Exception:
            pass

    # Fallback: Line-based chunking
    lines = source.splitlines()
    chunks = []
    i = 0
    while i < len(lines):
        chunk_lines = lines[i:i + CHUNK_LINES]
        if not chunk_lines:
            break
        header = f"// File: {os.path.basename(filepath)}\n"
        chunks.append(header + "\n".join(chunk_lines))
        i += CHUNK_LINES - OVERLAP_LINES
    return chunks

async def index_project(target_dir: str, project_id: str):
    print(f"Bootstrapping Graph database...", file=sys.stderr)
    await memory_bootstrap.bootstrap_schema()

    files_to_index = []
    for root, _, files in os.walk(target_dir):
        for file in files:
            if any(file.endswith(ext) for ext in TS_LANG_MAP.keys()):
                files_to_index.append(os.path.join(root, file))

    sem = asyncio.Semaphore(10)
    async def process_chunk(project_id, rel_path, chunk_idx, chunk_text):
        async with sem:
            vector = await get_embedding(chunk_text)
            if not vector:
                return False
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

    print(f"Executing {len(tasks)} embedding tasks...", file=sys.stderr)
    results = await asyncio.gather(*tasks)
    total_chunks = sum(1 for r in results if r)
    print(f"Done! Indexed {total_chunks} chunks into codebase_embeddings for project {project_id}.", file=sys.stderr)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python index_workspace.py <target_directory> [project_id]")
        sys.exit(1)
    target = os.path.abspath(sys.argv[1])
    pid = sys.argv[2] if len(sys.argv) > 2 else hashlib.md5(target.encode()).hexdigest()[:12]
    asyncio.run(index_project(target, pid))

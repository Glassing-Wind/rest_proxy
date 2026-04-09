"""tools/docs/pipeline.py — documentation indexing pipeline."""

import hashlib
import json
import sys
import time
from typing import Any, Dict, List, Optional

import memory.store as memory_store
import memory.bootstrap as memory_bootstrap
from embedding_service import get_embedding_service

from tools.brain.docs.chunking import chunk_content
from tools.brain.docs.crawl import crawl_pages
from tools.brain.docs.discovery import discover_pages


def _chunk_id(url: str, idx: int) -> str:
    raw = f"{url}::{idx}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def index_docs(
    urls: List[str],
    topic: str,
    discover: bool = False,
    url_filters: Optional[List[str]] = None,
) -> int:
    """Full pipeline: discover → crawl → extract → chunk → embed → upsert."""
    await memory_bootstrap.bootstrap_schema()
    await memory_store.open_pool()
    embedding_svc = get_embedding_service()

    # ── Fast-fail: verify embedding service is reachable before crawling ──────
    try:
        probe = await embedding_svc.embed_batch_async(["ping"])
        if not probe or not probe[0]:
            raise ValueError("embedding service returned empty vector")
    except Exception as exc:
        print(
            f"[doc-indexer] FATAL: embedding service unavailable — {exc}\n"
            "  Make sure LM Studio (or your configured embedding server) is running "
            "before starting a doc index job.",
            file=sys.stderr,
            flush=True,
        )
        return 0

    t0 = time.time()
    total_chunks = 0

    # Expand URLs with llms.txt / sitemap discovery (opt-in)
    if discover:
        all_pages: List[str] = []
        for url in urls:
            discovered = await discover_pages(url, url_filters=url_filters)
            all_pages.extend(discovered)
        # Deduplicate but preserve all distinct seed URLs
        all_pages = list(dict.fromkeys(all_pages))
    else:
        all_pages = list(dict.fromkeys(urls))
        if url_filters:
            all_pages = [u for u in all_pages if not any(f in u for f in url_filters)]

    print(
        f"[doc-indexer] Crawling {len(all_pages)} pages for topic='{topic}'",
        flush=True,
    )

    # Crawl
    crawled = await crawl_pages(all_pages)
    print(f"[doc-indexer] Crawled {len(crawled)} pages successfully", flush=True)

    # Extract + chunk into rolling buffer
    buffer: List[Dict] = []
    bs = embedding_svc.effective_batch_size

    async def flush(buf: List[Dict]) -> int:
        if not buf:
            return 0
        texts = [b["text"] for b in buf]
        vectors = await embedding_svc.embed_batch_async(texts)
        written = await _upsert_doc_chunks_batch(buf, vectors, topic)
        return written

    for page in crawled:
        fmt = "xml" if "xml" in page else "markdown"
        text = page.get("xml") or page.get("markdown", "")
        chunks = chunk_content(text, page["url"], page["title"], fmt=fmt)
        for idx, chunk in enumerate(chunks):
            buffer.append(
                {
                    "chunk_id": _chunk_id(page["url"], idx),
                    "source_url": page["url"],
                    "title": page["title"],
                    "chunk_index": idx,
                    "text": chunk["text"],
                    "context_path": chunk["context_path"],
                    "domain": chunk.get("domain", ""),
                    "doc_type": chunk.get("doc_type", ""),
                    "section_title": chunk.get("section_title", ""),
                }
            )
            if len(buffer) >= bs:
                total_chunks += await flush(buffer)
                buffer = []

    if buffer:
        total_chunks += await flush(buffer)

    elapsed = time.time() - t0
    print(
        f"[doc-indexer] Done — {total_chunks} chunks / {len(crawled)} pages "
        f"in {elapsed:.2f}s  topic={topic}",
        flush=True,
    )
    return total_chunks


async def index_authored_document(
    *,
    topic: str,
    url: str,
    title: str,
    content: str,
    fmt: str = "markdown",
    replace_existing: bool = True,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Chunk and index authored documentation content into doc_embeddings.

    Intended for agent-written guides or curated notes that should become
    searchable via search_documentation().
    """
    await memory_bootstrap.bootstrap_schema()
    await memory_store.open_pool()

    if not memory_store._pg_pool_available():
        raise RuntimeError("Postgres pool not available — check LM_PROXY_PG_DSN in .env")

    if not content.strip():
        raise ValueError("Documentation content is empty.")

    embedding_svc = get_embedding_service()
    chunks = chunk_content(content, url, title, fmt=fmt)
    if not chunks:
        raise ValueError("No indexable chunks were produced from the supplied content.")

    items = []
    for idx, chunk in enumerate(chunks):
        items.append(
            {
                "chunk_id": _chunk_id(url, idx),
                "source_url": url,
                "title": title,
                "chunk_index": idx,
                "text": chunk["text"],
                "context_path": chunk.get("context_path") or [],
                "domain": chunk.get("domain", ""),
                "doc_type": chunk.get("doc_type", ""),
                "section_title": chunk.get("section_title", ""),
                "extra_metadata": extra_metadata or {},
            }
        )

    texts = [item["text"] for item in items]
    vectors = await embedding_svc.embed_batch_async(texts)

    if replace_existing:
        await _delete_doc_chunks(topic=topic, url=url)

    written = await _upsert_doc_chunks_batch(items, vectors, topic)
    return {
        "topic": topic,
        "url": url,
        "title": title,
        "chunks": written,
    }


async def _delete_doc_chunks(*, topic: str, url: str) -> int:
    """Delete existing chunks for a single topic/url pair before re-indexing."""
    if not memory_store._pg_pool_available():
        return 0
    async with memory_store._pg_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM doc_embeddings WHERE source = %s AND url = %s",
                (topic, url),
            )
            deleted = cur.rowcount if cur.rowcount is not None else 0
        await conn.commit()
    return deleted


async def _upsert_doc_chunks_batch(
    items: List[Dict], vectors: List[List[float]], topic: str
) -> int:
    """Batch-upsert doc chunks into doc_embeddings — one executemany per flush, one round-trip."""
    if not memory_store._pg_pool_available() or not items:
        return 0
    rows = []
    for item, vector in zip(items, vectors):
        if not vector:
            continue
        vec_str = "[" + ",".join(str(v) for v in vector) + "]"
        metadata_payload = {
            "title": item.get("title", ""),
            "topic": topic,
            "context_path": item.get("context_path", []),
            "domain": item.get("domain", ""),
            "doc_type": item.get("doc_type", ""),
            "section_title": item.get("section_title", ""),
        }
        extra_metadata = item.get("extra_metadata")
        if isinstance(extra_metadata, dict) and extra_metadata:
            metadata_payload.update(extra_metadata)
        metadata = json.dumps(metadata_payload)
        rows.append(
            (
                item["chunk_id"],
                topic,
                item["source_url"],
                item["chunk_index"],
                item["text"],
                vec_str,
                metadata,
            )
        )
    if not rows:
        return 0
    try:
        async with memory_store._pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    """
                    INSERT INTO doc_embeddings
                      (chunk_id, source, url, ref_type, chunk_index, content,
                       embedding, metadata)
                    VALUES (%s, %s, %s, 'documentation', %s, %s, %s::vector, %s::jsonb)
                    ON CONFLICT (chunk_id) DO UPDATE
                      SET content    = EXCLUDED.content,
                          embedding  = EXCLUDED.embedding,
                          metadata   = EXCLUDED.metadata,
                          created_at = EXTRACT(EPOCH FROM NOW())
                    """,
                    rows,
                )
        return len(rows)
    except Exception as exc:
        print(f"[doc-indexer] batch upsert error: {exc}", file=sys.stderr, flush=True)
        return 0

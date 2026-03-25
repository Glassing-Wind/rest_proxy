#!/usr/bin/env python3
"""doc_indexer.py – Documentation crawl, extract, chunk, embed, and store pipeline.

Pipeline per URL:
  1. llms.txt discovery  — try {root}/llms.txt → sitemap.xml fallback
  2. Crawl4AI            — async headless browser for JS-rendered docs
  3. Trafilatura         — main-content extraction (strips nav, ads, sidebars)
  4. Chunk               — sliding window (250 lines / 25-line overlap)
  5. Embed               — embedding_service.embed_batch()
  6. Upsert              — doc_embeddings ON CONFLICT (chunk_id) DO UPDATE

CLI:
    python doc_indexer.py --urls-file urls.json --topic "neo4j" --job-id "abc123"
"""
import sys
import os

# sentence_transformers must own the process Metal/BLAS context first
from sentence_transformers import SentenceTransformer  # noqa: E402

import asyncio
import hashlib
import json
import re
import time
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv

_base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_base_dir, ".env"))
sys.path.insert(0, _base_dir)

import memory_store
import memory_bootstrap
from embedding_service import get_embedding_service

# ── Tuning ───────────────────────────────────────────────────────────────────
CHUNK_LINES    = 250        # fallback: target lines per chunk
OVERLAP_LINES  = 25         # fallback: line overlap between chunks
CHUNK_MAX_BYTES = 8_000     # max bytes per section chunk (ts_pack split_markdown)
MAX_PAGE_BYTES = 2_000_000  # skip pages > 2MB of markdown


# ── llms.txt discovery ───────────────────────────────────────────────────────

async def _fetch_url_text(url: str, timeout: int = 10) -> Optional[str]:
    """Lightweight HTTP GET → text, no browser needed."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "DocIndexer/1.0"})
            if r.status_code == 200:
                return r.text
    except Exception:
        pass
    return None


async def discover_pages(seed_url: str) -> List[str]:
    """Given a seed URL, try llms.txt then sitemap.xml to get related doc pages.

    Returns a deduplicated list of URLs to crawl (includes seed_url itself).
    """
    parsed = urlparse(seed_url)
    root   = f"{parsed.scheme}://{parsed.netloc}"
    pages  = [seed_url]

    # 1. llms.txt
    llms_txt = await _fetch_url_text(f"{root}/llms.txt")
    if llms_txt:
        for line in llms_txt.splitlines():
            line = line.strip()
            if line.startswith("http"):
                pages.append(line)
            elif line.startswith("/"):
                pages.append(urljoin(root, line))
        print(f"[doc-indexer] llms.txt found at {root} — {len(pages)} pages", flush=True)
        return list(dict.fromkeys(pages))  # deduplicate, preserve order

    # 2. sitemap.xml (very lightweight parse — just grab <loc> tags)
    sitemap = await _fetch_url_text(f"{root}/sitemap.xml")
    if sitemap:
        locs = re.findall(r"<loc>(https?://[^<]+)</loc>", sitemap)
        # Filter to same domain + paths that look like docs
        doc_hints = {"/docs", "/api", "/reference", "/guide", "/manual", "/learn"}
        filtered  = [
            u for u in locs
            if urlparse(u).netloc == parsed.netloc
            and any(h in u for h in doc_hints)
        ]
        if filtered:
            pages.extend(filtered[:200])  # cap at 200 from sitemap
            print(f"[doc-indexer] sitemap.xml — {len(filtered)} doc pages found", flush=True)
            return list(dict.fromkeys(pages))

    # 3. Just crawl the seed URL itself
    return pages


# ── Crawl ────────────────────────────────────────────────────────────────────

async def crawl_pages(urls: List[str]) -> List[Dict]:
    """Crawl each URL with Crawl4AI. Returns list of {url, html, title}."""
    import fcntl, tempfile
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg     = CrawlerRunConfig(
        page_timeout=20_000,        # ms
        wait_until="domcontentloaded",
        exclude_external_links=True,
    )

    # crawl4ai's RobotsParser opens a shared SQLite DB in __init__.
    # Serialize that init across concurrent doc-crawl subprocesses with a
    # file-based exclusive lock so only one process runs it at a time.
    lock_path = os.path.join(tempfile.gettempdir(), "crawl4ai_init.lock")
    with open(lock_path, "w") as _lf:
        fcntl.flock(_lf.fileno(), fcntl.LOCK_EX)
        try:
            crawler_cm = AsyncWebCrawler(config=browser_cfg)
        finally:
            fcntl.flock(_lf.fileno(), fcntl.LOCK_UN)

    results = []
    async with crawler_cm as crawler:
        for url in urls:
            try:
                r = await crawler.arun(url=url, config=run_cfg)
                md = r.markdown or ""
                if r.success and md:
                    if len(md) > MAX_PAGE_BYTES:
                        print(f"[doc-indexer] skip (too large): {url}", flush=True)
                        continue
                    title = r.metadata.get("title", "") if r.metadata else ""
                    results.append({"url": url, "markdown": md, "title": title})
            except Exception as e:
                print(f"[doc-indexer] crawl error {url}: {e}", file=sys.stderr, flush=True)
    return results


# ── Extract ──────────────────────────────────────────────────────────────────

def chunk_markdown(markdown: str, url: str, title: str) -> List[Dict]:
    """Chunk documentation markdown with ts_pack for heading/code-block-aware splits.

    Uses ts_pack.process('markdown') which natively populates context_path from
    the section-node hierarchy in the markdown AST (added to ts-pack-core).
    Falls back to a simple line-window split if ts_pack is unavailable.
    Returns list of dicts: {text, context_path}.
    """
    if not markdown.strip():
        return []

    header = f"// Source: {url}\n// Title: {title}\n\n"

    try:
        import tree_sitter_language_pack as ts_pack
        config = ts_pack.ProcessConfig('markdown', chunk_max_size=CHUNK_MAX_BYTES)
        result = ts_pack.process(markdown, config)
        chunks = result.get('chunks', [])
        if chunks:
            return [
                {
                    "text":         header + c['content'],
                    "context_path": c.get('metadata', {}).get('context_path') or [],
                }
                for c in chunks
                if c.get('content', '').strip()
                and not c.get('metadata', {}).get('has_error_nodes', False)
            ]
    except Exception as e:
        print(f"[doc-indexer] ts_pack chunk error: {e}", file=sys.stderr, flush=True)

    # Fallback: simple line-window split (no context_path)
    lines  = markdown.splitlines()
    out    = []
    i      = 0
    while i < len(lines):
        chunk = header + "\n".join(lines[i : i + CHUNK_LINES])
        if chunk.strip():
            out.append({"text": chunk, "context_path": []})
        i += CHUNK_LINES - OVERLAP_LINES
    return out


# ── Main pipeline ─────────────────────────────────────────────────────────────

def _chunk_id(url: str, idx: int) -> str:
    raw = f"{url}::{idx}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def index_docs(urls: List[str], topic: str) -> int:
    """Full pipeline: discover → crawl → extract → chunk → embed → upsert."""
    await memory_bootstrap.bootstrap_schema()
    await memory_store.open_pool()
    embedding_svc = get_embedding_service()

    t0          = time.time()
    total_chunks = 0

    # Expand URLs with llms.txt / sitemap discovery
    all_pages: List[str] = []
    for url in urls:
        discovered = await discover_pages(url)
        all_pages.extend(discovered)
    all_pages = list(dict.fromkeys(all_pages))  # deduplicate

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
        texts   = [b["text"] for b in buf]
        vectors = await embedding_svc.embed_batch_async(texts)
        written = 0
        for item, vec in zip(buf, vectors):
            ok = await _upsert_doc_chunk(item, vec, topic)
            if ok:
                written += 1
        return written

    for page in crawled:
        chunks = chunk_markdown(page["markdown"], page["url"], page["title"])
        for idx, chunk in enumerate(chunks):
            buffer.append({
                "chunk_id":     _chunk_id(page["url"], idx),
                "source_url":   page["url"],
                "title":        page["title"],
                "chunk_index":  idx,
                "text":         chunk["text"],
                "context_path": chunk["context_path"],
            })
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


async def _upsert_doc_chunk(item: Dict, vector: List[float], topic: str) -> bool:
    """Upsert one doc chunk into doc_embeddings."""
    if not memory_store._pg_pool_available():
        return False
    try:
        import json as _json
        vec_str = "[" + ",".join(str(v) for v in vector) + "]"
        metadata = _json.dumps({
            "title":        item.get("title", ""),
            "topic":        topic,
            "context_path": item.get("context_path", []),
        })
        async with memory_store._pg_pool.connection() as conn:
            await conn.execute(
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
                (
                    item["chunk_id"], topic, item["source_url"],
                    item["chunk_index"], item["text"], vec_str, metadata,
                ),
            )
        return True
    except Exception as exc:
        print(f"[doc-indexer] upsert error: {exc}", file=sys.stderr, flush=True)
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Documentation indexer")
    parser.add_argument("--urls-file", required=True, help="JSON file with list of URLs")
    parser.add_argument("--topic",     required=True, help="Topic label (e.g. 'neo4j')")
    args = parser.parse_args()

    with open(args.urls_file) as fh:
        urls: List[str] = json.load(fh)

    print(f"[doc-indexer] {len(urls)} seed URLs  topic={args.topic}", flush=True)
    asyncio.run(index_docs(urls, args.topic))

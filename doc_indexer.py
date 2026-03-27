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

    URL scope: discovered URLs are pinned to the seed URL's path prefix so that
    a seed of https://crawlee.dev/python/docs/ never pulls in /js/ or /blog/ pages.
    """
    parsed      = urlparse(seed_url)
    root        = f"{parsed.scheme}://{parsed.netloc}"
    # Normalise the prefix: strip trailing filename so /foo/bar.html → /foo/
    seed_path   = parsed.path
    if not seed_path.endswith("/"):
        seed_path = seed_path.rsplit("/", 1)[0] + "/"
    pages = [seed_url]

    def _in_scope(url: str) -> bool:
        """True iff url is on the same host and starts with the seed path prefix."""
        u = urlparse(url)
        return u.netloc == parsed.netloc and u.path.startswith(seed_path)

    # 1. llms.txt
    llms_txt = await _fetch_url_text(f"{root}/llms.txt")
    if llms_txt:
        for line in llms_txt.splitlines():
            line = line.strip()
            if line.startswith("http"):
                candidate = line
            elif line.startswith("/"):
                candidate = urljoin(root, line)
            else:
                continue
            if _in_scope(candidate):
                pages.append(candidate)
        print(f"[doc-indexer] llms.txt: {len(pages)} in-scope pages (prefix={seed_path})", flush=True)
        return list(dict.fromkeys(pages))  # deduplicate, preserve order

    # 2. sitemap.xml (very lightweight parse — just grab <loc> tags)
    sitemap = await _fetch_url_text(f"{root}/sitemap.xml")
    if sitemap:
        locs = re.findall(r"<loc>(https?://[^<]+)</loc>", sitemap)
        # Keep only URLs within the seed path prefix (already implies same domain)
        filtered = [u for u in locs if _in_scope(u)]
        if filtered:
            pages.extend(filtered[:200])  # cap at 200 from sitemap
            print(f"[doc-indexer] sitemap.xml — {len(filtered)} in-scope pages (prefix={seed_path})", flush=True)
            return list(dict.fromkeys(pages))

    # 3. Just crawl the seed URL itself
    return pages


# Known JS-heavy domains that need a headless browser.
# Static sites (GitHub raw, readthedocs plain, etc.) use the fast HTTP crawler.
_JS_DOMAINS = {
    "neo4j.com", "developer.apple.com", "reactnative.dev",
    "docs.swift.org", "swift.org", "developer.mozilla.org",
    "learn.microsoft.com", "docs.microsoft.com",
    "github.com",           # issues/PRs/discussions are fully JS-rendered
    "stackoverflow.com",    # question pages are JS-rendered
}


async def crawl_pages(urls: List[str]) -> List[Dict]:
    """Crawl each URL using crawlee. Returns list of {url, markdown, title}.

    Routes URLs to the right engine:
    - ParselCrawler  (fast HTTP)      for plain/static sites
    - PlaywrightCrawler (JS headless) for JS-rendered domains in _JS_DOMAINS
    """
    import trafilatura
    # Crawlee writes request queues + datasets to ./storage/ by default.
    # In subprocess context cwd may be read-only, so redirect to /tmp.
    _storage = f"/tmp/crawlee_{os.getpid()}"
    os.makedirs(_storage, exist_ok=True)
    os.environ["CRAWLEE_STORAGE_DIR"] = _storage

    from crawlee.crawlers import (
        ParselCrawler, ParselCrawlingContext,
        PlaywrightCrawler, PlaywrightCrawlingContext,
    )

    results: List[Dict] = []

    js_urls   = [u for u in urls if any(d in u for d in _JS_DOMAINS)]
    http_urls = [u for u in urls if u not in set(js_urls)]

    # ── Fast HTTP crawl (ParselCrawler) ───────────────────────────────────────
    http_urls = [u for u in http_urls if not u.lower().endswith(".pdf")]  # ParselCrawler can't parse binary PDFs
    if http_urls:
        http_crawler = ParselCrawler(
            max_requests_per_crawl=len(http_urls),
            max_request_retries=2,
            use_session_pool=False,             # no proxies configured — session pooling adds no benefit and looks like a bot
            additional_http_error_status_codes=[429, 503],  # treat rate-limits as retryable errors
        )

        @http_crawler.router.default_handler
        async def _http_handler(context: ParselCrawlingContext) -> None:
            # context.selector is the Parsel Selector for the crawled page
            html  = context.selector.get() or ""
            md    = trafilatura.extract(
                html, include_links=False, output_format="markdown",
                favor_precision=True,
            ) or ""
            title = context.selector.css("title::text").get("") or ""
            if md.strip():
                results.append({"url": context.request.url, "markdown": md, "title": title})
                print(f"[doc-indexer] crawled (http) {context.request.url} — {len(md)} chars", flush=True)

        @http_crawler.failed_request_handler
        async def _http_error(context: ParselCrawlingContext, error: Exception) -> None:
            print(f"[doc-indexer] HTTP failed (all retries): {context.request.url} — {error}", file=sys.stderr, flush=True)

        try:
            await http_crawler.run(http_urls)
        except Exception as e:
            print(f"[doc-indexer] HTTP crawler error: {e}", file=sys.stderr, flush=True)

    # ── JS headless crawl (PlaywrightCrawler) ─────────────────────────────────
    if js_urls:
        from datetime import timedelta
        pw_crawler = PlaywrightCrawler(
            max_requests_per_crawl=len(js_urls),
            headless=True,
            max_request_retries=2,
            request_handler_timeout=timedelta(seconds=45),
            retry_on_blocked=False,             # disabled: retrying from same IP signals bot behavior and flags the IP
            use_session_pool=False,             # no proxies configured — session pooling only helps when paired with proxy rotation
            additional_http_error_status_codes=[429, 503],  # retry rate-limits automatically
            browser_launch_options={
                "args": ["--disable-dev-shm-usage"],  # prevent Chromium OOM in subprocess
            },
        )

        # Set navigation options BEFORE crawlee navigates (avoids double-navigate deadlock).
        @pw_crawler.pre_navigation_hook
        async def _pre_nav(context: PlaywrightCrawlingContext) -> None:
            context.page.set_default_navigation_timeout(60_000)
            # Block bandwidth-heavy assets that don't affect text content
            await context.page.route(
                "**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf,eot,mp4,webm}",
                lambda route: route.abort(),
            )
            await context.page.route(
                "**/{gtm,analytics,googletagmanager,google-analytics,doubleclick,googlesyndication,hotjar,segment,intercom,drift,hubspot}*",
                lambda route: route.abort(),
            )


        @pw_crawler.router.default_handler
        async def _pw_handler(context: PlaywrightCrawlingContext) -> None:
            # Page is already navigated by crawlee at this point — just read content.
            html  = await context.page.content()
            md    = trafilatura.extract(
                html, include_links=False, output_format="markdown",
                favor_precision=True,
            ) or ""
            title = await context.page.title()
            if md.strip():
                if len(md.encode()) > MAX_PAGE_BYTES:
                    print(f"[doc-indexer] skip (too large): {context.request.url}", flush=True)
                    return
                results.append({"url": context.request.url, "markdown": md, "title": title})
                print(f"[doc-indexer] crawled (js) {context.request.url} — {len(md)} chars", flush=True)

        @pw_crawler.failed_request_handler
        async def _pw_error(context: PlaywrightCrawlingContext, error: Exception) -> None:
            print(f"[doc-indexer] JS failed (all retries): {context.request.url} — {error}", file=sys.stderr, flush=True)

        try:
            await pw_crawler.run(js_urls)
        except Exception as e:
            print(f"[doc-indexer] Playwright crawler error: {e}", file=sys.stderr, flush=True)


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
            file=sys.stderr, flush=True,
        )
        return 0

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

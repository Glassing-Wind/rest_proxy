#!/usr/bin/env python3
"""doc_indexer.py – Documentation crawl, extract, chunk, embed, and store pipeline.

Pipeline per URL:
  1. llms.txt discovery  — try {root}/llms.txt → sitemap.xml fallback
  2. crawlee-python       — AdaptivePlaywrightCrawler (HTTP first, Playwright fallback)
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
CHUNK_LINES = 250  # fallback: target lines per chunk
OVERLAP_LINES = 25  # fallback: line overlap between chunks
CHUNK_MAX_BYTES = 8_000  # max bytes per section chunk (ts_pack split_markdown)
MAX_PAGE_BYTES = 2_000_000  # skip pages > 2MB of markdown


# ── llms.txt discovery ───────────────────────────────────────────────────────


async def _fetch_url_text(url: str, timeout: int = 10) -> Optional[str]:
    """Lightweight HTTP GET → text. Uses CurlImpersonateHttpClient for TLS-level
    browser impersonation (anti-bot), falling back to plain httpx on import error."""
    try:
        from crawlee.http_clients import CurlImpersonateHttpClient
        from crawlee import Request as CrawleeRequest

        client = CurlImpersonateHttpClient()
        async with client:
            resp = await client.send_request(
                CrawleeRequest.from_url(url),
                session=None,
                proxy_info=None,
            )
            if resp.status_code == 200:
                return resp.read().decode("utf-8", errors="replace")
    except Exception:
        pass
    # Fallback: plain httpx (used if curl_cffi not installed)
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                },
            )
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
    parsed = urlparse(seed_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    # Normalise the prefix: strip trailing filename so /foo/bar.html → /foo/
    seed_path = parsed.path
    if not seed_path.endswith("/"):
        seed_path = seed_path.rsplit("/", 1)[0] + "/"

    # If the seed looks like a specific document (not a directory), skip discovery.
    if (
        parsed.path
        and not parsed.path.endswith("/")
        and not seed_url.endswith("/llms.txt")
    ):
        return [seed_url]
    if parsed.path.startswith("/docs/") and parsed.path.rstrip("/") != "/docs":
        return [seed_url]

    # ── GitHub repo detection: rewrite to raw markdown ────────────────────────
    # github.com/owner/repo  (or .../tree/branch)  →  raw.githubusercontent.com/...
    # Bypasses Playwright + trafilatura entirely; preserves #-heading structure.
    gh_match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?/*$",
        seed_url,
    )
    if gh_match:
        owner, repo, branch, path = gh_match.groups()
        branch = branch or None
        rel = path or "README.md"
        if not rel.endswith(".md"):
            rel = rel.rstrip("/") + "/README.md"
        branches = [branch] if branch else ["main", "master"]
        for br in branches:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{br}/{rel}"
            text = await _fetch_url_text(raw_url)
            if text:
                print(f"[doc-indexer] GitHub → raw: {raw_url}", flush=True)
                return [raw_url]  # single clean page; no further discovery needed
        # fallback: let the normal pipeline handle it
        print(
            f"[doc-indexer] GitHub raw fetch failed — falling back to Playwright for {seed_url}",
            flush=True,
        )

    pages = [seed_url]

    def _in_scope(url: str) -> bool:
        """True iff url is on the same host and starts with the seed path prefix."""
        u = urlparse(url)
        return u.netloc == parsed.netloc and u.path.startswith(seed_path)

    def _parse_llms(text: str) -> List[str]:
        llms_urls: List[str] = []
        llms_urls.extend(re.findall(r"\((https?://[^)]+)\)", text))
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            for token in line.split():
                tok = token.strip("(),;.")
                if tok.startswith("http"):
                    llms_urls.append(tok)
                elif tok.startswith("/"):
                    llms_urls.append(urljoin(root, tok))
        return list(dict.fromkeys(llms_urls))

    def _filter_llms_urls(urls: List[str]) -> List[str]:
        if not urls:
            return []
        filtered: List[str] = []
        for u in urls:
            parsed_u = urlparse(u)
            if parsed_u.netloc != parsed.netloc:
                continue
            if seed_path.startswith("/docs/") and not parsed_u.path.startswith(
                "/docs/"
            ):
                continue
            filtered.append(u)
        return filtered

    # 1. llms.txt (seed URL can be llms.txt itself)
    if seed_url.endswith("/llms.txt"):
        llms_txt = await _fetch_url_text(seed_url)
        if llms_txt:
            llms_urls = _filter_llms_urls(_parse_llms(llms_txt))
            if llms_urls:
                print(
                    f"[doc-indexer] llms.txt seed: using {len(llms_urls)} URL(s)",
                    flush=True,
                )
                return llms_urls

    llms_txt = await _fetch_url_text(f"{root}/llms.txt")
    if not llms_txt and seed_path.startswith("/docs/"):
        llms_txt = await _fetch_url_text(f"{root}/docs/llms.txt")
    if llms_txt:
        llms_urls = _filter_llms_urls(_parse_llms(llms_txt))

        # Validate URLs to avoid dead llms.txt entries (fall back if none resolve).
        valid_llms: List[str] = []
        for u in llms_urls[:200]:
            try:
                if await _fetch_url_text(u, timeout=5):
                    valid_llms.append(u)
            except Exception:
                continue

        if valid_llms:
            in_scope = [u for u in valid_llms if _in_scope(u)]
            if in_scope:
                pages.extend(in_scope)
                print(
                    f"[doc-indexer] llms.txt: {len(pages)} in-scope pages (prefix={seed_path})",
                    flush=True,
                )
                return list(dict.fromkeys(pages))
            # llms.txt may point to a canonical docs domain; prefer it.
            print(
                f"[doc-indexer] llms.txt: using {len(valid_llms)} URL(s) outside scope",
                flush=True,
            )
            return list(dict.fromkeys(valid_llms))

        if llms_urls:
            print(
                f"[doc-indexer] llms.txt: validation failed, using {len(llms_urls)} URL(s)",
                flush=True,
            )
            return list(dict.fromkeys(llms_urls))

    # 2. sitemap.xml — stream via SitemapRequestLoader (handles gz + sitemap index files)
    try:
        from crawlee.request_loaders import SitemapRequestLoader
        from crawlee.http_clients import HttpxHttpClient as _SitemapClient

        async with SitemapRequestLoader(
            sitemap_urls=[f"{root}/sitemap.xml"],
            http_client=_SitemapClient(),
        ) as loader:
            req = await loader.fetch_next_request()
            while req is not None:
                if _in_scope(req.url):
                    pages.append(req.url)
                await loader.mark_request_as_handled(req)
                if len(pages) > 201:  # hard cap (1 seed + 200 from sitemap)
                    break
                req = await loader.fetch_next_request()
        if len(pages) > 1:
            print(
                f"[doc-indexer] sitemap.xml — {len(pages) - 1} in-scope pages (prefix={seed_path})",
                flush=True,
            )
            return list(dict.fromkeys(pages))
    except Exception as _e:
        print(
            f"[doc-indexer] SitemapRequestLoader failed ({_e}), falling back to regex",
            flush=True,
        )
        sitemap_txt = await _fetch_url_text(f"{root}/sitemap.xml")
        if sitemap_txt:
            locs = re.findall(r"<loc>(https?://[^<]+)</loc>", sitemap_txt)
            filtered = [u for u in locs if _in_scope(u)]
            if filtered:
                pages.extend(filtered[:200])
                print(
                    f"[doc-indexer] sitemap.xml (regex) — {len(filtered)} pages (prefix={seed_path})",
                    flush=True,
                )
                return list(dict.fromkeys(pages))

    # 3. Link extraction — try HTTP first, then Playwright if the page is JS-rendered.
    #    Handles Sphinx/MkDocs sites (HTTP) and SPA doc sites like neo4j (Playwright).
    async def _extract_links_from_html(html: str) -> List[str]:
        raw_hrefs = re.findall(r'href=["\']([^"\'#?][^"\']*)["\']', html)
        found = []
        for href in raw_hrefs:
            candidate = urljoin(seed_url, href).split("#")[0].split("?")[0]
            if _in_scope(candidate) and candidate not in pages:
                found.append(candidate)
        return found

    seed_html = await _fetch_url_text(seed_url)
    if seed_html:
        found = await _extract_links_from_html(seed_html)
        pages.extend(found)

    if len(pages) == 1:
        # HTTP found no links — page is likely JS-rendered. Try Playwright.
        print(
            f"[doc-indexer] HTTP link-extract found nothing, trying Playwright: {seed_url}",
            flush=True,
        )
        try:
            from datetime import timedelta
            from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext

            _storage = f"/tmp/crawlee_{os.getpid()}_disc"
            os.makedirs(_storage, exist_ok=True)
            os.environ["CRAWLEE_STORAGE_DIR"] = _storage
            js_links: List[str] = []
            pw = PlaywrightCrawler(
                max_requests_per_crawl=1,
                headless=True,
                max_request_retries=1,
                request_handler_timeout=timedelta(seconds=30),
                retry_on_blocked=False,
                use_session_pool=False,
                browser_launch_options={"args": ["--disable-dev-shm-usage"]},
            )

            @pw.router.default_handler
            async def _disc_handler(ctx: PlaywrightCrawlingContext) -> None:
                html = await ctx.page.content()
                js_links.extend(await _extract_links_from_html(html))

            await pw.run([seed_url])
            pages.extend(js_links)
            if len(pages) > 1:
                print(
                    f"[doc-indexer] Playwright link-extract: {len(pages) - 1} in-scope links found",
                    flush=True,
                )
        except Exception as e:
            print(
                f"[doc-indexer] Playwright link-extract failed: {e}",
                file=sys.stderr,
                flush=True,
            )

    if len(pages) > 1:
        print(
            f"[doc-indexer] link-extract fallback: {len(pages)} in-scope pages "
            f"(prefix={seed_path})",
            flush=True,
        )

    return list(dict.fromkeys(pages))


async def crawl_pages(urls: List[str]) -> List[Dict]:
    """Crawl each URL. Returns list of {url, xml|markdown, title}.

    Routing (in order):
    1. raw.githubusercontent.com → plain httpx GET, no trafilatura (native markdown)
    2. All others               → AdaptivePlaywrightCrawler (HTTP first, auto-Playwright
                                   fallback when content is insufficient).  Playwright-only
                                   pages get block_requests() to skip ads/media/analytics.
    """
    import trafilatura

    # Per-job isolated storage — fresh request queue every run, no cross-job
    # URL dedup bleed.  purge_on_start=False lets a crashed job resume.
    # NOTE: We intentionally do NOT set persistence_enabled on the predictor:
    # CRAWLEE_STORAGE_DIR must equal the crawler's storage_dir for the KV store
    # to resolve correctly, and sharing that path across jobs risks the request
    # queue's uniqueKey dedup silently skipping URLs from previous runs
    # (confirmed by crawlee issue #539 — Configuration kwarg can be overridden
    # by the env var in some code paths).  Within-job learning from
    # result_checker/result_comparator gives ~95% of the benefit anyway.
    _job_storage = f"/tmp/crawlee_{os.getpid()}"
    os.makedirs(_job_storage, exist_ok=True)
    os.environ["CRAWLEE_STORAGE_DIR"] = _job_storage

    from datetime import timedelta
    from crawlee.configuration import Configuration
    from crawlee._types import RequestHandlerRunResult
    from crawlee.crawlers import (
        AdaptivePlaywrightCrawler,
        AdaptivePlaywrightCrawlingContext,
        AdaptivePlaywrightPreNavCrawlingContext,
    )
    from crawlee.http_clients import ImpitHttpClient
    from crawlee.fingerprint_suite import (
        DefaultFingerprintGenerator,
        HeaderGeneratorOptions,
    )
    from crawlee.crawlers._adaptive_playwright._rendering_type_predictor import (
        DefaultRenderingTypePredictor,
    )

    # Within-job predictor — learns HTTP vs Playwright for each domain encountered
    # during this run.  detection_ratio=0.05 caps the overhead of dual-path probing.
    _predictor = DefaultRenderingTypePredictor(detection_ratio=0.05)

    # ── Predictor quality signals — teach the RenderingTypePredictor what
    #    "good content" looks like so it correctly learns HTTP vs Playwright. ──────
    #    Without these, the predictor compares push_data_calls (always 0 for us)
    #    and thinks HTTP is always sufficient, even for JS-rendered pages.
    _MIN_QUALITY_CHARS = 200  # minimum XML content length to count as "valid"

    def _result_checker(result: RequestHandlerRunResult) -> bool:
        """True if this sub-crawler produced real content (not an empty page)."""
        if not result.push_data_calls:
            return False
        return result.push_data_calls[0].get("_len", 0) >= _MIN_QUALITY_CHARS

    def _result_comparator(
        r1: RequestHandlerRunResult, r2: RequestHandlerRunResult
    ) -> bool:
        """True if HTTP and Playwright produced content of similar length (≥0.7 ratio),
        indicating HTTP alone is sufficient and Playwright isn't needed."""
        l1 = r1.push_data_calls[0].get("_len", 0) if r1.push_data_calls else 0
        l2 = r2.push_data_calls[0].get("_len", 0) if r2.push_data_calls else 0
        if l1 == 0 and l2 == 0:
            return True  # both empty — both bad, treat as same
        if l1 == 0 or l2 == 0:
            return False  # one got content, one didn't — not equivalent
        return min(l1, l2) / max(l1, l2) >= 0.7  # within 30% = same content

    # Fingerprint generator for anti-bot resistance on Playwright pages
    _fp_gen = DefaultFingerprintGenerator(
        header_options=HeaderGeneratorOptions(browsers=["chrome"]),
    )

    results: List[Dict] = []

    # ── 1. Raw GitHub markdown (no browser, no trafilatura) ───────────────────
    raw_urls = [u for u in urls if "raw.githubusercontent.com" in u]
    rest_urls = [u for u in urls if u not in set(raw_urls)]
    for raw_url in raw_urls:
        text = await _fetch_url_text(raw_url)
        if text:
            title = (
                raw_url.rstrip("/")
                .rsplit("/", 1)[-1]
                .replace("-", " ")
                .replace("_", " ")
            )
            results.append({"url": raw_url, "markdown": text, "title": title})
            print(
                f"[doc-indexer] fetched raw: {raw_url} — {len(text)} chars", flush=True
            )
        else:
            print(
                f"[doc-indexer] raw fetch failed: {raw_url}",
                file=sys.stderr,
                flush=True,
            )

    # ── 2. AdaptivePlaywrightCrawler for all other URLs ───────────────────────
    rest_urls = [u for u in rest_urls if not u.lower().endswith(".pdf")]
    if not rest_urls:
        return results

    max_requests = len(rest_urls)
    if os.getenv("LM_PROXY_DOCS_MAX_REQUESTS"):
        try:
            max_requests = int(os.getenv("LM_PROXY_DOCS_MAX_REQUESTS", ""))
        except ValueError:
            max_requests = len(rest_urls)
    crawler = AdaptivePlaywrightCrawler.with_beautifulsoup_static_parser(
        max_requests_per_crawl=max_requests,
        http_client=ImpitHttpClient(),
        # Use job-specific storage for request queue (ephemeral, crash-resumable);
        # predictor KV lives in the stable _PREDICTOR_DIR via CRAWLEE_STORAGE_DIR env.
        configuration=Configuration(
            storage_dir=_job_storage,
            purge_on_start=False,
        ),
        rendering_type_predictor=_predictor,  # persistent cross-job learning
        result_checker=_result_checker,  # validate content quality
        result_comparator=_result_comparator,  # compare HTTP vs Playwright
        playwright_crawler_specific_kwargs={
            "headless": True,
            "max_request_retries": 2,
            "request_handler_timeout": timedelta(seconds=45),
            "retry_on_blocked": False,
            "use_session_pool": False,
            "additional_http_error_status_codes": [429, 503],
            "browser_launch_options": {"args": ["--disable-dev-shm-usage"]},
            "fingerprint_generator": _fp_gen,
        },
    )

    # Block ads, tracking, media — Playwright path only (no page object in HTTP path)
    @crawler.pre_navigation_hook(playwright_only=True)
    async def _block_junk(context: AdaptivePlaywrightPreNavCrawlingContext) -> None:
        context.page.set_default_navigation_timeout(60_000)
        await context.block_requests(
            extra_url_patterns=[
                "adsbygoogle.js",
                "gtm.js",
                "analytics.js",
                "hotjar",
                "segment.io",
                "intercom",
                "drift",
                "hubspot",
            ],
        )

    # AdaptivePlaywrightCrawler intentionally fires both sub-crawlers periodically
    # (for RenderingTypePredictor learning). We deduplicate here, preferring HTTP
    # results (cheaper) over Playwright when both run for the same URL.
    seen_urls: Dict[str, str] = {}  # url → mode that stored the result

    @crawler.router.default_handler
    async def _handler(context: AdaptivePlaywrightCrawlingContext) -> None:
        url = context.request.url

        # Get raw HTML: check _page (private; context.page raises in static mode)
        if context._page is not None:
            try:
                await context._page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass
            html = await context._page.content()
            title = await context._page.title()
            mode = "js"
        else:
            raw = await context.http_response.read()
            html = raw.decode("utf-8", errors="replace")
            title_m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
            title = title_m.group(1).strip() if title_m else url.rsplit("/", 1)[-1]
            mode = "http"

        # Skip if already stored via HTTP (best mode); allow HTTP to replace a js entry
        prior = seen_urls.get(url)
        if prior == "http":
            return  # already have best result; discard Playwright duplicate
        if prior == "js" and mode == "js":
            return  # same mode duplicate — skip

        xml = (
            trafilatura.extract(
                html,
                include_links=False,
                output_format="xml",
                favor_recall=True,
            )
            or ""
        )
        if not xml.strip():
            markdown = (
                trafilatura.extract(
                    html,
                    include_links=False,
                    output_format="markdown",
                    favor_recall=True,
                )
                or ""
            )
            if markdown.strip():
                await context.push_data({"_len": len(markdown)})
                results.append({"url": url, "markdown": markdown, "title": title})
                print(
                    f"[doc-indexer] crawled ({mode}, md fallback): {url} — {len(markdown)} chars",
                    flush=True,
                )
                seen_urls[url] = mode
                return
            print(f"[doc-indexer] skip (no content): {url}", flush=True)
            return
        if len(xml.encode()) > MAX_PAGE_BYTES:
            print(f"[doc-indexer] skip (too large): {url}", flush=True)
            return

        # Signal content quality to the predictor via push_data.
        # This is the ONLY output the result_checker/result_comparator see; we use
        # a sentinel key "_len" so real dataset consumers can ignore it.
        await context.push_data({"_len": len(xml)})

        if prior == "js" and mode == "http":
            # Upgrade: remove the old Playwright entry, replace with HTTP result
            for i, r in enumerate(results):
                if r["url"] == url:
                    results[i] = {"url": url, "xml": xml, "title": title}
                    break
            print(
                f"[doc-indexer] upgraded http←js: {url} — {len(xml)} chars", flush=True
            )
        else:
            results.append({"url": url, "xml": xml, "title": title})
            print(
                f"[doc-indexer] crawled ({mode}): {url} — {len(xml)} chars", flush=True
            )

        seen_urls[url] = mode

    @crawler.failed_request_handler
    async def _error(
        context: AdaptivePlaywrightCrawlingContext, error: Exception
    ) -> None:
        print(
            f"[doc-indexer] JS failed: {context.request.url} — {error}",
            file=sys.stderr,
            flush=True,
        )

    try:
        await crawler.run(rest_urls)
    except Exception as e:
        print(f"[doc-indexer] crawler error: {e}", file=sys.stderr, flush=True)

    # Log adaptive crawler statistics: HTTP vs Playwright ratio and mispredictions
    try:
        s = crawler.statistics.state
        http_runs = getattr(s, "http_only_request_handler_runs", "?")
        js_runs = getattr(s, "browser_request_handler_runs", "?")
        misses = getattr(s, "rendering_type_mispredictions", "?")
        print(
            f"[doc-indexer] stats: http={http_runs}  js={js_runs}  "
            f"mispredictions={misses}  total={len(results)}",
            flush=True,
        )
    except Exception:
        pass

    return results


# ── Extract ──────────────────────────────────────────────────────────────────


def chunk_content(
    content: str, url: str, title: str, fmt: str = "markdown"
) -> List[Dict]:
    """Chunk documentation content with ts_pack, supporting both 'markdown' and 'xml' formats.

    - fmt='markdown': used for raw GitHub READMEs (native # headings)
    - fmt='xml':      used for trafilatura XML output (preserves <head> tags from HTML)

    ts_pack populates context_path from the section-node hierarchy in the AST.
    Falls back to a simple line-window split if ts_pack is unavailable.
    Returns list of dicts: {text, context_path}.
    """
    if not content.strip():
        return []

    header = f"// Source: {url}\n// Title: {title}\n\n"

    try:
        import tree_sitter_language_pack as ts_pack

        config = ts_pack.ProcessConfig(fmt, chunk_max_size=CHUNK_MAX_BYTES)
        result = ts_pack.process(content, config)
        chunks = result.get("chunks", [])
        if chunks:

            def _strip_xml(text: str) -> str:
                """Remove XML tags, collapse whitespace — keeps human-readable content."""
                text = re.sub(r"<[^>]+>", " ", text)
                return re.sub(r" {2,}", " ", text).strip()

            strip = _strip_xml if fmt == "xml" else (lambda t: t)
            return [
                {
                    "text": header + strip(c["content"]),
                    "context_path": c.get("metadata", {}).get("context_path") or [],
                }
                for c in chunks
                if c.get("content", "").strip()
                and not c.get("metadata", {}).get("has_error_nodes", False)
            ]
    except Exception as e:
        print(
            f"[doc-indexer] ts_pack chunk error ({fmt}): {e}",
            file=sys.stderr,
            flush=True,
        )

    # Fallback: simple line-window split (no context_path)
    lines = content.splitlines()
    out = []
    i = 0
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
            file=sys.stderr,
            flush=True,
        )
        return 0

    t0 = time.time()
    total_chunks = 0

    # Expand URLs with llms.txt / sitemap discovery
    all_pages: List[str] = []
    for url in urls:
        discovered = await discover_pages(url)
        all_pages.extend(discovered)
    # Deduplicate but preserve all distinct seed URLs
    all_pages = list(dict.fromkeys(all_pages))

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


async def _upsert_doc_chunks_batch(
    items: List[Dict], vectors: List[List[float]], topic: str
) -> int:
    """Batch-upsert doc chunks into doc_embeddings — one executemany per flush, one round-trip."""
    if not memory_store._pg_pool_available() or not items:
        return 0
    import json as _json

    rows = []
    for item, vector in zip(items, vectors):
        if not vector:
            continue
        vec_str = "[" + ",".join(str(v) for v in vector) + "]"
        metadata = _json.dumps(
            {
                "title": item.get("title", ""),
                "topic": topic,
                "context_path": item.get("context_path", []),
            }
        )
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


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Documentation indexer")
    parser.add_argument(
        "--urls-file", required=True, help="JSON file with list of URLs"
    )
    parser.add_argument("--topic", required=True, help="Topic label (e.g. 'neo4j')")
    args = parser.parse_args()

    with open(args.urls_file) as fh:
        urls: List[str] = json.load(fh)

    print(f"[doc-indexer] {len(urls)} seed URLs  topic={args.topic}", flush=True)
    asyncio.run(index_docs(urls, args.topic))

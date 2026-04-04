"""tools/docs/crawl.py — documentation crawling and extraction."""

import os
import re
import sys
from typing import Dict, List
from urllib.parse import urlparse

from tools.docs.config import DEFAULT_FORCE_PLAYWRIGHT_HOSTS, MAX_PAGE_BYTES
from tools.docs.discovery import _fetch_url_text


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
    from crawlee import ConcurrencySettings
    from crawlee._types import RequestHandlerRunResult
    from crawlee.crawlers import (
        AdaptivePlaywrightCrawler,
        AdaptivePlaywrightCrawlingContext,
        AdaptivePlaywrightPreNavCrawlingContext,
        PlaywrightCrawler,
        PlaywrightCrawlingContext,
        PlaywrightPreNavCrawlingContext,
    )
    from crawlee.events import LocalEventManager
    from crawlee.http_clients import ImpitHttpClient
    from crawlee.storage_clients import FileSystemStorageClient
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
    quiet = os.getenv("LM_PROXY_DOCS_QUIET", "").strip().lower() in {"1", "true", "yes"}

    def _log(message: str) -> None:
        if quiet:
            return
        print(message, flush=True)

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
            _log(f"[doc-indexer] fetched raw: {raw_url} — {len(text)} chars")
        else:
            print(
                f"[doc-indexer] raw fetch failed: {raw_url}",
                file=sys.stderr,
                flush=True,
            )

    # ── 2. AdaptivePlaywrightCrawler / PlaywrightCrawler ──────────────────────
    rest_urls = [u for u in rest_urls if not u.lower().endswith(".pdf")]
    if not rest_urls:
        return results

    max_requests = len(rest_urls)
    if os.getenv("LM_PROXY_DOCS_MAX_REQUESTS"):
        try:
            max_requests = int(os.getenv("LM_PROXY_DOCS_MAX_REQUESTS", ""))
        except ValueError:
            max_requests = len(rest_urls)
    if len(rest_urls) > max_requests:
        rest_urls = rest_urls[:max_requests]
    http_client = None
    client_choice = os.getenv("LM_PROXY_DOCS_HTTP_CLIENT", "impit").strip().lower()
    if client_choice in {"httpx", "httpxhttpclient"}:
        try:
            from crawlee.http_clients import HttpxHttpClient

            http_client = HttpxHttpClient()
        except Exception as exc:
            print(
                f"[doc-indexer] httpx client unavailable ({exc}); falling back to Impit",
                file=sys.stderr,
                flush=True,
            )
    elif client_choice in {"curl", "curl-impersonate", "curlimpersonate"}:
        try:
            from crawlee.http_clients import CurlImpersonateHttpClient

            http_client = CurlImpersonateHttpClient()
        except Exception as exc:
            print(
                f"[doc-indexer] curl-impersonate client unavailable ({exc}); falling back to Impit",
                file=sys.stderr,
                flush=True,
            )

    if http_client is None:
        http_client = ImpitHttpClient()

    configuration = Configuration(
        storage_dir=_job_storage,
        purge_on_start=False,
    )
    storage_client = FileSystemStorageClient()

    max_concurrency = 16
    env_max_concurrency = os.getenv("LM_PROXY_DOCS_MAX_CONCURRENCY", "").strip()
    if env_max_concurrency:
        try:
            max_concurrency = max(1, int(env_max_concurrency))
        except ValueError:
            max_concurrency = 16
    concurrency_settings = ConcurrencySettings(max_concurrency=max_concurrency)

    async with LocalEventManager() as event_manager:
        force_hosts = DEFAULT_FORCE_PLAYWRIGHT_HOSTS
        env_hosts = os.getenv("LM_PROXY_DOCS_FORCE_PLAYWRIGHT_HOSTS", "")
        if env_hosts.strip():
            force_hosts = [h.strip().lower() for h in env_hosts.split(",") if h.strip()]

        def _force_playwright(url: str) -> bool:
            host = urlparse(url).netloc.lower()
            return any(h in host for h in force_hosts)

        force_js_urls = [u for u in rest_urls if _force_playwright(u)]
        adaptive_urls = [u for u in rest_urls if u not in set(force_js_urls)]

        adaptive_crawler = None
        if adaptive_urls:
            adaptive_crawler = AdaptivePlaywrightCrawler.with_beautifulsoup_static_parser(
                max_requests_per_crawl=max_requests,
                http_client=http_client,
                configuration=configuration,
                storage_client=storage_client,
                event_manager=event_manager,
                concurrency_settings=concurrency_settings,
                rendering_type_predictor=_predictor,  # persistent cross-job learning
                result_checker=_result_checker,  # validate content quality
                result_comparator=_result_comparator,  # compare HTTP vs Playwright
                max_request_retries=2,
                request_handler_timeout=timedelta(seconds=45),
                retry_on_blocked=False,
                use_session_pool=False,
                additional_http_error_status_codes=[429, 503],
                playwright_crawler_specific_kwargs={
                    "headless": True,
                    "browser_launch_options": {"args": ["--disable-dev-shm-usage"]},
                    "fingerprint_generator": _fp_gen,
                },
            )

        js_crawler = None
        if force_js_urls:
            js_crawler = PlaywrightCrawler(
                max_requests_per_crawl=max_requests,
                configuration=configuration,
                storage_client=storage_client,
                event_manager=event_manager,
                concurrency_settings=concurrency_settings,
                max_request_retries=2,
                request_handler_timeout=timedelta(seconds=45),
                retry_on_blocked=False,
                use_session_pool=False,
                additional_http_error_status_codes=[429, 503],
                browser_launch_options={"args": ["--disable-dev-shm-usage"]},
                fingerprint_generator=_fp_gen,
            )

        # Block ads, tracking, media — Playwright path only (no page object in HTTP path)
        async def _block_junk(context) -> None:
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

        if adaptive_crawler:

            @adaptive_crawler.pre_navigation_hook(playwright_only=True)
            async def _block_junk_adaptive(
                context: AdaptivePlaywrightPreNavCrawlingContext,
            ) -> None:
                await _block_junk(context)

        if js_crawler:

            @js_crawler.pre_navigation_hook
            async def _block_junk_playwright(
                context: PlaywrightPreNavCrawlingContext,
            ) -> None:
                await _block_junk(context)

        # AdaptivePlaywrightCrawler intentionally fires both sub-crawlers periodically
        # (for RenderingTypePredictor learning). We deduplicate here, preferring HTTP
        # results (cheaper) over Playwright when both run for the same URL.
        seen_urls: Dict[str, str] = {}  # url → mode that stored the result

        async def _store_content(
            context,
            url: str,
            html: str,
            title: str,
            mode: str,
        ) -> None:
            prior = seen_urls.get(url)
            if prior == "http":
                return
            if prior == "js" and mode == "js":
                return

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
                    _log(
                        f"[doc-indexer] crawled ({mode}, md fallback): {url} — {len(markdown)} chars"
                    )
                    seen_urls[url] = mode
                    return
                _log(f"[doc-indexer] skip (no content): {url}")
                return
            if len(xml.encode()) > MAX_PAGE_BYTES:
                _log(f"[doc-indexer] skip (too large): {url}")
                return

            await context.push_data({"_len": len(xml)})

            if prior == "js" and mode == "http":
                for i, r in enumerate(results):
                    if r["url"] == url:
                        results[i] = {"url": url, "xml": xml, "title": title}
                        break
                _log(f"[doc-indexer] upgraded http←js: {url} — {len(xml)} chars")
            else:
                results.append({"url": url, "xml": xml, "title": title})
                _log(f"[doc-indexer] crawled ({mode}): {url} — {len(xml)} chars")

            seen_urls[url] = mode

        if adaptive_crawler:

            @adaptive_crawler.router.default_handler
            async def _handler(context: AdaptivePlaywrightCrawlingContext) -> None:
                url = context.request.url
                page = None
                try:
                    page = context.page
                except Exception:
                    page = None

                if page is not None:
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5_000)
                    except Exception:
                        pass
                    html = await page.content()
                    title = await page.title()
                    mode = "js"
                else:
                    raw = await context.http_response.read()
                    html = raw.decode("utf-8", errors="replace")
                    title_m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
                    title = (
                        title_m.group(1).strip() if title_m else url.rsplit("/", 1)[-1]
                    )
                    mode = "http"

                await _store_content(context, url, html, title, mode)

            @adaptive_crawler.failed_request_handler
            async def _error_adaptive(
                context: AdaptivePlaywrightCrawlingContext, error: Exception
            ) -> None:
                print(
                    f"[doc-indexer] JS failed: {context.request.url} — {error}",
                    file=sys.stderr,
                    flush=True,
                )

        if js_crawler:

            @js_crawler.router.default_handler
            async def _handler_js(context: PlaywrightCrawlingContext) -> None:
                url = context.request.url
                try:
                    await context.page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    pass
                html = await context.page.content()
                title = await context.page.title()
                await _store_content(context, url, html, title, "js")

            @js_crawler.failed_request_handler
            async def _error_js(
                context: PlaywrightCrawlingContext, error: Exception
            ) -> None:
                print(
                    f"[doc-indexer] JS failed: {context.request.url} — {error}",
                    file=sys.stderr,
                    flush=True,
                )

        try:
            if js_crawler:
                await js_crawler.run(force_js_urls)
            if adaptive_crawler:
                await adaptive_crawler.run(adaptive_urls)
        except Exception as e:
            print(f"[doc-indexer] crawler error: {e}", file=sys.stderr, flush=True)

        if adaptive_crawler:
            try:
                s = adaptive_crawler.statistics.state
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

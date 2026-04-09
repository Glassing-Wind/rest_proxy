"""tools/docs/crawl.py — documentation crawling and extraction."""

import os
import re
import sys
from typing import Dict, List
from urllib.parse import urlparse

from tools.brain.docs.config import (
    host_profile_delay_ms,
    host_profile_flag,
    host_profile_selectors,
    MAX_PAGE_BYTES,
)
from tools.brain.docs.discovery import _fetch_url_text


def _normalize_text_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out: list[str] = []
    prev = None
    for line in lines:
        if line == prev:
            continue
        out.append(line)
        prev = line
    return "\n".join(out)


def _extract_host_specific_text(url: str, html: str) -> str:
    selectors = host_profile_selectors(url, "content_selectors")
    if not selectors:
        return ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        candidates: list[str] = []
        for selector in selectors:
            for node in soup.select(selector):
                text = _normalize_text_lines(node.get_text("\n", strip=True))
                if len(text) >= 400:
                    candidates.append(text)
        if not candidates:
            return ""
        return max(candidates, key=len)
    except Exception:
        return ""


async def _extract_host_specific_rendered_text(page, url: str) -> str:
    selectors = host_profile_selectors(url, "content_selectors")
    if not selectors:
        return ""
    candidates: list[str] = []
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            if not count:
                continue
            for idx in range(min(count, 8)):
                text = await locator.nth(idx).inner_text(timeout=1500)
                text = _normalize_text_lines(text)
                if len(text) >= 400:
                    candidates.append(text)
        except Exception:
            continue
    if not candidates:
        return ""
    return max(candidates, key=len)


def _extract_host_specific_text_from_parser(url: str, parsed) -> str:
    selectors = host_profile_selectors(url, "content_selectors")
    if not selectors:
        return ""
    candidates: list[str] = []
    for selector in selectors:
        try:
            nodes = parsed.select(selector)
        except Exception:
            continue
        for node in nodes[:8]:
            try:
                text = _normalize_text_lines(node.get_text("\n", strip=True))
            except Exception:
                continue
            if len(text) >= 400:
                candidates.append(text)
    if not candidates:
        return ""
    return max(candidates, key=len)


async def _settle_dynamic_page(page, url: str) -> None:
    ready_selectors = host_profile_selectors(url, "ready_selectors")
    for selector in ready_selectors:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=10_000)
            break
        except Exception:
            continue

    delay_ms = host_profile_delay_ms(url)
    if delay_ms:
        try:
            await page.wait_for_timeout(delay_ms)
        except Exception:
            return


async def _crawl_direct_playwright(urls: List[str]) -> List[Dict]:
    from playwright.async_api import async_playwright

    results: List[Dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--disable-dev-shm-usage"]
        )
        try:
            context = await browser.new_context()

            async def _route_handler(route) -> None:
                request_url = route.request.url.lower()
                blocked_tokens = (
                    "adsbygoogle.js",
                    "gtm.js",
                    "analytics.js",
                    "googletagmanager",
                    "google-analytics",
                    "hotjar",
                    "segment.io",
                    "intercom",
                    "drift",
                    "hubspot",
                )
                blocked_suffixes = (
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".gif",
                    ".webp",
                    ".svg",
                    ".woff",
                    ".woff2",
                )
                if any(token in request_url for token in blocked_tokens) or request_url.endswith(
                    blocked_suffixes
                ):
                    await route.abort()
                    return
                await route.continue_()

            await context.route("**/*", _route_handler)
            for url in urls:
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    await _settle_dynamic_page(page, url)
                    title = await page.title()
                    rendered_text = await _extract_host_specific_rendered_text(page, url)
                    if not rendered_text.strip():
                        body_text = await page.locator("body").inner_text()
                        rendered_text = _normalize_text_lines(body_text)
                    if rendered_text.strip():
                        results.append(
                            {"url": url, "markdown": rendered_text, "title": title}
                        )
                        print(
                            f"[doc-indexer] crawled (direct playwright): {url} — {len(rendered_text)} chars",
                            flush=True,
                        )
                    else:
                        print(
                            f"[doc-indexer] skip (direct playwright, no content): {url}",
                            flush=True,
                        )
                finally:
                    await page.close()
            await context.close()
        finally:
            await browser.close()
    return results


def _html_text_fallback(url: str, html: str) -> str:
    """Best-effort plain-text extraction for JS-heavy docs when trafilatura fails."""
    specific = _extract_host_specific_text(url, html)
    if specific:
        return specific
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        root = (
            soup.find("main")
            or soup.find("article")
            or soup.find(attrs={"role": "main"})
            or soup.body
            or soup
        )
        return _normalize_text_lines(root.get_text("\n", strip=True))
    except Exception:
        return ""


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

    direct_playwright_urls = [
        u
        for u in rest_urls
        if host_profile_flag(u, "direct_playwright")
    ]
    if direct_playwright_urls:
        results.extend(await _crawl_direct_playwright(direct_playwright_urls))
        rest_urls = [u for u in rest_urls if u not in set(direct_playwright_urls)]
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
        env_hosts = os.getenv("LM_PROXY_DOCS_FORCE_PLAYWRIGHT_HOSTS", "")
        env_force_hosts = [h.strip().lower() for h in env_hosts.split(",") if h.strip()]

        def _force_playwright(url: str) -> bool:
            host = urlparse(url).netloc.lower()
            return host_profile_flag(url, "force_playwright") or any(
                h in host for h in env_force_hosts
            )

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
            rendered_text: str = "",
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
                if rendered_text.strip():
                    await context.push_data({"_len": len(rendered_text)})
                    results.append(
                        {"url": url, "markdown": rendered_text, "title": title}
                    )
                    _log(
                        f"[doc-indexer] crawled ({mode}, rendered fallback): {url} — {len(rendered_text)} chars"
                    )
                    seen_urls[url] = mode
                    return
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
                text_fallback = _html_text_fallback(url, html)
                if text_fallback.strip():
                    await context.push_data({"_len": len(text_fallback)})
                    results.append(
                        {"url": url, "markdown": text_fallback, "title": title}
                    )
                    _log(
                        f"[doc-indexer] crawled ({mode}, text fallback): {url} — {len(text_fallback)} chars"
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
                        await context.wait_for_load_state("networkidle", timeout=5_000)
                    except Exception:
                        pass
                    await _settle_dynamic_page(page, url)
                    rendered_text = await _extract_host_specific_rendered_text(page, url)
                    html = await page.content()
                    title = await page.title()
                    mode = "js"
                else:
                    rendered_text = ""
                    raw = await context.http_response.read()
                    html = raw.decode("utf-8", errors="replace")
                    if host_profile_selectors(url, "content_selectors"):
                        try:
                            parsed = context.parse_with_static_parser()
                            rendered_text = _extract_host_specific_text_from_parser(url, parsed)
                        except Exception:
                            rendered_text = ""
                    title_m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
                    title = (
                        title_m.group(1).strip() if title_m else url.rsplit("/", 1)[-1]
                    )
                    mode = "http"

                await _store_content(context, url, html, title, mode, rendered_text)

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
                await _settle_dynamic_page(context.page, url)
                rendered_text = await _extract_host_specific_rendered_text(
                    context.page, url
                )
                html = await context.page.content()
                title = await context.page.title()
                await _store_content(
                    context, url, html, title, "js", rendered_text
                )

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

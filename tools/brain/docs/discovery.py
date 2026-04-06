"""tools/docs/discovery.py — llms.txt, sitemap, and link discovery."""

import os
import re
import sys
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from tools.brain.docs.config import DEFAULT_SKIP_SITEMAP_HOSTS


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


async def discover_pages(
    seed_url: str, url_filters: Optional[List[str]] = None
) -> List[str]:
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
    skip_sitemap_hosts = DEFAULT_SKIP_SITEMAP_HOSTS
    env_skip_sitemaps = os.getenv("LM_PROXY_DOCS_SKIP_SITEMAP_HOSTS", "")
    if env_skip_sitemaps.strip():
        skip_sitemap_hosts = [
            h.strip().lower() for h in env_skip_sitemaps.split(",") if h.strip()
        ]

    def _in_scope(url: str) -> bool:
        """True iff url is on the same host and starts with the seed path prefix."""
        u = urlparse(url)
        if u.netloc != parsed.netloc or not u.path.startswith(seed_path):
            return False
        if url_filters:
            for filt in url_filters:
                if filt and filt in url:
                    return False
        return True

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
        return [u for u in urls if _in_scope(u)]

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
            print(
                "[doc-indexer] llms.txt: no in-scope URLs found; falling back",
                flush=True,
            )

        if llms_urls:
            print(
                f"[doc-indexer] llms.txt: validation failed, using {len(llms_urls)} URL(s)",
                flush=True,
            )
            return list(dict.fromkeys(llms_urls))

    # 2. sitemap.xml — stream via SitemapRequestLoader (handles gz + sitemap index files)
    skip_sitemap = any(h in parsed.netloc.lower() for h in skip_sitemap_hosts)
    if skip_sitemap:
        print(
            f"[doc-indexer] skip sitemap loader for host={parsed.netloc}",
            flush=True,
        )
    if not skip_sitemap:
        try:
            from crawlee.request_loaders import SitemapRequestLoader
            from crawlee.http_clients import HttpxHttpClient as _SitemapClient

            async with SitemapRequestLoader(
                sitemap_urls=[f"{root}/sitemap.xml", f"{root}/sitemap_index.xml"],
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
            for sitemap_url in (f"{root}/sitemap.xml", f"{root}/sitemap_index.xml"):
                sitemap_txt = await _fetch_url_text(sitemap_url)
                if not sitemap_txt:
                    continue
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
        raw_hrefs = re.findall(r"href=[\"\']([^\"\'#?][^\"\']*)[\"\']", html)
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

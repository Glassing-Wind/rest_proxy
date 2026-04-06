"""tools/docs/research.py — documentation research tools."""

import json
import os
import sys
import threading
import subprocess
from mcp.server.fastmcp import FastMCP

from _jobs import _JOBS, _JOBS_LOCK, _drain_proc_output, _finalize_job


async def _tavily_search(query: str, max_results: int = 10) -> list:
    """
    Search via Tavily API (async-native, no threading workaround needed).
    Returns a list of dicts with 'url', 'title', 'content' keys.
    Falls back to an empty list if TAVILY_API_KEY is not set.
    """
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return []
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=api_key)
    resp = await client.search(query, max_results=max_results, include_domains=[])
    return resp.get("results", [])


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def research_documentation(topic: str, query: str) -> str:
        """
        Search the web for documentation relevant to a topic/query.
        Returns candidate URLs with titles and snippets for the agent to review.
        Call download_documentation() with chosen URLs to crawl and index them.

        Args:
            topic: Library or product name (e.g. 'neo4j', 'pgvector', 'crawl4ai').
            query: Specific question or feature to find docs for.
        """
        try:
            import httpx
            from urllib.parse import urlparse

            search_query = f"{topic} {query}"
            hits = await _tavily_search(search_query, max_results=10)
            if not hits:
                hits = await _tavily_search(
                    f"{topic} {query} documentation", max_results=10
                )

            if not hits:
                return (
                    f"No results found for: {topic} — {query}  (Is TAVILY_API_KEY set?)"
                )

            lines = [f"Documentation search: '{topic}' — '{query}'", ""]
            seen_domains = set()

            for hit in hits:
                url = hit.get("url", "")
                title = hit.get("title", "")
                body = (hit.get("content") or "")[:120].replace("\n", " ")
                score = hit.get("score", 0)
                lines += [f"• {title}  [{score:.2f}]", f"  {url}", f"  {body}…", ""]

                domain = urlparse(url).netloc
                if domain not in seen_domains:
                    seen_domains.add(domain)
                    llms_url = f"{urlparse(url).scheme}://{domain}/llms.txt"
                    try:
                        async with httpx.AsyncClient(timeout=5) as client:
                            r = await client.get(llms_url)
                            if r.status_code == 200:
                                lines += [
                                    f"  ✅ llms.txt found at {llms_url} (structured page list available)",
                                    "",
                                ]
                    except Exception:
                        pass

            lines += [
                "---",
                f"Found {len(hits)} results. Call download_documentation(urls=[...], topic='{topic}')",
            ]
            lines.append("to crawl, extract, and index any of the above URLs.")
            return "\n".join(lines)
        except Exception as e:
            return f"Error during research: {e}"

    @mcp.tool()
    async def research_and_index(topic: str, query: str, max_urls: int = 5) -> str:
        """
        Single-call research pipeline: web search → auto-select best URLs → crawl & index.

        Combines research_documentation + download_documentation into one step.
        Searches for documentation relevant to the topic/query, picks the top URLs
        from high-quality domains, and immediately starts background indexing.
        Use get_index_status(job_id) to monitor, search_documentation() once done.

        Args:
            topic:    Library or product name (e.g. 'swift call graph', 'neo4j').
            query:    Specific question or concept to find docs for.
            max_urls: Maximum number of URLs to index (default 5, max 10).
        """
        try:
            from urllib.parse import urlparse
            import time, uuid

            max_urls = min(int(max_urls), 10)

            # ── 1. Web search ─────────────────────────────────────────────────
            search_query = f"{topic} {query}"
            hits = await _tavily_search(search_query, max_results=20)
            if not hits:
                hits = await _tavily_search(
                    f"{topic} {query} documentation", max_results=20
                )

            if not hits:
                return (
                    f"No results found for: {topic} — {query}  (Is TAVILY_API_KEY set?)"
                )

            # ── 2. Filter + rank URLs ─────────────────────────────────────────
            # Prefer official docs, readthedocs, GitHub, swift.org, etc.
            PREFERRED = (
                "swift.org",
                "docs.",
                "readthedocs",
                "github.com",
                "developer.apple.com",
            )
            BLACKLIST = (
                "youtube.com",
                "reddit.com",
                "twitter.com",
                "stackoverflow.com/questions",
            )

            def _score(url: str) -> int:
                url = url.lower()
                if any(b in url for b in BLACKLIST):
                    return -1
                score = 0
                for p in PREFERRED:
                    if p in url:
                        score += 1
                return score

            seen_domains: set[str] = set()
            selected: list[str] = []
            for hit in sorted(
                hits, key=lambda h: _score(h.get("href", "")), reverse=True
            ):
                url = hit.get("url", "")
                domain = urlparse(url).netloc
                if not url or _score(url) < 0:
                    continue
                if domain not in seen_domains:
                    seen_domains.add(domain)
                    selected.append(url)
                if len(selected) >= max_urls:
                    break

            if not selected:
                return "No suitable documentation URLs found."

            # ── 3. Kick off indexing job ──────────────────────────────────────
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            runtime_dir = os.path.join(base_dir, ".runtime")
            os.makedirs(runtime_dir, exist_ok=True)
            job_id = str(uuid.uuid4())[:8]

            urls_file = os.path.join(runtime_dir, f"doc_{job_id}_urls.json")
            with open(urls_file, "w") as fh:
                json.dump(selected, fh)

            with _JOBS_LOCK:
                _JOBS[job_id] = {
                    "status": "running",
                    "project_id": f"doc:{topic}",
                    "project_path": f"docs://{topic}",
                    "file_count": len(selected),
                    "struct_rc": 0,
                    "sem_rc": None,
                    "logs": [],
                    "started_at": time.time(),
                    "finished_at": None,
                }

            doc_cmd = [
                sys.executable,
                os.path.join(base_dir, "tools", "docs", "indexer.py"),
                "--urls-file",
                urls_file,
                "--topic",
                topic,
            ]
            proc = subprocess.Popen(
                doc_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            threading.Thread(
                target=_drain_proc_output,
                args=(proc, job_id, "[doc]", "sem_rc"),
                daemon=True,
            ).start()
            threading.Thread(
                target=_finalize_job, args=(job_id, urls_file), daemon=True
            ).start()

            url_list = "\n".join(f"  • {u}" for u in selected)
            return (
                f"Research + indexing started for '{topic}' — '{query}'\n\n"
                f"URLs selected ({len(selected)}):\n{url_list}\n\n"
                f"  job_id: {job_id}\n"
                f"  topic:  {topic}\n\n"
                f"Use get_index_status('{job_id}') to monitor.\n"
                f"Use search_documentation(query, topic='{topic}') once done."
            )
        except Exception as e:
            return f"Error in research_and_index: {e}"

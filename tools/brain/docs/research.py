"""tools/docs/research.py — documentation research tools."""

import asyncio
import json
import os
import threading
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from mcp.server.fastmcp import FastMCP

from _jobs import _JOBS, _JOBS_LOCK, _drain_proc_output, _finalize_job
from _runtime import resolve_python_runtime
from tools.brain.docs.config import DEFAULT_TOPIC_SEED_URLS, score_documentation_url


async def _web_search(query: str, max_results: int = 10) -> list[dict[str, str]]:
    """Discover public URLs with the installed key-free search client off the event loop."""
    def search() -> list[dict[str, str]]:
        from ddgs import DDGS

        with DDGS(timeout=10) as client:
            rows = client.text(query, max_results=max_results, backend="duckduckgo,bing,brave")
        hits = []
        seen = set()
        for row in rows or []:
            url = row.get("href", "")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or url in seen:
                continue
            seen.add(url)
            hits.append({"url": url, "title": row.get("title", ""), "content": row.get("body", "")})
            if len(hits) >= max_results:
                break
        return hits

    return await asyncio.to_thread(search)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def research_documentation(topic: str, query: str) -> str:
        """
        Search the web using the installed ddgs client (no API key).
        Returns external documentation relevant to a topic/query.
        Returns candidate URLs with titles and snippets for the agent to review.
        Call download_documentation() with chosen URLs to crawl and index them.

        This tool is for third-party documentation discovery only. It does not
        inspect the target repo, synthesize a repo-specific guide, or index
        authored notes. For that workflow, use author_and_index_documentation().

        Topic naming guidance:
        - Prefer repo-scoped topics for repo-specific work, such as
          '<repo>-<integration>' or '<repo>-<system>'.
        - Avoid generic topics like 'quickbooks' unless cross-repo sharing is
          intentionally desired.

        Args:
            topic: Library or product name (e.g. 'neo4j', 'pgvector', 'crawl4ai').
            query: Specific question or feature to find docs for.
        """
        try:
            import httpx

            search_query = f"{topic} {query}"
            hits = await _web_search(search_query, max_results=10)
            if not hits:
                hits = await _web_search(
                    f"{topic} {query} documentation", max_results=10
                )

            if not hits:
                return (
                    f"No results found for: {topic} — {query}  Supply known URLs to download_documentation() to crawl directly."
                )

            lines = [f"Documentation search: '{topic}' — '{query}'", ""]
            seen_domains = set()

            for hit in hits:
                url = hit.get("url", "")
                title = hit.get("title", "")
                body = (hit.get("content") or "")[:120].replace("\n", " ")
                lines += [f"• {title}", f"  {url}", f"  {body}…", ""]

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
            return (f"Documentation discovery unavailable ({type(e).__name__}). "
                    "Supply known URLs to download_documentation() to crawl directly.")

    @mcp.tool()
    async def research_and_index(topic: str, query: str, max_urls: int = 5) -> str:
        """
        Single-call external docs pipeline: web search → auto-select best URLs → crawl & index.

        Combines research_documentation + download_documentation into one step.
        Searches for documentation relevant to the topic/query, picks the top URLs
        from high-quality domains, and immediately starts background indexing.
        Use get_index_status(job_id) to monitor, search_documentation() once done.

        This tool indexes third-party source documentation, not repo-specific
        synthesized guides. For authored guides, use author_and_index_documentation().

        Topic naming guidance:
        - Prefer repo-scoped topics for repo-specific work, such as
          '<repo>-<integration>' or '<repo>-<system>'.
        - Avoid generic topics unless cross-repo sharing is intentionally desired.

        Args:
            topic:    Library or product name (e.g. 'swift call graph', 'neo4j').
            query:    Specific question or concept to find docs for.
            max_urls: Maximum number of URLs to index (default 5, max 10).
        """
        try:
            import time
            import uuid

            max_urls = min(int(max_urls), 10)

            # ── 1. Web search ─────────────────────────────────────────────────
            search_query = f"{topic} {query}"
            hits = await _web_search(search_query, max_results=20)
            if not hits:
                hits = await _web_search(
                    f"{topic} {query} documentation", max_results=20
                )

            if not hits:
                return (
                    f"No results found for: {topic} — {query}  Supply known URLs to download_documentation() to crawl directly."
                )

            # ── 2. Filter + rank URLs ─────────────────────────────────────────
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
                return score_documentation_url(topic, url)

            seen_domains: set[str] = set()
            selected: list[str] = []
            for hit in sorted(
                hits, key=lambda h: _score(h.get("url", "")), reverse=True
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
            for extra in DEFAULT_TOPIC_SEED_URLS.get(topic, []):
                if extra not in selected:
                    selected.append(extra)
                if len(selected) >= max_urls:
                    break

            # ── 3. Kick off indexing job ──────────────────────────────────────
            repo_root = Path(__file__).resolve().parents[3]
            runtime_dir = repo_root / ".runtime"
            os.makedirs(runtime_dir, exist_ok=True)
            job_id = str(uuid.uuid4())[:8]

            urls_file = runtime_dir / f"doc_{job_id}_urls.json"
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
                    "runtime_python": None,
                    "runtime_source": None,
                    "runtime_conda_env": None,
                }

            runtime = resolve_python_runtime()
            python_cmd = list(runtime["cmd"])
            doc_cmd = python_cmd + [
                str(repo_root / "tools" / "brain" / "docs" / "indexer.py"),
                "--urls-file",
                str(urls_file),
                "--topic",
                topic,
            ]
            if topic in DEFAULT_TOPIC_SEED_URLS:
                doc_cmd.append("--discover")
            proc = subprocess.Popen(
                doc_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            with _JOBS_LOCK:
                if job_id in _JOBS:
                    _JOBS[job_id]["runtime_python"] = runtime.get("python")
                    _JOBS[job_id]["runtime_source"] = runtime.get("source")
                    _JOBS[job_id]["runtime_conda_env"] = runtime.get("conda_env")
                    _JOBS[job_id]["logs"].append(
                        "[runtime] "
                        f"python={runtime.get('python')} "
                        f"source={runtime.get('source')} "
                        f"conda_env={runtime.get('conda_env') or '-'}"
                    )
            threading.Thread(
                target=_drain_proc_output,
                args=(proc, job_id, "[doc]", "sem_rc"),
                daemon=True,
            ).start()
            threading.Thread(
                target=_finalize_job, args=(job_id, str(urls_file)), daemon=True
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
            return (f"Research/indexing failed ({type(e).__name__}). "
                    "Supply known URLs to download_documentation() to crawl directly.")

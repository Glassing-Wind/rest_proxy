"""tools/docs/index_jobs.py — background indexing jobs."""

import json
import os
import sys
import threading
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP

from _jobs import _JOBS, _JOBS_LOCK, _drain_proc_output, _finalize_job
from _runtime import resolve_python_runtime
from tools.brain.docs.config import DEFAULT_TOPIC_SEED_URLS


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def download_documentation(urls: list, topic: str) -> str:
        """
        Crawl, extract, chunk, embed, and index external documentation URLs into Postgres.
        Returns immediately with a job_id. Use get_index_status(job_id) to monitor.

        By default, only the provided seed URLs are crawled. Set
        LM_PROXY_DOCS_DISCOVER=1 to enable llms.txt/sitemap/link discovery.
        Content is extracted from crawled pages via the docs pipeline
        (crawlee + trafilatura), then chunked at heading-section boundaries
        using an AST-aware splitter (tree-sitter markdown).
        Each chunk includes a context_path breadcrumb (e.g. ['GRPCServer', 'Error Handling']).
        Idempotent: re-crawling updates existing chunks rather than duplicating.

        This tool is for third-party documentation ingestion. It does not write
        or synthesize repo-specific guides. For authored guides, use
        author_and_index_documentation() or index_local_documentation_file().

        Topic naming guidance:
        - Prefer repo-scoped topics for repo-specific work, such as
          '<repo>-<integration>' or '<repo>-<system>'.
        - Avoid generic topics like 'quickbooks' unless cross-repo sharing is
          intentionally desired.

        Args:
            urls:  List of documentation URLs to crawl (seed URLs).
            topic: Label for these docs (e.g. 'neo4j', 'pgvector').
        """
        try:
            import time, uuid

            if not urls:
                return "Error: no URLs provided."

            repo_root = Path(__file__).resolve().parents[3]
            runtime_dir = repo_root / ".runtime"
            os.makedirs(runtime_dir, exist_ok=True)
            job_id = str(uuid.uuid4())[:8]

            expanded_urls = list(dict.fromkeys(list(urls) + DEFAULT_TOPIC_SEED_URLS.get(topic, [])))
            urls_file = runtime_dir / f"doc_{job_id}_urls.json"
            with open(urls_file, "w") as fh:
                json.dump(expanded_urls, fh)

            with _JOBS_LOCK:
                _JOBS[job_id] = {
                    "status": "running",
                    "project_id": f"doc:{topic}",
                    "project_path": f"docs://{topic}",
                    "file_count": len(expanded_urls),
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
            if os.getenv("LM_PROXY_DOCS_DISCOVER", "").lower() in {
                "1",
                "true",
                "yes",
            } or topic in DEFAULT_TOPIC_SEED_URLS:
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

            return (
                f"Documentation indexing started in background.\n"
                f"  job_id: {job_id}\n"
                f"  topic:  {topic}\n"
                f"  seeds:  {len(expanded_urls)} URL(s)\n"
                f"\nUse get_index_status('{job_id}') to monitor progress.\n"
                f"Use search_documentation(query, topic='{topic}') once done."
            )
        except Exception as e:
            return f"Error starting doc indexer: {e}"

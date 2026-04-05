"""tools/docs/index_jobs.py — background indexing jobs."""

import json
import os
import sys
import threading
import subprocess
from mcp.server.fastmcp import FastMCP

from _jobs import _JOBS, _JOBS_LOCK, _drain_proc_output, _finalize_job


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def download_documentation(urls: list, topic: str) -> str:
        """
        Crawl, extract, chunk, embed, and index documentation URLs into Postgres.
        Returns immediately with a job_id. Use get_index_status(job_id) to monitor.

        By default, only the provided seed URLs are crawled. Set
        LM_PROXY_DOCS_DISCOVER=1 to enable llms.txt/sitemap/link discovery.
        Content is extracted as native markdown via crawl4ai, then chunked at
        heading-section boundaries using an AST-aware splitter (tree-sitter markdown).
        Each chunk includes a context_path breadcrumb (e.g. ['GRPCServer', 'Error Handling']).
        Idempotent: re-crawling updates existing chunks rather than duplicating.

        Args:
            urls:  List of documentation URLs to crawl (seed URLs).
            topic: Label for these docs (e.g. 'neo4j', 'pgvector').
        """
        try:
            import time, uuid

            if not urls:
                return "Error: no URLs provided."

            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            runtime_dir = os.path.join(repo_root, ".runtime")
            os.makedirs(runtime_dir, exist_ok=True)
            job_id = str(uuid.uuid4())[:8]

            urls_file = os.path.join(runtime_dir, f"doc_{job_id}_urls.json")
            with open(urls_file, "w") as fh:
                json.dump(list(urls), fh)

            with _JOBS_LOCK:
                _JOBS[job_id] = {
                    "status": "running",
                    "project_id": f"doc:{topic}",
                    "project_path": f"docs://{topic}",
                    "file_count": len(urls),
                    "struct_rc": 0,
                    "sem_rc": None,
                    "logs": [],
                    "started_at": time.time(),
                    "finished_at": None,
                }

            doc_cmd = [
                sys.executable,
                os.path.join(repo_root, "tools", "docs", "indexer.py"),
                "--urls-file",
                urls_file,
                "--topic",
                topic,
            ]
            if os.getenv("LM_PROXY_DOCS_DISCOVER", "").lower() in {
                "1",
                "true",
                "yes",
            }:
                doc_cmd.append("--discover")
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

            return (
                f"Documentation indexing started in background.\n"
                f"  job_id: {job_id}\n"
                f"  topic:  {topic}\n"
                f"  seeds:  {len(urls)} URL(s)\n"
                f"\nUse get_index_status('{job_id}') to monitor progress.\n"
                f"Use search_documentation(query, topic='{topic}') once done."
            )
        except Exception as e:
            return f"Error starting doc indexer: {e}"

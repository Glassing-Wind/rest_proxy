#!/usr/bin/env python3
"""tools/docs/indexer.py – Documentation crawl, extract, chunk, embed, and store pipeline.

Pipeline per URL:
  1. llms.txt discovery  — try {root}/llms.txt → sitemap.xml fallback
  2. crawlee-python       — AdaptivePlaywrightCrawler (HTTP first, Playwright fallback)
  3. Trafilatura         — main-content extraction (strips nav, ads, sidebars)
  4. Chunk               — sliding window (250 lines / 25-line overlap)
  5. Embed               — embedding_service.embed_batch()
  6. Upsert              — doc_embeddings ON CONFLICT (chunk_id) DO UPDATE

CLI:
    python tools/docs/indexer.py --urls-file urls.json --topic "neo4j" --job-id "abc123"
"""

import sys
import os

# sentence_transformers must own the process Metal/BLAS context first
from sentence_transformers import SentenceTransformer  # noqa: E402

import asyncio
import json
from typing import List

from dotenv import load_dotenv

_base_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(os.path.dirname(_base_dir))
load_dotenv(os.path.join(_repo_root, ".env"))
sys.path.insert(0, _repo_root)

from tools.docs.config import DEFAULT_TOPIC_URL_FILTERS, DEFAULT_URL_FILTERS
from tools.docs.discovery import discover_pages
from tools.docs.crawl import crawl_pages
from tools.docs.chunking import chunk_content
from tools.docs.pipeline import index_docs

__all__ = [
    "discover_pages",
    "crawl_pages",
    "chunk_content",
    "index_docs",
]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Documentation indexer")
    parser.add_argument(
        "--urls-file", required=False, help="JSON file with list of URLs"
    )
    parser.add_argument("--topic", required=False, help="Topic label (e.g. 'neo4j')")
    parser.add_argument("urls_file_pos", nargs="?", help="Positional JSON file path")
    parser.add_argument("topic_pos", nargs="?", help="Positional topic label")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Enable llms.txt/sitemap/link discovery for each seed URL",
    )
    parser.add_argument(
        "--url-filter",
        action="append",
        default=[],
        help="Substring filter to exclude URLs (repeatable)",
    )
    args = parser.parse_args()

    urls_file = args.urls_file or args.urls_file_pos
    topic = args.topic or args.topic_pos
    if not urls_file:
        parser.error("urls file is required (positional or --urls-file)")
    if not topic:
        parser.error("topic is required (positional or --topic)")

    with open(urls_file) as fh:
        urls: List[str] = json.load(fh)

    print(f"[doc-indexer] {len(urls)} seed URLs  topic={topic}", flush=True)
    topic_filters = DEFAULT_TOPIC_URL_FILTERS.get(topic, [])
    url_filters = DEFAULT_URL_FILTERS + topic_filters + (args.url_filter or [])

    asyncio.run(
        index_docs(
            urls,
            topic,
            discover=bool(args.discover),
            url_filters=url_filters or None,
        )
    )

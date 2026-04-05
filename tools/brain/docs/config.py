"""tools/docs/config.py — documentation indexer defaults."""

# ── Tuning ───────────────────────────────────────────────────────────────────
CHUNK_LINES = 250  # fallback: target lines per chunk
OVERLAP_LINES = 25  # fallback: line overlap between chunks
CHUNK_MAX_BYTES = 8_000  # max bytes per section chunk (ts_pack split_markdown)
MAX_PAGE_BYTES = 2_000_000  # skip pages > 2MB of markdown
DEFAULT_URL_FILTERS = [
    "/python/docs/0.6/",
    "/python/api/0.6/",
]
DEFAULT_TOPIC_URL_FILTERS = {
    "neo4j-cypher": [
        "/docs/cypher-manual/current/cypher-neo4j/",
        "/docs/cypher-manual/current/cypher-aura/",
        "/docs/cypher-manual/current/cypher-overview/",
    ]
}
DEFAULT_FORCE_PLAYWRIGHT_HOSTS = [
    "neo4j.com",
]
DEFAULT_SKIP_SITEMAP_HOSTS = [
    "neo4j.com",
]

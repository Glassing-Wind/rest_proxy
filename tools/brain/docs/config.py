"""tools/docs/config.py — documentation indexer defaults."""

from __future__ import annotations

from typing import Any, Dict
from urllib.parse import urlparse

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

DEFAULT_TOPIC_SEED_URLS = {
    "neo4j": [
        "https://neo4j.com/docs/python-manual/current/",
        "https://neo4j.com/docs/operations-manual/current/",
        "https://neo4j.com/developer/kb/",
        "https://neo4j.com/docs/java-reference/current/transaction-management/",
    ]
}

TOPIC_FAMILIES = {
    "neo4j": ["neo4j", "neo4j-%"],
}

# Host-specific crawling profiles. These exist to capture the selector-driven
# extraction patterns recommended by the Crawlee docs for JS-heavy sites.
DOC_HOST_PROFILES: Dict[str, Dict[str, Any]] = {
    "neo4j.com": {
        "ready_selectors": [
            "main",
            "article",
        ],
        "content_selectors": [
            "main",
            "article",
            "#content",
            ".content",
        ],
        "link_selectors": [
            "main a",
            "article a",
            "nav a",
            "a",
        ],
        "settle_delay_ms": 1200,
    },
    "developer.intuit.com": {
        "force_playwright": True,
        "direct_playwright": True,
        "skip_llms": True,
        "skip_sitemap": True,
        "ready_selectors": [
            "#app",
            "[class*=content]",
        ],
        "content_selectors": [
            "[class*=content]",
            "#app",
            "main",
            "article",
            "[role=main]",
        ],
        "link_selectors": [
            "[class*=content] a",
            "main a",
            "article a",
            "nav a",
            "a",
        ],
        "settle_delay_ms": 8000,
    },
}


def _normalize_host(url_or_host: str) -> str:
    parsed = urlparse(url_or_host)
    return (parsed.netloc or url_or_host).lower()


def get_host_profile(url_or_host: str) -> Dict[str, Any]:
    host = _normalize_host(url_or_host)
    merged: Dict[str, Any] = {}
    for pattern, profile in DOC_HOST_PROFILES.items():
        if pattern in host:
            merged.update(profile)
    return merged


def host_profile_flag(url_or_host: str, key: str) -> bool:
    return bool(get_host_profile(url_or_host).get(key))


def host_profile_selectors(url_or_host: str, key: str) -> list[str]:
    value = get_host_profile(url_or_host).get(key) or []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def host_profile_delay_ms(url_or_host: str) -> int:
    raw = get_host_profile(url_or_host).get("settle_delay_ms", 0)
    try:
        return max(0, int(raw))
    except Exception:
        return 0


def score_documentation_url(topic: str, url: str) -> int:
    """Rank canonical docs higher during discovery for known topics."""
    url_lower = (url or "").lower()
    topic_lower = (topic or "").lower()
    score = 0

    generic_preferred = (
        "docs.",
        "readthedocs",
        "github.com",
        "developer.apple.com",
        "swift.org",
    )
    for token in generic_preferred:
        if token in url_lower:
            score += 1

    if topic_lower == "neo4j":
        if "neo4j.com/developer/kb/" in url_lower:
            score += 8
        if "neo4j.com/docs/operations-manual/" in url_lower:
            score += 7
        if "neo4j.com/docs/python-manual/" in url_lower:
            score += 6
        if "neo4j.com/docs/java-reference/" in url_lower:
            score += 5
        if "neo4j.com/docs/" in url_lower:
            score += 3

    return score


def topic_family_patterns(topic: str) -> list[str]:
    topic = (topic or "").strip()
    if not topic:
        return []
    return list(TOPIC_FAMILIES.get(topic, [topic]))

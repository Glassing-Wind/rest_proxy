"""tools/docs/chunking.py — documentation chunking helpers."""

import inspect
import re
import sys
from typing import Dict, List
from urllib.parse import urlparse

from tools.brain.docs.config import (
    CHUNK_LINES,
    CHUNK_MAX_BYTES,
    CHUNK_OVERLAP_BYTES,
    OVERLAP_LINES,
)


def _infer_doc_type(url: str, title: str) -> str:
    url_lower = (url or "").lower()
    title_lower = (title or "").lower()
    if "/developer/kb/" in url_lower or "knowledge base" in title_lower:
        return "knowledge-base"
    if "/operations-manual/" in url_lower:
        return "operations-manual"
    if "/python-manual/" in url_lower:
        return "python-driver-manual"
    if "/java-reference/" in url_lower:
        return "java-reference"
    if "/cypher-manual/" in url_lower:
        return "cypher-manual"
    if "/api/" in url_lower:
        return "api-reference"
    return "documentation"


def chunk_content(
    content: str, url: str, title: str, fmt: str = "markdown"
) -> List[Dict]:
    """Chunk documentation content with ts_pack, supporting both 'markdown' and 'xml' formats.

    - fmt='markdown': used for raw GitHub READMEs (native # headings)
    - fmt='xml':      used for trafilatura XML output (preserves <head> tags from HTML)

    ts_pack populates context_path from the section-node hierarchy in the AST.
    Falls back to a simple line-window split if ts_pack is unavailable.
    Returns list of dicts: {text, context_path}.
    """
    if not content.strip():
        return []

    domain = urlparse(url).netloc
    doc_type = _infer_doc_type(url, title)

    try:
        import tree_sitter_language_pack as ts_pack

        config_kwargs = {"chunk_max_size": CHUNK_MAX_BYTES}
        process_config_sig = inspect.signature(ts_pack.ProcessConfig)
        if "chunk_overlap" in process_config_sig.parameters:
            config_kwargs["chunk_overlap"] = CHUNK_OVERLAP_BYTES
        elif "_chunk_overlap" in process_config_sig.parameters:
            config_kwargs["_chunk_overlap"] = CHUNK_OVERLAP_BYTES
        config = ts_pack.ProcessConfig(fmt, **config_kwargs)
        result = ts_pack.process(content, config)
        chunks = result.get("chunks", [])
        if chunks:

            def _strip_xml(text: str) -> str:
                """Remove XML tags, collapse whitespace — keeps human-readable content."""
                text = re.sub(r"<[^>]+>", " ", text)
                return re.sub(r" {2,}", " ", text).strip()

            strip = _strip_xml if fmt == "xml" else (lambda t: t)
            out = []
            for c in chunks:
                chunk_text = c.get("content", "")
                meta = c.get("metadata", {}) or {}
                if not chunk_text.strip() or meta.get("has_error_nodes", False):
                    continue
                context_path = meta.get("context_path") or []
                section = " > ".join(str(item) for item in context_path if str(item).strip())
                header = (
                    f"// Source: {url}\n"
                    f"// Domain: {domain}\n"
                    f"// Doc Type: {doc_type}\n"
                    f"// Title: {title}\n"
                    + (f"// Section: {section}\n" if section else "")
                    + "\n"
                )
                out.append(
                    {
                        "text": header + strip(chunk_text),
                        "context_path": context_path,
                        "domain": domain,
                        "doc_type": doc_type,
                        "section_title": context_path[-1] if context_path else "",
                    }
                )
            return out
    except Exception as e:
        print(
            f"[doc-indexer] ts_pack chunk error ({fmt}): {e}",
            file=sys.stderr,
            flush=True,
        )

    # Fallback: simple line-window split (no context_path)
    lines = content.splitlines()
    out = []
    i = 0
    while i < len(lines):
        chunk = (
            f"// Source: {url}\n"
            f"// Domain: {domain}\n"
            f"// Doc Type: {doc_type}\n"
            f"// Title: {title}\n\n"
            + "\n".join(lines[i : i + CHUNK_LINES])
        )
        if chunk.strip():
            out.append(
                {
                    "text": chunk,
                    "context_path": [],
                    "domain": domain,
                    "doc_type": doc_type,
                    "section_title": "",
                }
            )
        i += CHUNK_LINES - OVERLAP_LINES
    return out

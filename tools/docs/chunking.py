"""tools/docs/chunking.py — documentation chunking helpers."""

import re
import sys
from typing import Dict, List

from tools.docs.config import CHUNK_LINES, CHUNK_MAX_BYTES, OVERLAP_LINES


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

    header = f"// Source: {url}\n// Title: {title}\n\n"

    try:
        import tree_sitter_language_pack as ts_pack

        config = ts_pack.ProcessConfig(fmt, chunk_max_size=CHUNK_MAX_BYTES)
        result = ts_pack.process(content, config)
        chunks = result.get("chunks", [])
        if chunks:

            def _strip_xml(text: str) -> str:
                """Remove XML tags, collapse whitespace — keeps human-readable content."""
                text = re.sub(r"<[^>]+>", " ", text)
                return re.sub(r" {2,}", " ", text).strip()

            strip = _strip_xml if fmt == "xml" else (lambda t: t)
            return [
                {
                    "text": header + strip(c["content"]),
                    "context_path": c.get("metadata", {}).get("context_path") or [],
                }
                for c in chunks
                if c.get("content", "").strip()
                and not c.get("metadata", {}).get("has_error_nodes", False)
            ]
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
        chunk = header + "\n".join(lines[i : i + CHUNK_LINES])
        if chunk.strip():
            out.append({"text": chunk, "context_path": []})
        i += CHUNK_LINES - OVERLAP_LINES
    return out

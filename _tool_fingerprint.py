"""Shared tool fingerprint helpers for MCP server freshness checks."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path


def _tool_source_files(mcp) -> list[str]:
    paths: set[str] = set()
    for tool in mcp._tool_manager.list_tools():
        try:
            source = inspect.getsourcefile(tool.fn)
        except Exception:
            source = None
        if not source:
            continue
        try:
            paths.add(str(Path(source).resolve()))
        except Exception:
            continue
    return sorted(paths)


def compute_tool_fingerprint(mcp) -> tuple[str, int]:
    """Return a short fingerprint of tool names plus their implementation files."""
    try:
        tools = sorted(mcp._tool_manager.list_tools(), key=lambda tool: tool.name)
    except Exception:
        return hashlib.sha256(b"[]").hexdigest()[:12], 0

    tool_names = [tool.name for tool in tools]
    source_files = _tool_source_files(mcp)
    source_hashes: list[dict[str, str]] = []
    for path in source_files:
        try:
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except Exception:
            digest = "<unreadable>"
        source_hashes.append({"path": path, "sha256": digest})

    payload = {
        "tool_names": tool_names,
        "tool_sources": source_hashes,
    }
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    return fingerprint, len(tool_names)

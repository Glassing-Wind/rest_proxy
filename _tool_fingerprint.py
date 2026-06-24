"""Shared tool fingerprint helpers for MCP server freshness checks."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

DEFAULT_IMPLEMENTATION_SOURCE_ROOTS = (
    REPO_ROOT / "tools",
    REPO_ROOT / "graphrag_core",
    REPO_ROOT / "memory",
    REPO_ROOT / "local_embeddings",
    REPO_ROOT / "_helpers.py",
    REPO_ROOT / "_jobs.py",
    REPO_ROOT / "_mcp.py",
    REPO_ROOT / "_runtime.py",
    REPO_ROOT / "_semantic_contract.py",
    REPO_ROOT / "_tool_fingerprint.py",
    REPO_ROOT / "embedding_service.py",
    REPO_ROOT / "graph_bootstrap.py",
)


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


def _implementation_source_files(
    roots: list[str | Path] | tuple[str | Path, ...],
) -> list[str]:
    paths: set[str] = set()
    for root_value in roots:
        try:
            root = Path(root_value).resolve()
        except Exception:
            continue
        if root.is_file():
            if root.suffix == ".py":
                paths.add(str(root))
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            paths.add(str(path.resolve()))
    return sorted(paths)


def compute_tool_fingerprint(
    mcp,
    *,
    runtime_source_files: list[str | Path] | None = None,
    implementation_source_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
) -> tuple[str, int]:
    """Return a short fingerprint of registered tools and their local runtime."""
    try:
        tools = sorted(mcp._tool_manager.list_tools(), key=lambda tool: tool.name)
    except Exception:
        return hashlib.sha256(b"[]").hexdigest()[:12], 0

    tool_names = [tool.name for tool in tools]
    # Keep freshness stable across import order while including explicit
    # transport entrypoints that are not themselves registered tool sources.
    source_files = set(_tool_source_files(mcp))
    source_files.update(
        _implementation_source_files(
            implementation_source_roots or DEFAULT_IMPLEMENTATION_SOURCE_ROOTS
        )
    )
    for source in runtime_source_files or []:
        try:
            source_files.add(str(Path(source).resolve()))
        except Exception:
            continue
    source_hashes: list[dict[str, str]] = []
    for path in sorted(source_files):
        try:
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except Exception:
            digest = "<unreadable>"
        source_hashes.append({"path": path, "sha256": digest})

    payload = {
        "tool_names": tool_names,
        "runtime_sources": source_hashes,
    }
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    return fingerprint, len(tool_names)

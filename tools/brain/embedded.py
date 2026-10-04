"""Explicit experimental embedded tools shared by STDIO and HTTP MCP."""
from __future__ import annotations

import os

from memory.embedded_runtime import get_embedded_runtime
from memory.storage_config import embedded_graph_selected


async def index_embedded_repository(source_root: str, project_id: str, paths: list[str]) -> dict:
    """Replace this project's embedded snapshot from an explicit relative-path manifest.

    Waits for completion. Omitted files disappear from the publication. Uses an
    already loaded loopback model; does not invoke external-storage index workers.
    """
    result = await get_embedded_runtime().index(source_root, project_id, paths)
    return {'project_id': result['project_id'], 'run_id': result['run_id'],
            'files': len(result['manifest']['files']), 'retrieval': result['manifest']['retrieval']}


async def search_embedded_repository(project_id: str, query: str, mode: str = 'hybrid',
                                     limit: int = 10) -> list[dict]:
    """Search only published chunks with source hashes/run citations. Text needs no model."""
    return await get_embedded_runtime().search(project_id, query, mode, limit)


async def describe_embedded_file(project_id: str, file_path: str, start_line: int = 1,
                                 max_lines: int = 80, max_chars: int = 12000) -> dict | None:
    """Read bounded numbered original source and outlines from the published snapshot."""
    return await get_embedded_runtime().describe_file(project_id, file_path, start_line=start_line,
                                                     max_lines=max_lines, max_chars=max_chars)


async def get_embedded_overview(project_id: str) -> dict | None:
    """Inspect publication identity, file/symbol counts and embedding configuration."""
    return await get_embedded_runtime().overview(project_id)


def register(mcp):
    if embedded_graph_selected() and os.getenv('LM_PROXY_EMBEDDED_STATE', '').strip():
        for tool in (index_embedded_repository, search_embedded_repository,
                     describe_embedded_file, get_embedded_overview):
            mcp.tool()(tool)

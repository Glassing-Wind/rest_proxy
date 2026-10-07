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
            'attempt_id': result['attempt_id'], 'files': len(result['manifest']['files']), 'retrieval': result['manifest']['retrieval']}


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


async def list_embedded_projects(limit: int = 25, after: str = '') -> dict:
    """List committed embedded project IDs/paths/runs with a bounded project-ID cursor."""
    return await get_embedded_runtime().list_projects(limit=limit, after=after)


async def get_embedded_file_facts(project_id: str, file_path: str, limit: int = 50, offset: int = 0) -> dict | None:
    """Read cited imports, syntactic call sites and native route/HTTP facts from a publication.

    Targets are observations, not resolved CALLS edges. Older snapshots require reindexing.
    """
    return await get_embedded_runtime().file_facts(project_id, file_path, limit=limit, offset=offset)


async def get_embedded_relationships(project_id: str, kind: str = 'calls', file_path: str = '',
                                     direction: str = 'out', limit: int = 50, after: str = '', symbol_id: str = '') -> dict | None:
    """Read calls/imports/symbol_imports/http_routes candidates with endpoint source citations.

    These are static source bindings, not guarantees of runtime dispatch. Use in/out
    to inspect incoming callers/importers or outgoing candidates for a published file.
    symbol_imports binds conservative Python from-imports to local module functions;
    use direction in with symbol_id to inspect that function's importing files.
    """
    return await get_embedded_runtime().relationships(project_id, kind=kind, file_path=file_path,
                                                     direction=direction, limit=limit, after=after, symbol_id=symbol_id)


async def get_embedded_project_metadata(project_id: str) -> dict:
    """Read revisioned user/agent annotations; these are not verified repository evidence."""
    return await get_embedded_runtime().project_metadata(project_id)


async def update_embedded_project_metadata(project_id: str, metadata: dict,
                                           expected_revision: int, expected_run_id: str) -> dict:
    """Replace bounded project annotations with exact revision/publication preconditions.

    Read metadata first. Conflict does not write; review current metadata before retrying.
    An empty object clears annotations while advancing revision. No evidence is changed.
    """
    return await get_embedded_runtime().project_metadata(project_id, metadata=metadata,
                                                       expected_revision=expected_revision,
                                                       expected_run_id=expected_run_id)


async def get_embedded_indexing_attempt(project_id: str) -> dict:
    """Read the latest durable owned attempt, last phase and current publication identity.

    Reopen marks unfinished work interrupted; no automatic resume or legacy job/PID control.
    """
    return await get_embedded_runtime().indexing_attempt(project_id)


async def get_embedded_workspace_activity(project_id: str) -> dict:
    """Read durable watch intent and unexpired client leases; no worker/process liveness claim."""
    return await get_embedded_runtime().workspace_activity(project_id)


async def set_embedded_watch_intent(project_id: str, requested: bool,
                                    expected_revision: int, expected_run_id: str) -> dict:
    """Persist desired watch state; does not activate an indexing worker."""
    return await get_embedded_runtime().workspace_activity(project_id, watch_requested=requested,
        expected_revision=expected_revision, expected_run_id=expected_run_id)


async def refresh_embedded_session(project_id: str, session_id: str,
                                   expected_revision: int, expected_run_id: str, lease_seconds: int = 900) -> dict:
    """Refresh an explicit client lease (60..3600 seconds); zero releases it.

    Session IDs are caller-supplied identifiers, not authenticated identities or process handles.
    """
    if not session_id:
        raise ValueError('Use a nonempty explicit session ID')
    return await get_embedded_runtime().workspace_activity(project_id, session_id=session_id,
        lease_seconds=lease_seconds, expected_revision=expected_revision, expected_run_id=expected_run_id)


async def resolve_embedded_session(session_id: str) -> dict:
    """Discover a unique unexpired client lease in a current published root; refuse ambiguity."""
    return await get_embedded_runtime().resolve_session(session_id)


async def configure_embedded_project_watch(project_id: str, enable: bool = False,
                                           expected_revision: int | None = None, expected_run_id: str = '') -> dict:
    """Watch this project: preview model/service readiness and current manifest scope.

    Enable requires the preview revision/run. Blocked setup does not change project intent.
    This action does not enable global service permission, load a model or expand the manifest.
    """
    return await get_embedded_runtime().configure_project_watch(project_id, enable=enable,
        expected_revision=expected_revision, expected_run_id=expected_run_id)


def register(mcp):
    if embedded_graph_selected() and os.getenv('LM_PROXY_EMBEDDED_STATE', '').strip():
        for tool in (index_embedded_repository, search_embedded_repository,
                     describe_embedded_file, get_embedded_overview, list_embedded_projects, get_embedded_file_facts, get_embedded_relationships,
                     get_embedded_project_metadata, update_embedded_project_metadata, get_embedded_indexing_attempt, get_embedded_workspace_activity,
                     set_embedded_watch_intent, refresh_embedded_session, resolve_embedded_session, configure_embedded_project_watch):
            mcp.tool()(tool)

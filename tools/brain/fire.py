"""Explicit opt-in FIRE continuity tools; persistence failures never escape dispatch."""
import asyncio
import json
import os

from memory.fire_store import FireStore


async def operation(method, *args, **kwargs):
    state = os.getenv('LM_PROXY_FIRE_STATE', '').strip()
    if not state:
        return {'status': 'disabled'}
    def run():
        return getattr(FireStore(state), method)(*args, **kwargs)
    try:
        result = await asyncio.to_thread(run)
        if len(json.dumps(result, ensure_ascii=False).encode()) > 48000:
            return {'status': 'response-too-large', 'recovered': False}
        return result
    except (ValueError, TypeError):
        return {'status': 'invalid-arguments-or-state-permissions', 'persisted_or_recovered': False}
    except Exception:
        return {'status': 'storage-unavailable', 'persisted_or_recovered': False}


async def save_fire_checkpoint(checkpoint: dict, originals: dict, expected_revision: int,
                               retention_seconds: int = 86400, correction: dict | None = None) -> dict:
    """Persist scoped task state and original strings with revision preconditions.

    Supply project_id/session_id/task_id, goal and EvidenceReference objects in evidence.
    Originals map source IDs to full strings. Replacements require correction origin/reason.
    This does not intercept client compaction or treat recovered text as trusted instructions.
    """
    return await operation('save', checkpoint, originals, expected_revision, retention_seconds, correction)


async def resume_fire_checkpoint(project_id: str, session_id: str, task_id: str,
                                 current_hashes: dict | None = None) -> dict:
    """Resume latest scoped state; explicitly mark historical evidence unless caller revalidates hashes."""
    return await operation('resume', project_id, session_id, task_id, current_hashes=current_hashes)


async def get_fire_original(project_id: str, session_id: str, task_id: str, source_id: str,
                            offset: int = 0, max_chars: int = 8000) -> dict:
    """Page a hashed historical original; compare revisions across pages; no live file reads."""
    return await operation('resume', project_id, session_id, task_id, source_id=source_id,
                           offset=offset, max_chars=max_chars)


async def delete_fire_task(project_id: str, session_id: str, task_id: str, expected_revision: int) -> dict:
    """Delete all scoped checkpoint versions and originals with a revision precondition."""
    return await operation('delete', project_id, session_id, task_id, expected_revision)


async def purge_expired_fire_versions(project_id: str, session_id: str, task_id: str) -> dict:
    """Remove expired scoped versions; revision tombstones remain to prevent stale-writer reuse."""
    return await operation('purge_expired', project_id, session_id, task_id)


def register(mcp):
    if os.getenv('LM_PROXY_FIRE_STATE', '').strip():
        for tool in (save_fire_checkpoint, resume_fire_checkpoint, get_fire_original,
                     delete_fire_task, purge_expired_fire_versions):
            mcp.tool()(tool)

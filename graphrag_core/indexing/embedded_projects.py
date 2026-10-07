"""Durable embedded project discovery from committed publication receipts."""
from __future__ import annotations

from pathlib import Path


async def list_projects(driver, *, limit: int = 25, after: str = '') -> dict:
    if not 1 <= limit <= 100 or len(after) > 128:
        raise ValueError('Use limit 1..100 and a project ID cursor of at most 128 characters')

    async def read(tx):
        return await (await tx.run(
            'MATCH (p:OutlinePublication) WHERE p.id > $after '
            'RETURN p.id AS project_id, p.root_path AS workspace_path, p.run_id AS run_id '
            'ORDER BY project_id LIMIT $count', after=after, count=limit + 1,
        )).data()

    async with driver.session() as session:
        rows = await session.execute_read(read)
    return {'projects': rows[:limit], 'next_cursor': rows[limit - 1]['project_id'] if len(rows) > limit else None}


async def resolve_project(driver, workspace_id: str) -> dict | None:
    """Resolve ID first, then canonical absolute path, then an unambiguous basename.

    Absolute paths never fall back to basename. No missing-project hash is invented.
    Resolution works when the original source directory no longer exists.
    """
    if not workspace_id or len(workspace_id) > 4096 or '\0' in workspace_id:
        raise ValueError('Use a nonempty workspace identifier of at most 4096 characters')
    path = Path(workspace_id).expanduser()
    absolute = path.is_absolute()
    canonical = str(path.resolve()) if absolute else None
    fields = 'RETURN p.id AS project_id, p.root_path AS workspace_path, p.run_id AS run_id LIMIT 2'

    async def read(tx):
        rows = await (await tx.run('MATCH (p:OutlinePublication {id:$selector}) ' + fields,
                                  selector=workspace_id)).data()
        matched_by = 'project_id'
        if not rows:
            if absolute:
                query = 'MATCH (p:OutlinePublication {root_path:$selector}) '
                selector = canonical
                matched_by = 'canonical_path'
            elif '/' not in workspace_id and '\\' not in workspace_id:
                query = 'MATCH (p:OutlinePublication) WHERE p.root_path ENDS WITH $selector '
                selector = '/' + workspace_id
                matched_by = 'unique_basename'
            else:
                return None
            rows = await (await tx.run(query + fields, selector=selector)).data()
        if len(rows) > 1:
            raise ValueError('Ambiguous embedded workspace; use its explicit project ID')
        return dict(rows[0], matched_by=matched_by) if rows else None

    async with driver.session() as session:
        return await session.execute_read(read)

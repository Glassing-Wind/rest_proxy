"""Latest owned embedded index attempt per project; graph receipt remains authoritative."""
from __future__ import annotations

import time


async def start_attempt(driver, project_id: str, attempt_id: str, root_path: str) -> None:
    async def write(tx):
        await tx.run('MERGE (j:EmbeddedIndexAttempt {id:$project}) '
                     'SET j.attempt_id=$attempt, j.root_path=$root, j.run_id="", '
                     'j.status="running", j.phase="snapshot", j.error_type="", j.updated_ms=$updated',
                     project=project_id, attempt=attempt_id, root=root_path,
                     updated=time.time_ns() // 1000000)
    async with driver.session() as session:
        await session.execute_write(write)


async def update_attempt(driver, project_id: str, attempt_id: str, *, phase: str, run_id: str = '') -> None:
    async def write(tx):
        await tx.run('MATCH (j:EmbeddedIndexAttempt {id:$project}) WHERE j.attempt_id=$attempt '
                     'SET j.phase=$phase, j.run_id=$run, j.updated_ms=$updated',
                     project=project_id, attempt=attempt_id, phase=phase, run=run_id,
                     updated=time.time_ns() // 1000000)
    async with driver.session() as session:
        await session.execute_write(write)


async def finish_failed_attempt(driver, project_id: str, attempt_id: str, *, cancelled: bool,
                                error_type: str) -> None:
    async def write(tx):
        # A cancellation/error delivered after commit must not overwrite successful publication.
        await tx.run('MATCH (j:EmbeddedIndexAttempt {id:$project}), (p:OutlinePublication {id:$project}) '
                     'WHERE j.attempt_id=$attempt AND j.run_id=p.run_id AND j.run_id<>"" '
                     'SET j.status="published", j.phase="published", j.error_type="", j.updated_ms=$updated',
                     project=project_id, attempt=attempt_id, updated=time.time_ns() // 1000000)
        await tx.run('MATCH (j:EmbeddedIndexAttempt {id:$project}) '
                     'WHERE j.attempt_id=$attempt AND j.status="running" '
                     'SET j.status=$status, j.error_type=$error, j.updated_ms=$updated',
                     project=project_id, attempt=attempt_id, status='cancelled' if cancelled else 'failed',
                     error=error_type[:128], updated=time.time_ns() // 1000000)
    async with driver.session() as session:
        await session.execute_write(write)


async def recover_attempts(driver) -> None:
    """Run only after acquiring the exclusive graph owner; do not resume native work."""
    async def write(tx):
        await tx.run('MATCH (j:EmbeddedIndexAttempt), (p:OutlinePublication) '
                     'WHERE j.status="running" AND j.id=p.id AND j.run_id=p.run_id AND j.run_id<>"" '
                     'SET j.status="published", j.phase="published", j.error_type="", j.updated_ms=$updated',
                     updated=time.time_ns() // 1000000)
        await tx.run('MATCH (j:EmbeddedIndexAttempt) WHERE j.status="running" '
                     'SET j.status="interrupted", j.error_type="OwnerRestart", j.updated_ms=$updated',
                     updated=time.time_ns() // 1000000)
    async with driver.session() as session:
        await session.execute_write(write)


async def read_attempt(driver, project_id: str) -> dict:
    if not project_id or len(project_id) > 128 or ':' in project_id:
        raise ValueError('Use a nonempty project ID without colons, at most 128 characters')
    async def read(tx):
        rows = await (await tx.run('MATCH (j:EmbeddedIndexAttempt {id:$project}) '
                                  'RETURN j.attempt_id AS attempt_id, j.root_path AS workspace_path, '
                                  'j.run_id AS candidate_run_id, j.status AS status, j.phase AS phase, '
                                  'j.error_type AS error_type, j.updated_ms AS updated_ms',
                                  project=project_id)).data()
        publications = await (await tx.run('MATCH (p:OutlinePublication {id:$project}) '
                                          'RETURN p.run_id AS run_id', project=project_id)).data()
        return {'project_id': project_id, **(rows[0] if rows else {'status': 'no_attempt'}),
                'published_run_id': publications[0]['run_id'] if publications else None,
                'automatic_resume': False, 'scope': 'latest-owned-embedded-attempt'}
    async with driver.session() as session:
        return await session.execute_read(read)


async def drain(operation):
    """Finish journal updates before releasing owner locks even under repeated cancellation."""
    import asyncio
    task = asyncio.create_task(operation)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass
    return task.result()

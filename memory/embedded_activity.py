"""Durable watch intent and bounded session leases; no worker activation or authentication."""
import json
import time


async def workspace_activity(driver, project_id: str, *, expected_revision: int | None = None,
                             expected_run_id: str = '', watch_requested: bool | None = None,
                             session_id: str = '', lease_seconds: int = 900) -> dict:
    if not project_id or len(project_id) > 128 or ':' in project_id:
        raise ValueError('Use a nonempty project ID without colons, at most 128 characters')
    writing = watch_requested is not None or bool(session_id)
    if writing and (type(expected_revision) is not int or expected_revision < 0 or not expected_run_id):
        raise ValueError('Writes require revision and publication preconditions')
    if watch_requested is not None and type(watch_requested) is not bool:
        raise ValueError('Watch intent must be boolean')
    if session_id and (len(session_id) > 128 or type(lease_seconds) is not int
                       or lease_seconds != 0 and not 60 <= lease_seconds <= 3600):
        raise ValueError('Use session ID at most 128 characters; lease 60..3600 seconds or zero to release')
    async def operation(tx):
        pubs = await (await tx.run('MATCH (p:OutlinePublication {id:$id}) '
                                  'RETURN p.run_id AS run_id, p.root_path AS root', id=project_id)).data()
        if not pubs:
            return {'project_id': project_id, 'status': 'not_published'}
        pub = pubs[0]
        rows = await (await tx.run('MATCH (a:WorkspaceActivity {id:$id}) '
                                  'RETURN a.revision AS revision, a.root_path AS root, a.payload_json AS payload',
                                  id=project_id)).data()
        old = rows[0] if rows else None
        revision = old['revision'] if old else 0
        same_root = old is None or old['root'] == pub['root']
        state = json.loads(old['payload']) if old and same_root else {'watch_requested': False, 'sessions': {}}
        now = time.time_ns() // 1000000
        state['sessions'] = {key: expiry for key, expiry in state['sessions'].items() if expiry > now}
        if writing:
            if expected_revision != revision or expected_run_id != pub['run_id']:
                return {'project_id': project_id, 'status': 'conflict', 'revision': revision, 'run_id': pub['run_id']}
            if watch_requested is not None:
                state['watch_requested'] = watch_requested
            if session_id:
                if lease_seconds == 0:
                    state['sessions'].pop(session_id, None)
                else:
                    if session_id not in state['sessions'] and len(state['sessions']) >= 32:
                        raise ValueError('Workspace session limit is 32 active leases')
                    state['sessions'][session_id] = now + lease_seconds * 1000
            revision += 1
            await tx.run('MERGE (a:WorkspaceActivity {id:$id}) '
                         'SET a.revision=$revision, a.root_path=$root, a.payload_json=$payload',
                         id=project_id, revision=revision, root=pub['root'],
                         payload=json.dumps(state, sort_keys=True, separators=(',', ':')))
            same_root = True
        return {'project_id': project_id, 'status': 'published' if same_root else 'workspace_changed',
                'revision': revision, 'run_id': pub['run_id'], 'workspace_path': pub['root'],
                **state, 'embedded_watch_worker_active': False, 'session_process_liveness_verified': False}
    async with driver.session() as session:
        return await (session.execute_write(operation) if writing else session.execute_read(operation))

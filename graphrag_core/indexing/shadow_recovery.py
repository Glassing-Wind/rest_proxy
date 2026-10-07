"""Local owned-writer adjudication. Remote or uncertain writers remain protected."""

import os
import socket

from graphrag_core.indexing.shadow_admin import snapshot_digest, snapshot_shadow


def require_dead_local_owner(owner: dict) -> None:
    """Accept only a positive PID on this host with an explicit no-such-process result."""
    pid = owner.get('pid')
    if owner.get('host') != socket.gethostname() or not isinstance(pid, int) or pid <= 0:
        raise RuntimeError('Recovery refused: remote or unknown worker identity')
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise RuntimeError('Recovery refused: worker inspection is uncertain') from exc
    raise RuntimeError('Recovery refused: worker PID is alive or reused')


def recovery_preview(tx, namespace: str) -> dict:
    """Read the exact owner and staging contents; duplicate owners are never eligible."""
    rows = tx.run('MATCH (s:ShadowRun {namespace:$ns}) RETURN properties(s) AS owner',
                  ns=namespace).data()
    if len(rows) != 1:
        raise RuntimeError('Recovery refused: missing or ambiguous owner')
    owner = rows[0]['owner']
    if (not owner.get('owner') or owner.get('status') != 'running'
            or namespace != f"{owner.get('project_id')}::shadow::{owner.get('run_id')}"
            or not str(owner.get('run_id', '')).startswith(str(owner.get('project_id')) + ':')):
        raise RuntimeError('Recovery refused: incomplete or terminal ownership')
    require_dead_local_owner(owner)
    return {'namespace': namespace, 'owner': owner, 'staging': snapshot_shadow(tx, namespace)}


def recover_owned_shadow(tx, preview: dict, decision_id: str) -> dict:
    """Mark proven abandoned ownership terminal; preserve all graph data for normal cleanup."""
    owner = preview['owner']
    require_dead_local_owner(owner)
    row = tx.run(
        'MATCH (p:Project {id:$project}) '
        'SET p.shadow_admin_lock=coalesce(p.shadow_admin_lock,0)+1 '
        'WITH p MATCH (s:ShadowRun {namespace:$ns,owner:$owner}) '
        'SET s.recovery_lock=coalesce(s.recovery_lock,0) '
        'WITH p,s WHERE s.status="running" '
        'AND (NOT coalesce(p.struct_index_status,"") IN ["in_progress","running","cancelling"] '
        'OR p.struct_index_run_id=$run) '
        'AND (p.struct_active_run_id IS NULL OR p.struct_active_run_id <> $run) '
        'AND NOT EXISTS { MATCH (other:ShadowRun {project_id:$project,status:"running"}) '
        'WHERE other.namespace <> $ns } '
        'AND NOT EXISTS { MATCH (r:IndexRun {id:$run}) WHERE r.promoted_at IS NOT NULL } '
        'RETURN properties(s) AS owner',
        project=owner['project_id'], ns=preview['namespace'], owner=owner['owner'], run=owner['run_id'],
    ).single()
    if not row:
        raise RuntimeError('Recovery refused: publication or project activity changed')
    current_owner = dict(row['owner'])
    current_owner.pop('recovery_lock', None)
    expected_owner = dict(owner)
    expected_owner.pop('recovery_lock', None)
    if current_owner != expected_owner:
        raise RuntimeError('Recovery refused: ownership changed')
    if snapshot_digest(snapshot_shadow(tx, preview['namespace'])) != snapshot_digest(preview['staging']):
        raise RuntimeError('Recovery refused: staging contents changed')
    require_dead_local_owner(owner)
    tx.run(
        'MATCH (s:ShadowRun {namespace:$ns,owner:$owner}) '
        'SET s.status="failed",s.finished_at=timestamp(),s.recovery_decision=$decision '
        'WITH s MATCH (p:Project {id:$project}) '
        'FOREACH (_ IN CASE WHEN p.struct_index_run_id=$run '
        'AND p.struct_index_status IN ["in_progress","running","cancelling"] THEN [1] ELSE [] END | '
        'SET p.struct_index_status="failed") '
        'CREATE (:ShadowAdjudication {id:$decision,project_id:$project,namespace:$ns, '
        'failed_run:$run,decision:"marked_dead_local_owner",decided_at:timestamp(),snapshot_sha256:$digest})',
        ns=preview['namespace'], owner=owner['owner'], project=owner['project_id'], run=owner['run_id'],
        decision=decision_id, digest=snapshot_digest(preview),
    ).consume()
    return {'decision_id': decision_id, 'status': 'failed', 'data_deleted': False}

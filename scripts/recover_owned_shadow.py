#!/usr/bin/env python3
"""Preview a dead local shadow writer; --apply requires a prior preview digest.

This records terminal ownership without deleting data. Remote, live/reused PIDs,
ambiguous owners and changed evidence are refused. Use normal guarded cleanup later.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from neo4j import GraphDatabase, unit_of_work  # noqa: E402

from graphrag_core.indexing.shadow_admin import snapshot_digest  # noqa: E402
from graphrag_core.indexing.shadow_recovery import recovery_preview, recover_owned_shadow  # noqa: E402


def check_jobs(project: str, owner_pid: int) -> None:
    """Refuse other active/uncertain local workers, including the semantic phase."""
    from _jobs import _process_alive
    for path in (ROOT / '.runtime/jobs').glob('*/state.json'):
        job = json.loads(path.read_text())
        if job.get('project_id') != project or job.get('status') not in {'running', 'cancelling'}:
            continue
        if (job.get('struct_pid') != owner_pid or not job.get('sem_pid')
                or any(_process_alive(job.get(key)) for key in ('struct_pid', 'sem_pid'))):
            raise RuntimeError('Recovery refused: active or uncertain project job')


def main() -> int:
    load_dotenv(ROOT / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-digest')
    args = parser.parse_args()
    if args.apply and not args.expected_digest:
        parser.error('--apply requires --expected-digest from a prior preview')
    decision = 'owned-shadow-' + uuid.uuid4().hex
    from _jobs import claim_project_job_lock, _release_project_job_lock
    claimed = False
    project = args.namespace.partition('::shadow::')[0]
    try:
        if args.apply:
            claimed, _ = claim_project_job_lock(project, decision, project_path='')
            if not claimed:
                raise RuntimeError('Recovery refused: project index lock is held')
        with GraphDatabase.driver(os.environ['LM_PROXY_NEO4J_URI'],
                auth=(os.environ['LM_PROXY_NEO4J_USER'], os.environ['LM_PROXY_NEO4J_PASSWORD'])) as driver:
            with driver.session(database=os.getenv('LM_PROXY_NEO4J_DB', 'proxy')) as session:
                preview = session.execute_read(lambda tx: recovery_preview(tx, args.namespace))
                digest = snapshot_digest(preview)
                check_jobs(project, preview['owner']['pid'])
                result = {'namespace': args.namespace, 'preview_sha256': digest, 'apply': args.apply,
                          'owner': {key: preview['owner'].get(key) for key in
                                    ('project_id', 'run_id', 'host', 'pid', 'status', 'heartbeat_at')},
                          'staged_nodes': len(preview['staging']['nodes']),
                          'staged_relationships': len(preview['staging']['relationships'])}
                if args.apply:
                    if digest != args.expected_digest:
                        raise RuntimeError('Recovery refused: preview digest changed')
                    folder = ROOT / '.runtime/shadow-recovery' / decision
                    folder.mkdir(parents=True, mode=0o700)
                    fd = os.open(folder / 'preview.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'w') as file:
                        json.dump(preview, file, sort_keys=True, default=str)
                        file.flush()
                        os.fsync(file.fileno())

                    @unit_of_work(timeout=30, metadata={'source': 'lm_proxy', 'op': 'owned_shadow_recovery'})
                    def recover(tx):
                        check_jobs(project, preview['owner']['pid'])
                        return recover_owned_shadow(tx, preview, decision)

                    result['result'] = session.execute_write(recover)
                print(json.dumps(result, indent=2))
        return 0
    finally:
        if claimed:
            _release_project_job_lock(project, decision)


if __name__ == '__main__':
    raise SystemExit(main())

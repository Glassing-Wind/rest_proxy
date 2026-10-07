"""Revisioned project annotations; never treated as verified repository evidence."""
from __future__ import annotations

import json
import time


async def project_metadata(driver, project_id: str, *, metadata: dict | None = None,
                           expected_revision: int | None = None, expected_run_id: str = '') -> dict:
    """Read annotations or atomically replace them with publication/revision preconditions."""
    encoded = None
    if metadata is not None:
        if not isinstance(metadata, dict) or len(metadata) > 50:
            raise ValueError('Metadata must be a JSON object with at most 50 top-level keys')
        encoded = json.dumps(metadata, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 16384:
            raise ValueError('Metadata exceeds 16384 UTF-8 bytes')
        if type(expected_revision) is not int or expected_revision < 0 or not expected_run_id:
            raise ValueError('Metadata writes require a nonnegative revision and publication run ID')

    async def operation(tx):
        rows = await (await tx.run('MATCH (p:OutlinePublication {id:$project}) '
                                  'RETURN p.run_id AS run_id, p.root_path AS root_path',
                                  project=project_id)).data()
        if not rows:
            return {'project_id': project_id, 'status': 'not_published'}
        publication = rows[0]
        records = await (await tx.run('MATCH (m:WorkspaceMetadata {id:$project}) '
                                     'RETURN m.revision AS revision, m.root_path AS root_path, '
                                     'm.metadata_json AS metadata_json, m.updated_ms AS updated_ms, '
                                     'm.run_id AS updated_run_id', project=project_id)).data()
        record = records[0] if records else None
        revision = record['revision'] if record else 0
        if encoded is not None:
            if expected_run_id != publication['run_id'] or expected_revision != revision:
                return {'project_id': project_id, 'status': 'conflict', 'revision': revision,
                        'run_id': publication['run_id']}
            revision += 1
            now = time.time_ns() // 1000000
            await tx.run('MERGE (m:WorkspaceMetadata {id:$project}) '
                         'SET m.revision=$revision, m.root_path=$root, m.metadata_json=$metadata, '
                         'm.updated_ms=$updated, m.run_id=$run', project=project_id,
                         revision=revision, root=publication['root_path'], metadata=encoded,
                         updated=now, run=publication['run_id'])
            record = dict(revision=revision, root_path=publication['root_path'], metadata_json=encoded,
                          updated_ms=now, updated_run_id=publication['run_id'])
        matches_root = record is None or record['root_path'] == publication['root_path']
        result = {'project_id': project_id, 'status': 'published' if matches_root else 'workspace_changed',
                  'run_id': publication['run_id'], 'workspace_path': publication['root_path'],
                  'revision': revision, 'metadata': json.loads(record['metadata_json']) if record and matches_root else {},
                  'updated_ms': record['updated_ms'] if record else None,
                  'updated_run_id': record['updated_run_id'] if record else None,
                  'provenance': 'user-or-agent-authored-annotations', 'repository_evidence_verified': False}
        return result

    async with driver.session() as session:
        return await (session.execute_write(operation) if encoded is not None else session.execute_read(operation))

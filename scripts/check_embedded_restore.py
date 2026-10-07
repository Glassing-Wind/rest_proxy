#!/usr/bin/env python3
"""Disposable cold-copy restore drill; native engines and synthetic vectors.

Owners are closed before copying; this is not an online backup API. No model,
operational state or source checkout is changed. Run with embedded dependencies.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from unittest.mock import patch


def tree_hashes(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Drill snapshots do not accept symlinks')
        if path.is_file():
            with path.open('rb') as stream:
                result[str(path.relative_to(root))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


async def check(base: Path) -> dict:
    def deny_network(event, arguments):
        if event == 'socket.connect':
            raise RuntimeError('Restore drill denies Python socket connections')
    sys.addaudithook(deny_network)
    from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner
    from memory.fire_store import FireStore
    live, archive, restored = (base / name for name in ('live', 'archive', 'restored'))
    source = base / 'source'
    source.mkdir()
    original = 'def helper():\n    return 7\n'
    (source / 'helper.py').write_text(original)

    async def embed(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    async with EmbeddedRepositoryOwner(str(live / 'embedded'), 3) as owner:
        publication = await owner.index(str(source), 'restore-fixture', ['helper.py'],
                                        embed=embed, encoder_id='synthetic-restore-fixture')
        expected = await owner.describe_file('restore-fixture', 'helper.py')
        expected_vectors = await owner.search('restore-fixture', encoder_id='synthetic-restore-fixture',
                                             vector=[1.0, 0.0, 0.0], mode='vector')
    fire = FireStore(str(live / 'fire'))

    def checkpoint(task, decision):
        return {'project_id': 'restore-fixture', 'session_id': 's', 'task_id': task,
                'goal': 'Explain helper', 'decisions': [{'text': decision, 'origin': 'source-review'}],
                'evidence': [{'project_id': 'restore-fixture', 'session_id': 's',
                              'source_kind': 'source', 'source_id': 'helper.py'}]}

    fire.save(checkpoint('active', 'Initial'), {'helper.py': original}, 0)
    fire.save(checkpoint('active', 'Returns seven'), {'helper.py': original}, 1,
              correction={'origin': 'source-review', 'reason': 'Read the original'})
    fire.save(checkpoint('deleted', 'Must stay deleted'), {'helper.py': original}, 0)
    fire.delete('restore-fixture', 's', 'deleted', 1)
    with patch('memory.fire_store.time.time', return_value=time.time() - 120):
        fire.save(checkpoint('expired', 'Must stay expired'), {'helper.py': original}, 0,
                  retention_seconds=60)
    active = fire.resume('restore-fixture', 's', 'active')
    before = tree_hashes(live)
    # No active native owner or FIRE connection exists at this checkpoint.
    shutil.copytree(live, archive, copy_function=shutil.copy2)
    assert tree_hashes(archive) == before
    shutil.rmtree(live)
    shutil.rmtree(source)
    shutil.copytree(archive, restored, copy_function=shutil.copy2)
    assert tree_hashes(restored) == before
    async with EmbeddedRepositoryOwner(str(restored / 'embedded'), 3) as owner:
        assert await owner.describe_file('restore-fixture', 'helper.py') == expected
        vectors = await owner.search('restore-fixture', encoder_id='synthetic-restore-fixture',
                                     vector=[1.0, 0.0, 0.0], mode='vector')
        assert vectors == expected_vectors
        assert (await owner.overview('restore-fixture'))['run_id'] == publication['run_id']
    recovered = FireStore(str(restored / 'fire'))
    assert recovered.resume('restore-fixture', 's', 'active') == active
    original_result = recovered.resume('restore-fixture', 's', 'active', source_id='helper.py')
    assert original_result['original'] == original
    assert original_result['revision'] == 2
    deleted = recovered.resume('restore-fixture', 's', 'deleted')
    assert deleted['status'] == 'not_found' and deleted['revision'] == 2
    assert recovered.save(checkpoint('deleted', 'No ABA revival'), {}, 0)['status'] == 'conflict'
    assert recovered.resume('restore-fixture', 's', 'expired')['status'] == 'expired'
    assert recovered.purge_expired('restore-fixture', 's', 'expired')['versions_deleted'] == 1
    assert recovered.resume('other-project', 's', 'active')['status'] == 'not_found'
    return {'cold_copy_hashes_verified': True, 'files_copied': len(before),
            'source_and_live_state_removed': True, 'published_source_and_vectors_equal': True,
            'publication_identity_preserved': True, 'fire_revision_and_original_preserved': True,
            'deletion_tombstone_and_expiry_preserved': True, 'scope_isolation_preserved': True,
            'embedding': 'Synthetic vectors; no quality claims',
            'limitations': ['Cold fixture drill only, not online or coordinated multi-host backup.',
                            'Backups can contain sensitive originals and pre-deletion data.',
                            'Restored evidence remains historical until current-source validation.']}


if __name__ == '__main__':
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='embedded-restore-') as directory:
        result = asyncio.run(check(Path(directory).resolve()))
    result['seconds'] = round(time.monotonic() - started, 3)
    print(json.dumps(result, indent=2))

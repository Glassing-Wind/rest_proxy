"""Disposable SQLite artifact acceptance; no repository/model/service access.

Records are deterministic storage-contract fixtures, not analyzer-quality or
training evidence. Run: .venv/bin/python test_repository_pattern_store.py
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from graphrag_core.repository_learning.pattern_store import RepositoryPatternStore, canonical


def artifact(project='p', value='snake_case', source_id='main.py'):
    digest = hashlib.sha256(b'def authenticate(token):\n    return token\n').hexdigest()
    return {'version': 1, 'project_id': project, 'kind': 'provided-text',
            'sources': [{'project_id': project, 'source_kind': 'provided-text',
                         'source_id': source_id, 'source_sha256': digest}],
            'patterns': [{'key': 'naming', 'kind': 'convention', 'value': value, 'occurrences': 1,
                          'source_id': source_id, 'source_sha256': digest, 'producer': 'fixture-contract-v1'}],
            'backend': {'name': 'fixture'}, 'analysis': {'findings_status': 'analyzer-inference'},
            'context': {'symbols': [{'name': 'authenticate', 'kind': 'Function'}]}}


class RepositoryPatternStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='repository-pattern-store-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.directory = self.root / 'state'
        self.store = RepositoryPatternStore(str(self.directory))

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.store.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def test_reads_and_invalid_mutations_never_create_state(self):
        self.assertEqual(self.store.read('p'), {'status': 'not-learned', 'revision': 0, 'record_id': None})
        self.assertEqual(self.store.history('p')['records'], [])
        self.assertEqual(self.store.delete('p', 1)['status'], 'conflict')
        self.assertFalse(self.directory.exists())
        for record, revision in (({}, 0), (artifact(), True), (artifact(), -1)):
            with self.subTest(record=record, revision=revision), self.assertRaises(ValueError):
                self.store.save('p', record, revision)
        self.assertFalse(self.directory.exists())
        self.directory.mkdir(mode=0o700)
        self.assertEqual(self.store.read('p')['status'], 'not-learned')
        self.assertEqual(self.store.history('p')['records'], [])
        self.assertFalse(self.store.path.exists())

    def test_reopen_detached_historical_generations_and_deduplication(self):
        original = artifact()
        saved = self.store.save('p', original, 0)
        expected = deepcopy(original)
        self.assertEqual(saved['status'], 'stored')
        self.assertEqual(saved['record_id'], hashlib.sha256(canonical(original).encode()).hexdigest())
        original['patterns'][0]['value'] = 'caller mutation'
        reopened = RepositoryPatternStore(str(self.directory))
        self.assertEqual(reopened.read('p')['record'], expected)
        unchanged = reopened.save('p', expected, 1)
        self.assertEqual(unchanged, {'status': 'unchanged', 'revision': 1, 'record_id': saved['record_id']})
        self.assertEqual(reopened.save('p', expected, 0)['status'], 'conflict')
        second = reopened.save('p', artifact(value='CamelCase'), 1)
        self.assertEqual(second['revision'], 2)
        self.assertNotEqual(second['record_id'], saved['record_id'])
        self.assertEqual(reopened.read('p', revision=1)['record'], expected)
        returned = reopened.read('p')['record']
        returned['sources'].clear()
        self.assertEqual(reopened.read('p')['record'], artifact(value='CamelCase'))
        self.assertEqual(len(reopened.history('p')['records']), 2)
        self.assertEqual(reopened.read('other')['status'], 'not-learned')

    def test_two_independent_writers_obey_one_cas(self):
        self.store.save('p', artifact(), 0)
        barrier = threading.Barrier(2)

        def write(value):
            store = RepositoryPatternStore(str(self.directory))
            barrier.wait(timeout=5)
            return store.save('p', artifact(value=value), 1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ('writer-a', 'writer-b')))
        self.assertEqual(sorted(result['status'] for result in results), ['conflict', 'stored'])
        self.assertEqual([entry['revision'] for entry in self.store.history('p')['records']], [1, 2])
        winner = next(result for result in results if result['status'] == 'stored')
        self.assertEqual(self.store.read('p')['record_id'], winner['record_id'])

    def test_delete_retains_tombstone_fence_and_does_not_reset_revisions(self):
        self.store.save('p', artifact(), 0)
        self.assertEqual(self.store.delete('p', 0 + 2)['status'], 'conflict')
        deleted = self.store.delete('p', 1)
        self.assertEqual(deleted, {'status': 'deleted', 'revision': 2, 'record_id': None})
        self.assertEqual(self.store.read('p')['revision'], 2)
        self.assertEqual(self.store.read('p', revision=1)['status'], 'not-learned')
        self.assertEqual(self.store.history('p')['records'], [])
        self.assertEqual(self.store.save('p', artifact(), 0)['status'], 'conflict')
        self.assertEqual(self.store.delete('p', 2)['status'], 'not-learned')
        recreated = self.store.save('p', artifact(), 2)
        self.assertEqual(recreated['revision'], 3)
        self.assertEqual([entry['revision'] for entry in self.store.history('p')['records']], [3])

    def test_source_scope_hash_alias_and_normalization_validation(self):
        invalid = []
        wrong_project = artifact()
        wrong_project['project_id'] = 'other'
        invalid.append(wrong_project)
        foreign_source = artifact()
        foreign_source['sources'][0]['project_id'] = 'other'
        invalid.append(foreign_source)
        wrong_hash = artifact()
        wrong_hash['patterns'][0]['source_sha256'] = '0' * 64
        invalid.append(wrong_hash)
        unknown_source = artifact()
        unknown_source['patterns'][0]['source_id'] = 'alias.py'
        invalid.append(unknown_source)
        duplicate_source = artifact()
        duplicate_source['sources'].append(deepcopy(duplicate_source['sources'][0]))
        invalid.append(duplicate_source)
        duplicate_pattern = artifact()
        duplicate_pattern['patterns'].append(deepcopy(duplicate_pattern['patterns'][0]))
        invalid.append(duplicate_pattern)
        bad_digest = artifact()
        bad_digest['sources'][0]['source_sha256'] = 'A' * 64
        invalid.append(bad_digest)
        for field, value in (('occurrences', True), ('occurrences', 0), ('occurrences', 100001),
                             ('key', 'k' * 257), ('kind', 'k' * 129), ('producer', {})):
            record = artifact()
            record['patterns'][0][field] = value
            invalid.append(record)
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.store.save('p', record, 0)
        self.assertFalse(self.directory.exists())
        # Source labels never become paths: this operation neither reads nor
        # creates the named path, and only stores the supplied artifact hash.
        label = str(self.root / 'never-created-source.py')
        self.store.save('p', artifact(source_id=label), 0)
        self.assertFalse(Path(label).exists())
        self.assertEqual(self.store.read('p')['record']['sources'][0]['source_id'], label)

    def test_nonfinite_and_byte_count_limits_preserve_prior_head(self):
        saved = self.store.save('p', artifact(), 0)
        invalid = []
        for value in (float('nan'), float('inf'), '😀' * 1024):
            invalid.append(artifact(value=value))
        too_large = artifact()
        too_large['analysis']['details'] = 'x' * (512 * 1024)
        invalid.append(too_large)
        too_many_sources = artifact()
        too_many_sources['sources'] = [dict(too_many_sources['sources'][0], source_id=f'{index}.py')
                                       for index in range(129)]
        invalid.append(too_many_sources)
        too_many_patterns = artifact()
        too_many_patterns['patterns'] = [dict(too_many_patterns['patterns'][0], key=str(index))
                                        for index in range(1025)]
        invalid.append(too_many_patterns)
        for record in invalid:
            with self.subTest(size=len(str(record))), self.assertRaises(ValueError):
                self.store.save('p', record, 1)
            self.assertEqual(self.store.read('p')['record_id'], saved['record_id'])
            self.assertEqual(len(self.store.history('p')['records']), 1)

    def test_private_permissions_and_symlink_or_hardlink_aliases_are_refused(self):
        self.store.save('p', artifact(), 0)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        alias = self.root / 'alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            RepositoryPatternStore(str(alias))
        os.chmod(self.directory, 0o755)
        with self.assertRaises(ValueError):
            RepositoryPatternStore(str(self.directory))
        os.chmod(self.directory, 0o700)
        os.chmod(self.store.path, 0o644)
        with self.assertRaises(ValueError):
            self.store.read('p')
        os.chmod(self.store.path, 0o600)
        linked = self.root / 'linked'
        linked.mkdir(mode=0o700)
        os.link(self.store.path, linked / 'learning.sqlite3')
        with self.assertRaises(ValueError):
            RepositoryPatternStore(str(linked)).read('p')
        (linked / 'learning.sqlite3').unlink()
        target = self.root / 'do-not-touch'
        target.write_text('unchanged')
        self.store.path.unlink()
        self.store.path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.store.save('p', artifact(), 0)
        self.assertEqual(target.read_text(), 'unchanged')

    def test_corrupt_payload_scope_and_schema_are_refused(self):
        self.store.save('p', artifact(), 0)
        with self.database() as db:
            payload = json.loads(db.execute('SELECT payload FROM records').fetchone()[0])
            payload['patterns'][0]['value'] = 'mutated stored inference'
            db.execute('UPDATE records SET payload=?', (canonical(payload),))
        for operation in (lambda: self.store.read('p'), lambda: self.store.history('p'),
                          lambda: self.store.save('p', artifact(), 1)):
            with self.assertRaises(ValueError):
                operation()
        with self.database() as db:
            db.execute('DROP TABLE heads')
            db.execute('CREATE TABLE heads (project_id TEXT,revision INTEGER,record_revision INTEGER)')
        with self.assertRaises(ValueError):
            self.store.read('p')

    def test_head_update_failure_rolls_back_inserted_generation(self):
        first = self.store.save('p', artifact(), 0)
        with self.database() as db:
            db.execute("CREATE TRIGGER fail_head BEFORE INSERT ON heads BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(ValueError):
            self.store.save('p', artifact(value='changed'), 1)
        self.assertEqual(self.store.read('p')['record_id'], first['record_id'])
        self.assertEqual(len(self.store.history('p')['records']), 1)

    def test_matching_payload_hash_cannot_alias_another_project(self):
        self.store.save('p', artifact(), 0)
        foreign = artifact('q')
        saved = self.store.save('q', foreign, 0)
        payload = canonical(foreign)
        with self.database() as db:
            db.execute('UPDATE records SET payload=?,record_id=? WHERE project_id=?',
                       (payload, hashlib.sha256(payload.encode()).hexdigest(), 'p'))
        with self.assertRaises(ValueError):
            self.store.read('p')
        with self.assertRaises(ValueError):
            self.store.history('p')
        self.assertEqual(self.store.read('q')['record_id'], saved['record_id'])

    def test_history_is_metadata_only_paged_and_never_silently_evicted(self):
        for revision in range(128):
            self.store.save('p', artifact(value=f'style-{revision}'), revision)
        with self.assertRaises(ValueError):
            self.store.save('p', artifact(value='overflow'), 128)
        self.assertEqual(self.store.read('p')['revision'], 128)
        revisions, after = [], 0
        while True:
            page = self.store.history('p', limit=17, after_revision=after)
            for entry in page['records']:
                self.assertNotIn('record', entry)
                self.assertNotIn('patterns', entry)
                self.assertNotIn('context', entry)
                revisions.append(entry['revision'])
            if page['next_after_revision'] is None:
                break
            after = page['next_after_revision']
        self.assertEqual(revisions, list(range(1, 129)))
        for limit in (0, 51, True):
            with self.assertRaises(ValueError):
                self.store.history('p', limit=limit)

    def test_project_limit_includes_tombstones_to_preserve_aba_fences(self):
        for index in range(128):
            project = f'project-{index}'
            self.store.save(project, artifact(project), 0)
        with self.assertRaises(ValueError):
            self.store.save('new-project', artifact('new-project'), 0)
        self.store.delete('project-0', 1)
        with self.assertRaises(ValueError):
            self.store.save('new-project', artifact('new-project'), 0)
        self.assertEqual(self.store.save('project-0', artifact('project-0'), 2)['revision'], 3)

    @unittest.skipIf(os.name == 'nt', 'POSIX process termination fixture')
    def test_killed_uncommitted_writer_preserves_record_and_head(self):
        first = self.store.save('p', artifact(), 0)
        payload = canonical(artifact(value='uncommitted'))
        code = '''
import hashlib,sqlite3,sys
db=sqlite3.connect(sys.argv[1],isolation_level=None)
db.execute('PRAGMA foreign_keys=ON')
db.execute('BEGIN IMMEDIATE')
payload=sys.argv[2]
db.execute('INSERT INTO records VALUES (?,?,?,?)',('p',2,hashlib.sha256(payload.encode()).hexdigest(),payload))
db.execute('UPDATE heads SET revision=2,record_revision=2 WHERE project_id=?',('p',))
print('uncommitted',flush=True)
sys.stdin.read(1)
'''
        process = subprocess.Popen([sys.executable, '-c', code, str(self.store.path), payload],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            readable, _, _ = select.select([process.stdout], [], [], 5)
            self.assertTrue(readable, 'Disposable SQLite writer did not reach the transaction')
            self.assertEqual(process.stdout.readline().strip(), 'uncommitted')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        reopened = RepositoryPatternStore(str(self.directory))
        self.assertEqual(reopened.read('p')['record_id'], first['record_id'])
        self.assertEqual([entry['revision'] for entry in reopened.history('p')['records']], [1])


if __name__ == '__main__':
    unittest.main()

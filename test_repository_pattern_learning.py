"""Native publication/persistent-learning boundary acceptance over disposable state.

Native declarations, Ladybug, LanceDB and SQLite are real; default analyzer outputs
are typed deterministic fixtures and establish no learning-quality claim. Optional
actual RepoAnalyzer plaintext acceptance uses LM_PROXY_REPOANALYZER_TEST_ROOT.
No production source, databases, services, models or index registrations are touched.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import importlib
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import unittest
from unittest.mock import AsyncMock, patch

import test_repository_learning as native_fixtures
from graphrag_core.repository_learning.pattern_store import RepositoryPatternStore, canonical


REAL_ROOT = os.getenv('LM_PROXY_REPOANALYZER_TEST_ROOT')
REAL_PYTHON = os.getenv('LM_PROXY_REPOANALYZER_TEST_PYTHON')


@dataclass
class FixtureProfile:
    repo_name: str
    metadata: dict
    conventions: dict
    patterns: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)


def profile_echo(payload: dict) -> dict:
    return asdict(FixtureProfile(payload['metadata']['project_id'], deepcopy(payload['metadata']),
                  {'declarations': sorted(native_fixtures.declaration_names(payload))}))


class ProfileAdapter:
    """Typed profile echoes test identities and lock release, not analyzer quality."""

    def __init__(self, runtime, hook=None):
        self.runtime, self.hook, self.calls = runtime, hook, []

    def availability(self):
        return {'status': 'configured'}

    async def analyze(self, operation, *, payload=None, reference_payload=None, target_payload=None):
        if self.runtime._lock.locked() or self.runtime._owner._lock.locked():
            raise AssertionError('Analyzer held publication locks')
        self.calls.append(deepcopy({'operation': operation, 'payload': payload,
                                   'reference_payload': reference_payload, 'target_payload': target_payload}))
        if self.hook:
            return await self.hook(operation, payload, reference_payload, target_payload)
        result = (profile_echo(payload) if operation == 'profile' else
                  {'reference_profile': profile_echo(reference_payload),
                   'target_profile': profile_echo(target_payload), 'recommendations': []})
        return {'ok': True, 'result': result,
                'backend': {'name': 'RepoAnalyzer', 'mode': 'typed-test-fixture'}}


def text_echo(payload: dict) -> dict:
    """A detached source-span fixture solely for testing parent-side validation."""
    text = payload['text']
    end_point = [text.count('\n'), len(text.rsplit('\n', 1)[-1])]
    node = {'type': 'fixture_span', 'kind': 'fixture_span', 'pattern_name': 'fixture-pattern',
            'content': text, 'start_char': 0, 'end_char': len(text),
            'start_byte': 0, 'end_byte': len(text.encode()), 'start_point': [0, 0],
            'end_point': end_point, 'point_units': 'unicode-code-points', 'relationship_templates': {}}
    children = [node] if text else []
    result = {'mode': 'pattern-regex-slice', 'source_id': payload['source_id'],
              'source_sha256': payload['source_sha256'], 'metadata': deepcopy(payload['metadata']),
              'ast': {**node, 'type': 'document', 'kind': 'document', 'children': children},
              'counts': {'nodes': len(children), 'patterns': {'fixture-pattern': 1} if children else {}},
              'limitations': ['deterministic validation fixture']}
    return {'ok': True, 'result': result,
            'backend': {'name': 'RepoAnalyzer', 'mode': 'typed-text-test-fixture'}}


class TextAdapter:
    def __init__(self, mutate=None, failure=None):
        self.mutate, self.failure, self.calls = mutate, failure, []

    async def analyze(self, operation, *, payload=None, **_kwargs):
        self.calls.append(deepcopy(payload))
        if self.failure:
            return {'ok': False, 'error': self.failure}
        response = text_echo(payload)
        if self.mutate:
            self.mutate(response)
        return response


class RepositoryPatternLearningTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the existing native fixtures without inheriting/rerunning their 13 tests.
        native_fixtures.RepositoryLearningTests.setUpClass.__func__(cls)
        cls.patterns = importlib.import_module('graphrag_core.repository_learning.patterns')

    async def asyncSetUp(self):
        await native_fixtures.RepositoryLearningTests.asyncSetUp(self)
        self.state = self.directory / 'private-learning-memory'
        self.env = patch.dict(os.environ, {'LM_PROXY_REPOSITORY_LEARNING_STATE': str(self.state)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = RepositoryPatternStore(str(self.state))
        self.adapter = ProfileAdapter(self.runtime)
        self.gateway = self.service_module.RepositoryLearningGateway(self.runtime, self.adapter)
        self.gateway_patch = patch.object(self.patterns, '_configured_gateway', return_value=self.gateway)
        self.gateway_patch.start()
        self.addCleanup(self.gateway_patch.stop)

    def source_bytes(self) -> dict:
        return {str(path.relative_to(self.directory)): path.read_bytes()
                for project in ('left', 'right') for path in (self.directory / project).rglob('*')
                if path.is_file()}

    def replace_retained_record(self, project: str, original: dict, mutate=None) -> dict:
        """Simulate semantic corruption while preserving the store's outer JSON hash."""
        record = deepcopy(original)
        if mutate:
            mutate(record)
        payload = canonical(record)
        with closing(sqlite3.connect(self.store.path)) as database:
            with database:
                database.execute('UPDATE records SET payload=?,record_id=? WHERE project_id=? AND revision=1',
                                 (payload, hashlib.sha256(payload.encode()).hexdigest(), project))
        # The storage checksum/type contract alone deliberately accepts this shape.
        self.assertEqual(self.store.read(project, 1)['status'], 'stored')
        return record

    @staticmethod
    def resign_snapshot(record: dict) -> None:
        snapshot = record['context']
        unsigned = deepcopy(snapshot['payload'])
        unsigned['metadata'].pop('snapshot_sha256')
        digest = hashlib.sha256(canonical(unsigned).encode()).hexdigest()
        snapshot['payload']['metadata']['snapshot_sha256'] = digest
        snapshot['citation']['snapshot_sha256'] = digest
        record['analysis']['metadata']['snapshot_sha256'] = digest

    async def learn(self, project='left', revision=0, run=None, max_files=20):
        return await self.patterns.learn_repository_patterns(
            project, run or self.publications[project]['run_id'], revision, max_files=max_files)

    async def test_native_observations_cas_deduplication_and_no_source_writes(self):
        before = self.source_bytes()
        saved = await self.learn()
        self.assertEqual(saved['status'], 'stored')
        self.assertEqual(saved['revision'], 1)
        record = self.store.read('left')['record']
        self.assertEqual(record['context']['citation']['run_id'], self.publications['left']['run_id'])
        self.assertEqual(record['findings_status'], 'historical-pattern-observations-and-inference')
        kinds = Counter(symbol['kind'] for source in record['context']['sources'] for symbol in source['symbols'])
        actual = Counter()
        for pattern in record['patterns']:
            if pattern['kind'] == 'declaration_kind':
                actual[pattern['value']] += pattern['occurrences']
        self.assertEqual(actual, kinds)
        self.assertTrue({'Class', 'Function'} <= set(actual))
        self.assertTrue({'Policy', 'authorize', 'authenticate'} <= set(record['analysis']['conventions']['declarations']))
        for source in record['sources']:
            original = self.sources['left'][source['source_id']]
            self.assertEqual(source['source_sha256'], hashlib.sha256(original.encode()).hexdigest())
            self.assertEqual(source['source_kind'], 'published-source')
            self.assertNotIn('original', source)
        unchanged = await self.learn(revision=1)
        self.assertEqual(unchanged['status'], 'unchanged')
        self.assertEqual(unchanged['record_id'], saved['record_id'])
        self.assertEqual((await self.learn(revision=0))['status'], 'conflict')
        self.assertEqual(len(self.store.history('left')['records']), 1)
        read = await self.patterns.get_learned_repository_patterns('left', limit=50)
        languages = [item for item in read['patterns'] if item['kind'] == 'file_language']
        self.assertEqual(len(languages), 1)
        self.assertEqual(languages[0]['occurrences'], 2)
        self.assertEqual(languages[0]['distinct_sources'], 2)
        self.assertEqual(self.source_bytes(), before)

    async def test_failed_worker_and_wrong_publication_do_not_create_memory(self):
        async def fail(*_args):
            return {'ok': False, 'error': 'missing_dependency'}
        self.adapter.hook = fail
        result = await self.learn()
        self.assertEqual(result['status'], 'analyzer-unavailable')
        self.assertFalse(self.state.exists())
        self.adapter.hook = None
        self.assertEqual((await self.learn(run='obsolete-run'))['status'], 'publication-conflict')
        self.assertFalse(self.state.exists())
        self.assertEqual(self.store.read('left')['revision'], 0)

    async def test_reindex_race_fences_commit_without_memory_write(self):
        old_run = self.publications['left']['run_id']

        async def reindex(operation, payload, _reference, _target):
            self.assertEqual(operation, 'profile')
            self.sources['left']['main.py'] = 'def replacement_name():\n    return 3\n'
            (self.directory / 'left' / 'main.py').write_text(self.sources['left']['main.py'])
            self.publications['left'] = await self.owner.index(
                str(self.directory / 'left'), 'left', sorted(self.sources['left']),
                embed=native_fixtures.fixture_embed, encoder_id='fixture-v1')
            return {'ok': True, 'result': profile_echo(payload), 'backend': {'name': 'RepoAnalyzer'}}

        self.adapter.hook = reindex
        result = await self.learn(run=old_run)
        self.assertEqual(result['status'], 'publication-conflict')
        self.assertNotEqual(self.publications['left']['run_id'], old_run)
        self.assertFalse(self.state.exists())

    async def test_partial_selection_retains_explicit_coverage(self):
        result = await self.learn(max_files=1)
        self.assertEqual(result['status'], 'stored')
        self.assertFalse(result['coverage']['complete'])
        record = self.store.read('left')['record']
        self.assertEqual(len(record['sources']), 1)
        self.assertEqual(record['coverage']['omitted_files'], 1)

    async def test_retained_payload_symbol_forgery_cannot_rewrite_citations(self):
        await self.learn()
        original = self.store.read('left')['record']
        for symbol_field, value in [('name', 'forged_symbol_name'), ('signature', 'def forged_signature(other):')]:
            with self.subTest(field=symbol_field):
                def forge(record):
                    record['context']['payload']['files'][0]['symbols'][0][symbol_field] = value
                    self.resign_snapshot(record)

                changed = self.replace_retained_record('left', original, forge)
                self.assertEqual(changed['sources'], original['sources'])
                self.assertEqual(changed['context']['sources'], original['context']['sources'])
                with patch.object(self.patterns, '_configured_gateway',
                                  side_effect=AssertionError('Forged retained context reached analyzer')) as gateway:
                    read = await self.patterns.get_learned_repository_patterns('left', 1)
                    suggestion = await self.patterns.suggest_learned_repository_patterns('left', 'right', 1)
                self.assertNotEqual(read['status'], 'learned')
                self.assertNotIn('patterns', read)
                self.assertNotIn(suggestion['status'], {'ok', 'partial', 'suggestions'})
                gateway.assert_not_called()
                self.replace_retained_record('left', original)

    async def test_retained_profile_scope_and_citations_are_rechecked(self):
        await self.learn()
        original = self.store.read('left')['record']

        def forged_evidence(record):
            path = record['sources'][0]['source_id']
            record['analysis']['patterns'] = [{'evidence': [{'path': path, 'citation': {'project_id': 'foreign'}}]}]

        mutations = [
            lambda record: record['analysis'].update(repo_name='foreign'),
            lambda record: record['analysis']['metadata'].update(project_id='foreign'),
            lambda record: record['backend'].update(name='foreign'),
            forged_evidence,
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.replace_retained_record('left', original, mutate)
                with patch.object(self.patterns, '_configured_gateway',
                                  side_effect=AssertionError('Forged profile opened analyzer')) as gateway:
                    read = await self.patterns.get_learned_repository_patterns('left', 1)
                    suggestion = await self.patterns.suggest_learned_repository_patterns('left', 'right', 1)
                self.assertNotEqual(read['status'], 'learned')
                self.assertNotIn('profile', read)
                self.assertNotIn(suggestion['status'], {'ok', 'partial', 'suggestions'})
                gateway.assert_not_called()
                self.replace_retained_record('left', original)

    async def test_historical_read_and_suggestion_survive_deleted_reference(self):
        saved = await self.learn()
        original = deepcopy(self.store.read('left')['record'])
        self.sources['left']['main.py'] = 'def newer_reference():\n    return 7\n'
        (self.directory / 'left' / 'main.py').write_text(self.sources['left']['main.py'])
        self.publications['left'] = await self.owner.index(
            str(self.directory / 'left'), 'left', sorted(self.sources['left']),
            embed=native_fixtures.fixture_embed, encoder_id='fixture-v1')
        await self.owner.delete_project('left')
        shutil.rmtree(self.directory / 'left')
        before = self.source_bytes()
        capture = self.runtime.capture_learning_snapshots
        with patch.object(self.runtime, 'capture_learning_snapshots', AsyncMock(wraps=capture)) as spy:
            result = await self.patterns.suggest_learned_repository_patterns('left', 'right', 1)
        self.assertEqual(result['status'], 'ok')
        spy.assert_awaited_once_with(['right'], max_files=20)
        self.assertEqual(result['publications'][0], original['context']['citation'])
        self.assertEqual(result['reference_memory']['record_id'], saved['record_id'])
        self.assertEqual(result['reference_memory']['freshness'], 'historical-retained-reference')
        self.assertEqual(self.adapter.calls[-1]['reference_payload'], original['context']['payload'])
        self.assertEqual(self.source_bytes(), before)
        await self.runtime.close()
        with patch.object(self.patterns, '_configured_gateway', side_effect=AssertionError('Read opened analyzer')), \
                patch.object(self.runtime, '_get_owner', AsyncMock(side_effect=AssertionError('Read opened graph'))):
            read = await self.patterns.get_learned_repository_patterns('left', revision=1)
        self.assertEqual(read['status'], 'learned')
        self.assertEqual(read['freshness'], 'historical-not-current-repository')
        self.assertEqual(read['record_id'], saved['record_id'])

    async def test_disabled_state_and_invalid_inputs_do_not_touch_gateway_or_create_files(self):
        with patch.dict(os.environ, {'LM_PROXY_REPOSITORY_LEARNING_STATE': ''}), \
                patch.object(self.patterns, '_configured_gateway', side_effect=AssertionError('Disabled accessed graph')):
            self.assertEqual((await self.learn())['status'], 'disabled')
        for revision in (True, -1, '0'):
            self.assertEqual((await self.learn(revision=revision))['status'], 'invalid-learning-input')
        self.assertFalse(self.state.exists())

    async def test_cancelled_native_commit_drains_before_releasing_publication_locks(self):
        entered, release = threading.Event(), threading.Event()
        save = RepositoryPatternStore.save

        def blocking_save(store, *args):
            entered.set()
            if not release.wait(timeout=3):
                raise AssertionError('Fixture commit release timed out')
            return save(store, *args)

        with patch.object(RepositoryPatternStore, 'save', blocking_save):
            task = asyncio.create_task(self.learn())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.assertTrue(self.runtime._lock.locked())
                self.assertTrue(self.owner._lock.locked())
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertTrue(self.runtime._lock.locked())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                if not task.done():
                    await task
        self.assertFalse(self.runtime._lock.locked())
        self.assertFalse(self.owner._lock.locked())
        self.assertEqual(self.store.read('left')['revision'], 1)

    async def test_provided_text_is_retained_and_source_label_is_never_opened(self):
        adapter = TextAdapter()
        text = 'A café rule\nUse a named interface.'
        label = str(self.directory / 'never-created-document.txt')
        before = self.source_bytes()
        with patch('graphrag_core.repository_learning.adapter.RepoAnalyzerAdapter.from_environment', return_value=adapter):
            saved = await self.patterns.learn_text_patterns('notes', label, text, 0)
            same = await self.patterns.learn_text_patterns('notes', label, text, 1)
        self.assertEqual(saved['status'], 'stored')
        self.assertEqual(same['status'], 'unchanged')
        record = self.store.read('notes')['record']
        self.assertEqual(record['kind'], 'provided-text')
        self.assertEqual(record['sources'][0]['original'], text)
        self.assertEqual(record['sources'][0]['source_sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(record['analysis']['metadata']['source_kind'], 'provided-text')
        self.assertFalse(Path(label).exists())
        self.assertEqual(self.source_bytes(), before)
        def target_pattern(response):
            response['result']['ast']['children'][0]['pattern_name'] = 'target-only'
            response['result']['counts']['patterns'] = {'target-only': 1}

        with patch('graphrag_core.repository_learning.adapter.RepoAnalyzerAdapter.from_environment',
                   return_value=TextAdapter(target_pattern)):
            target = await self.patterns.learn_text_patterns('text-target', 'target.txt', 'Different target', 0)
        self.assertEqual(target['status'], 'stored')
        await self.runtime.close()
        with patch.object(self.patterns, '_configured_gateway', side_effect=AssertionError('Stored text accessed graph')), \
                patch('graphrag_core.repository_learning.adapter.RepoAnalyzerAdapter.from_environment',
                      side_effect=AssertionError('Stored text opened analyzer')):
            result = await self.patterns.suggest_learned_repository_patterns('notes', 'text-target', 1)
        self.assertEqual(result['status'], 'suggestions')
        self.assertEqual(result['freshness'], 'historical-retained-evidence')
        candidate = result['transfer']['candidates'][0]
        self.assertEqual(candidate['value'], 'fixture-pattern')
        self.assertEqual(candidate['reference_occurrences'], 1)
        self.assertEqual(candidate['reference_distinct_sources'], 1)
        self.assertEqual(candidate['reference_evidence'][0]['source_sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(candidate['status'], 'candidate-for-review')
        self.assertNotIn('original', candidate['reference_evidence'][0])

    async def test_forged_text_metadata_hash_and_spans_are_not_persisted(self):
        mutations = [
            lambda value: value['result']['metadata'].update(project_id='foreign'),
            lambda value: value['result'].update(source_sha256='0' * 64),
            lambda value: value['result'].update(source_id='foreign'),
            lambda value: value['result']['ast']['children'][0].update(content='forged'),
            lambda value: value['result']['ast']['children'][0].update(start_char=1),
            lambda value: value['result']['ast']['children'][0].update(end_byte=1),
            lambda value: value['result']['ast']['children'][0].update(start_char=True),
            lambda value: value['result']['ast'].update(start_char=False),
            lambda value: value['result']['ast'].update(start_point=[99, 0]),
            lambda value: value['result']['ast'].update(start_point=[False, 0]),
            lambda value: value['result']['ast']['children'][0].update(end_point=[99, 0]),
            lambda value: value['result']['ast']['children'][0].update(start_point=[False, 0]),
            lambda value: value['result']['ast']['children'][0].update(point_units='utf8-bytes'),
            lambda value: value['result']['counts'].update(nodes=99),
            lambda value: value['result']['counts'].update(nodes=True),
            lambda value: value['result']['counts'].update(patterns={'foreign': 1}),
            lambda value: value['result']['counts'].update(patterns={'fixture-pattern': True}),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                adapter = TextAdapter(mutate)
                with patch('graphrag_core.repository_learning.adapter.RepoAnalyzerAdapter.from_environment',
                           return_value=adapter):
                    result = await self.patterns.learn_text_patterns('notes', 'policy.txt', 'A café rule', 0)
                self.assertNotEqual(result['status'], 'stored')
                self.assertFalse(self.state.exists())

    async def test_retained_text_original_ast_counts_and_normalized_observations_are_rechecked(self):
        with patch('graphrag_core.repository_learning.adapter.RepoAnalyzerAdapter.from_environment',
                   return_value=TextAdapter()):
            self.assertEqual((await self.patterns.learn_text_patterns('notes', 'notes.txt', 'A café rule', 0))['status'],
                             'stored')
            self.assertEqual((await self.patterns.learn_text_patterns('target-notes', 'target.txt', 'Target rule', 0))['status'],
                             'stored')
        original = self.store.read('notes')['record']

        def source_hash(record):
            record['sources'][0]['source_sha256'] = '0' * 64
            for pattern in record['patterns']:
                pattern['source_sha256'] = '0' * 64

        mutations = [
            lambda record: record['sources'][0].update(original='Forged original'),
            source_hash,
            lambda record: record['analysis']['ast']['children'][0].update(content='forged'),
            lambda record: record['analysis']['counts'].update(nodes=99),
            lambda record: record['analysis']['counts'].update(patterns={'fixture-pattern': 2}),
            lambda record: record['patterns'][0].update(occurrences=2),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.replace_retained_record('notes', original, mutate)
                with patch.object(self.patterns, '_configured_gateway',
                                  side_effect=AssertionError('Retained text opened graph')) as gateway:
                    read = await self.patterns.get_learned_repository_patterns('notes', 1)
                    suggestion = await self.patterns.suggest_learned_repository_patterns('notes', 'target-notes', 1)
                self.assertNotEqual(read['status'], 'learned')
                self.assertNotIn('ast', read)
                self.assertNotEqual(suggestion['status'], 'suggestions')
                gateway.assert_not_called()
                self.replace_retained_record('notes', original)

    async def test_text_failure_byte_bound_and_delete_cas_preserve_head(self):
        with patch('graphrag_core.repository_learning.adapter.RepoAnalyzerAdapter.from_environment',
                   return_value=TextAdapter(failure='timeout')):
            self.assertEqual((await self.patterns.learn_text_patterns('notes', 'n.txt', 'rules', 0))['status'],
                             'analyzer-unavailable')
        self.assertFalse(self.state.exists())
        with patch('graphrag_core.repository_learning.adapter.RepoAnalyzerAdapter.from_environment',
                   side_effect=AssertionError('Oversized text touched analyzer')):
            self.assertEqual((await self.patterns.learn_text_patterns('notes', 'n.txt', 'é' * 8193, 0))['status'],
                             'invalid-learning-input')
        await self.learn()
        before = self.source_bytes()
        conflict = await self.patterns.delete_repository_learning('left', 2)
        self.assertEqual(conflict['status'], 'conflict')
        self.assertEqual(self.store.read('left')['revision'], 1)
        deleted = await self.patterns.delete_repository_learning('left', 1)
        self.assertEqual(deleted['status'], 'deleted')
        self.assertEqual(deleted['revision'], 2)
        self.assertEqual((await self.patterns.get_learned_repository_patterns('left', 1))['status'], 'not-learned')
        self.assertEqual((await self.learn(revision=0))['status'], 'conflict')
        self.assertEqual(self.source_bytes(), before)

    @unittest.skipUnless(REAL_ROOT, 'Set LM_PROXY_REPOANALYZER_TEST_ROOT for real plaintext persistence')
    async def test_actual_unicode_and_empty_plaintext_persistence(self):
        from graphrag_core.repository_learning.adapter import RepoAnalyzerAdapter

        adapter = RepoAnalyzerAdapter(REAL_ROOT, REAL_PYTHON)
        text = '= Guide =\n- café rule\nUse `read` and [docs](https://example.test/docs).\n'
        with patch('graphrag_core.repository_learning.adapter.RepoAnalyzerAdapter.from_environment', return_value=adapter):
            saved = await self.patterns.learn_text_patterns('notes', 'provided.txt', text, 0)
            unchanged = await self.patterns.learn_text_patterns('notes', 'provided.txt', text, 1)
            empty = await self.patterns.learn_text_patterns('empty', 'empty.txt', '', 0)
        self.assertEqual(saved['status'], 'stored')
        self.assertEqual(unchanged['status'], 'unchanged')
        self.assertEqual(unchanged['record_id'], saved['record_id'])
        self.assertEqual(empty['status'], 'stored')
        record = self.store.read('notes')['record']
        self.assertEqual(record['sources'][0]['original'], text)
        self.assertEqual(record['sources'][0]['source_kind'], 'provided-text')
        self.assertEqual(record['analysis']['mode'], 'pattern-regex-slice')
        self.assertEqual(record['backend']['parser_backend'], 'custom')
        self.assertFalse(record['backend']['runtime_initialized'])
        self.assertEqual(record['backend']['network_access'], 'denied')
        self.assertEqual(self.store.read('empty')['record']['analysis']['ast']['end_byte'], 0)


if __name__ == '__main__':
    unittest.main()

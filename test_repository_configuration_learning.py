"""Configuration learning acceptance over private disposable SQLite state.

Default cases use labelled typed parser fixtures; they verify evidence retention,
CAS and reuse, not parser accuracy or model learning. Set
LM_PROXY_GITHUB_ANALYZER_TEST_ROOT for the optional actual component-worker case.
No repository acquisition, services, models, source writes or reindexing occur.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ['PYTHON_DOTENV_DISABLED'] = '1'

from graphrag_core.repository_learning import patterns
from graphrag_core.repository_learning.github_configuration import COMPONENT_FILES, GithubConfigurationAdapter
from graphrag_core.repository_learning.pattern_store import RepositoryPatternStore, canonical


REAL_ROOT = os.getenv('LM_PROXY_GITHUB_ANALYZER_TEST_ROOT')
REQUIREMENTS_TEXT = 'requests[security]==2.32.0\n-e example>=1\nplain\n-c constraints.txt\n'
REQUIREMENTS = [
    {'name': 'requests', 'version': '==2.32.0', 'is_editable': False, 'extras': ['security']},
    {'name': 'example', 'version': '>=1', 'is_editable': True, 'extras': []},
    {'name': 'plain', 'version': 'latest', 'is_editable': False, 'extras': []},
]
CONSTRAINTS = [{'name': '-c constraints.txt', 'version': 'latest', 'is_editable': False, 'extras': []}]
IGNORE_TEXT = 'build/\n!keep.txt\n*.pyc\n'
IGNORE_RULES = [
    {'pattern': 'build/', 'negated': False, 'directory_only': True},
    {'pattern': 'keep.txt', 'negated': True, 'directory_only': False},
    {'pattern': '*.pyc', 'negated': False, 'directory_only': False},
]


class FixtureConfigurationAdapter:
    """Typed responses deliberately labelled as fixtures, never actual parsing."""

    def __init__(self, *, observations=None, mutate=None, failure=None):
        self.observations, self.mutate, self.failure = observations, mutate, failure
        self.calls = []

    async def analyze_configuration(self, project_id, source_id, text, parser):
        self.calls.append((project_id, source_id, text, parser))
        if self.failure:
            return {'ok': False, 'error': self.failure}
        observations = self.observations
        if observations is None:
            observations = ({'is_valid': True, 'errors': [], 'raw_data': None,
                             'requirements': REQUIREMENTS, 'constraints': CONSTRAINTS}
                            if parser == 'requirements' else
                            {'is_valid': True, 'errors': [], 'raw_data': None, 'patterns': IGNORE_RULES})
        response = {'ok': True,
                    'result': {'source_id': source_id,
                               'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
                               'parser': parser,
                               'metadata': {'project_id': project_id, 'source_kind': 'provided-text'},
                               'observations': deepcopy(observations),
                               'limitations': ['typed response fixture; not runtime dependency resolution']},
                    'backend': {'name': 'GithubAnalyzer', 'mode': 'typed-configuration-test-fixture'}}
        if self.mutate:
            self.mutate(response)
        return response


class RepositoryConfigurationLearningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='repository-configuration-learning-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.state = self.directory / 'private-learning-state'
        environment = patch.dict(os.environ, {'LM_PROXY_REPOSITORY_LEARNING_STATE': str(self.state)})
        environment.start()
        self.addCleanup(environment.stop)
        graph = patch.object(patterns, '_configured_gateway',
                             side_effect=AssertionError('Configuration accessed native graph owner'))
        graph.start()
        self.addCleanup(graph.stop)
        self.store = RepositoryPatternStore(str(self.state))

    async def learn(self, project='reference', source='provided-requirements', text=REQUIREMENTS_TEXT,
                    parser='requirements', revision=0, adapter=None):
        adapter = adapter or FixtureConfigurationAdapter()
        with patch.object(GithubConfigurationAdapter, 'from_environment', return_value=adapter):
            return await patterns.learn_configuration_patterns(project, source, text, parser, revision)

    def corrupt_retained(self, project, mutate):
        """Rehash corrupt JSON so tests exercise parent evidence checks, not checksum rejection."""
        retained = self.store.read(project)
        record = retained['record']
        mutate(record)
        encoded = canonical(record)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with closing(sqlite3.connect(self.store.path)) as database, database:
            database.execute('UPDATE records SET record_id=?,payload=? WHERE project_id=? AND revision=?',
                             (digest, encoded, project, retained['revision']))

    async def test_requirements_typed_values_original_and_deduplication(self):
        saved = await self.learn()
        self.assertEqual(saved['status'], 'stored')
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(saved['observations'], 4)
        retained = self.store.read('reference')['record']
        self.assertEqual(retained['kind'], 'configuration')
        source = retained['sources'][0]
        self.assertEqual(source['original'], REQUIREMENTS_TEXT)
        self.assertEqual(source['source_sha256'], hashlib.sha256(REQUIREMENTS_TEXT.encode()).hexdigest())
        self.assertEqual(source['source_kind'], 'provided-text')
        self.assertEqual(source['parser_backend'], 'GithubAnalyzer/requirements')
        values = [item['value'] for item in retained['patterns']]
        for observation in REQUIREMENTS + CONSTRAINTS:
            self.assertIn(observation, values)
        same = await self.learn(revision=1)
        self.assertEqual(same['status'], 'unchanged')
        self.assertEqual(same['record_id'], saved['record_id'])
        self.assertEqual(len(self.store.history('reference')['records']), 1)
        with patch.object(GithubConfigurationAdapter, 'from_environment',
                          side_effect=AssertionError('Read opened configuration worker')):
            read = await patterns.get_learned_repository_patterns('reference', revision=1, limit=50)
        self.assertEqual(read['status'], 'learned')
        self.assertEqual(read['freshness'], 'historical-not-current-repository')
        self.assertEqual(read['source_kind'], 'configuration')
        self.assertTrue(read['limitations'])
        self.assertEqual(len(read['patterns']), 4)
        for item in read['patterns']:
            self.assertEqual(item['occurrences'], 1)
            self.assertEqual(item['distinct_sources'], 1)
            self.assertNotIn('original', item['sources'][0])

    async def test_saved_reference_target_suggestion_and_revision_delete_fences(self):
        saved = await self.learn()
        target_observations = {'is_valid': True, 'requirements': [REQUIREMENTS[0]], 'constraints': []}
        target = await self.learn('target', text='requests[security]==2.32.0\n',
                                  adapter=FixtureConfigurationAdapter(observations=target_observations))
        self.assertEqual(target['status'], 'stored')
        with patch.object(GithubConfigurationAdapter, 'from_environment',
                          side_effect=AssertionError('Retained suggestion opened worker')):
            suggested = await patterns.suggest_learned_repository_patterns('reference', 'target', 1)
        self.assertEqual(suggested['status'], 'suggestions')
        self.assertEqual(suggested['reference_revision'], 1)
        self.assertEqual(suggested['target_revision'], target['revision'])
        self.assertEqual(suggested['freshness'], 'historical-retained-evidence')
        candidates = suggested['transfer']['candidates']
        self.assertEqual(len(candidates), 3)
        self.assertNotIn(REQUIREMENTS[0], [item['value'] for item in candidates])
        for item in candidates:
            self.assertEqual(item['status'], 'candidate-for-review')
            self.assertEqual(item['reference_occurrences'], 1)
            self.assertEqual(item['reference_distinct_sources'], 1)
            self.assertEqual(item['reference_evidence'][0]['source_sha256'],
                             hashlib.sha256(REQUIREMENTS_TEXT.encode()).hexdigest())
            self.assertNotIn('original', item['reference_evidence'][0])
        self.assertEqual((await self.learn(revision=0))['status'], 'conflict')
        self.assertEqual((await patterns.delete_repository_learning('reference', 2))['status'], 'conflict')
        self.assertEqual(self.store.read('reference')['record_id'], saved['record_id'])
        deleted = await patterns.delete_repository_learning('reference', 1)
        self.assertEqual(deleted['status'], 'deleted')
        self.assertEqual(deleted['revision'], 2)
        self.assertEqual((await patterns.get_learned_repository_patterns('reference'))['status'], 'not-learned')
        self.assertEqual((await patterns.suggest_learned_repository_patterns('reference', 'target', 1))['status'],
                         'reference-not-learned')
        self.assertEqual((await self.learn(revision=0))['status'], 'conflict')
        recreated = await self.learn(revision=2)
        self.assertEqual(recreated['status'], 'stored')
        self.assertEqual(recreated['revision'], 3)
        self.assertEqual(recreated['record_id'], saved['record_id'])

    async def test_ignore_flags_and_opaque_labels_are_preserved(self):
        label = str(self.directory / 'never-opened.gitignore')
        saved = await self.learn('ignores', source=label, text=IGNORE_TEXT, parser='gitignore')
        self.assertEqual(saved['status'], 'stored')
        self.assertFalse(Path(label).exists())
        record = self.store.read('ignores')['record']
        self.assertEqual(record['sources'][0]['original'], IGNORE_TEXT)
        self.assertEqual(record['sources'][0]['source_sha256'], hashlib.sha256(IGNORE_TEXT.encode()).hexdigest())
        self.assertEqual({canonical(item['value']) for item in record['patterns']},
                         {canonical(item) for item in IGNORE_RULES})
        read = await patterns.get_learned_repository_patterns('ignores', limit=50)
        self.assertEqual(read['status'], 'learned')
        self.assertEqual({canonical(item['value']) for item in read['patterns']},
                         {canonical(item) for item in IGNORE_RULES})

    async def test_occurrences_count_observations_without_claiming_extra_sources(self):
        observations = {'is_valid': True, 'requirements': [REQUIREMENTS[0], REQUIREMENTS[0]], 'constraints': []}
        saved = await self.learn(text='requests[security]==2.32.0\n' * 2,
                                  adapter=FixtureConfigurationAdapter(observations=observations))
        self.assertEqual(saved['status'], 'stored')
        self.assertEqual(saved['patterns'], 1)
        read = await patterns.get_learned_repository_patterns('reference')
        self.assertEqual(read['status'], 'learned')
        self.assertEqual(read['patterns'][0]['occurrences'], 2)
        self.assertEqual(read['patterns'][0]['distinct_sources'], 1)

    async def test_selected_reference_generation_and_target_revision_are_explicit(self):
        first = await self.learn()
        subset = {'is_valid': True, 'requirements': [REQUIREMENTS[0]], 'constraints': []}
        second = await self.learn(text='requests[security]==2.32.0\n', revision=1,
                                  adapter=FixtureConfigurationAdapter(observations=subset))
        self.assertEqual(second['revision'], 2)
        self.assertNotEqual(second['record_id'], first['record_id'])
        await self.learn('target', text='requests[security]==2.32.0\n',
                         adapter=FixtureConfigurationAdapter(observations=subset))
        target_subset = {'is_valid': True, 'requirements': [REQUIREMENTS[0], REQUIREMENTS[2]], 'constraints': []}
        target = await self.learn('target', text='requests[security]==2.32.0\nplain\n', revision=1,
                                  adapter=FixtureConfigurationAdapter(observations=target_subset))
        current = await patterns.get_learned_repository_patterns('reference')
        historical = await patterns.get_learned_repository_patterns('reference', revision=1)
        self.assertEqual(current['revision'], 2)
        self.assertEqual(current['total_patterns'], 1)
        self.assertEqual(historical['record_id'], first['record_id'])
        self.assertEqual(historical['total_patterns'], 4)
        suggested = await patterns.suggest_learned_repository_patterns('reference', 'target', 1)
        self.assertEqual(suggested['status'], 'suggestions')
        self.assertEqual(suggested['reference_revision'], 1)
        self.assertEqual(suggested['target_revision'], target['revision'])
        self.assertEqual(suggested['target_revision'], 2)
        self.assertEqual(suggested['transfer']['total_candidates'], 2)

    async def test_disabled_invalid_and_failed_workers_do_not_create_state(self):
        with patch.dict(os.environ, {'LM_PROXY_REPOSITORY_LEARNING_STATE': ''}), \
                patch.object(GithubConfigurationAdapter, 'from_environment',
                             side_effect=AssertionError('Disabled state opened worker')):
            self.assertEqual((await patterns.learn_configuration_patterns(
                'reference', 's', 'x', 'requirements', 0))['status'], 'disabled')
        self.assertFalse(self.state.exists())
        adapter = FixtureConfigurationAdapter(failure='missing_dependency')
        self.assertEqual((await self.learn(adapter=adapter))['status'], 'analyzer-unavailable')
        for values in ({'revision': True}, {'revision': -1}, {'parser': 'yaml'}, {'text': 'é' * 8193}):
            with self.subTest(values=values):
                invalid = await self.learn(**values)
                self.assertEqual(invalid['status'], 'invalid-learning-input')
        self.assertFalse(self.state.exists())
        self.assertEqual((await patterns.get_learned_repository_patterns('reference'))['status'], 'not-learned')
        self.assertFalse(self.state.exists())

    async def test_malformed_backend_or_typed_observations_are_refused_without_state(self):
        mutations = [
            lambda response: response['backend'].update(name='RepoAnalyzer'),
            lambda response: response['result'].update(source_id='foreign'),
            lambda response: response['result'].update(source_sha256='0' * 64),
            lambda response: response['result'].update(parser='gitignore'),
            lambda response: response['result']['metadata'].update(project_id='foreign'),
            lambda response: response['result']['observations'].update(requirements='untyped'),
            lambda response: response['result']['observations']['requirements'][0].update(name=7),
            lambda response: response['result']['observations']['requirements'][0].update(version=[]),
            lambda response: response['result']['observations']['requirements'][0].update(is_editable='false'),
            lambda response: response['result']['observations']['requirements'][0].update(extras=['ok', 7]),
            lambda response: response['result']['observations'].update(requirements=[REQUIREMENTS[0]] * 257),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                result = await self.learn(adapter=FixtureConfigurationAdapter(mutate=mutate))
                self.assertNotEqual(result['status'], 'stored')
                self.assertFalse(self.state.exists())
        result = await self.learn(adapter=FixtureConfigurationAdapter(observations={
            'is_valid': False, 'requirements': [], 'constraints': []}))
        self.assertEqual(result['status'], 'configuration-invalid')
        self.assertFalse(self.state.exists())
        for field in ('negated', 'directory_only'):
            def invalid_flag(response, field=field):
                response['result']['observations']['patterns'][0][field] = 1

            with self.subTest(flag=field):
                result = await self.learn(text=IGNORE_TEXT, parser='gitignore',
                                          adapter=FixtureConfigurationAdapter(mutate=invalid_flag))
                self.assertNotEqual(result['status'], 'stored')
                self.assertFalse(self.state.exists())

    async def test_hash_consistent_original_and_occurrence_corruption_refuse_read_and_reuse(self):
        mutations = [lambda record: record['sources'][0].update(original='substituted original'),
                     lambda record: record['patterns'][0].update(occurrences=99)]
        await self.learn('target')
        for number, mutate in enumerate(mutations):
            project = f'corrupt-{number}'
            await self.learn(project)
            self.corrupt_retained(project, mutate)
            # SQLite artifact hashing is consistent; typed/source revalidation must still refuse it.
            self.assertEqual(self.store.read(project)['status'], 'stored')
            read = await patterns.get_learned_repository_patterns(project)
            self.assertNotEqual(read['status'], 'learned')
            suggested = await patterns.suggest_learned_repository_patterns(project, 'target', 1)
            self.assertNotEqual(suggested['status'], 'suggestions')
        self.corrupt_retained('target', lambda record: record['patterns'][0].update(occurrences=88))
        await self.learn('valid-reference')
        suggested = await patterns.suggest_learned_repository_patterns('valid-reference', 'target', 1)
        self.assertNotEqual(suggested['status'], 'suggestions')

    @unittest.skipUnless(REAL_ROOT, 'Set LM_PROXY_GITHUB_ANALYZER_TEST_ROOT for genuine component acceptance')
    async def test_actual_components_persist_original_typed_flags_and_reuse_without_worker(self):
        root = Path(REAL_ROOT).resolve()
        backend_before = {relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
                          for relative in COMPONENT_FILES}
        adapter = GithubConfigurationAdapter(root, python=sys.executable)
        with patch.object(GithubConfigurationAdapter, 'from_environment', return_value=adapter):
            saved = await patterns.learn_configuration_patterns('actual', 'requirements.txt',
                                                                 REQUIREMENTS_TEXT, 'requirements', 0)
            same = await patterns.learn_configuration_patterns('actual', 'requirements.txt',
                                                                REQUIREMENTS_TEXT, 'requirements', 1)
            ignore = await patterns.learn_configuration_patterns('actual-ignore', '.gitignore',
                                                                  IGNORE_TEXT, 'gitignore', 0)
            target = await patterns.learn_configuration_patterns('actual-target', 'requirements.txt',
                                                                  'plain\n', 'requirements', 0)
        self.assertEqual(saved['status'], 'stored')
        self.assertEqual(same['status'], 'unchanged')
        self.assertEqual(same['record_id'], saved['record_id'])
        self.assertEqual(ignore['status'], 'stored')
        self.assertEqual(target['status'], 'stored')
        record = self.store.read('actual')['record']
        self.assertEqual(record['sources'][0]['original'], REQUIREMENTS_TEXT)
        self.assertEqual(record['sources'][0]['source_sha256'], hashlib.sha256(REQUIREMENTS_TEXT.encode()).hexdigest())
        actual = record['analysis']['observations']['requirements']
        # The actual parser retains brackets in the name and its extras default; no PEP508 claim.
        self.assertEqual(actual[0], {'name': 'requests[security]', 'version': '==2.32.0',
                                     'is_editable': False, 'extras': []})
        self.assertTrue(actual[1]['is_editable'])
        self.assertEqual(actual[2]['version'], 'latest')
        self.assertEqual(record['analysis']['observations']['constraints'], CONSTRAINTS)
        self.assertEqual(self.store.read('actual-ignore')['record']['analysis']['observations']['patterns'],
                         IGNORE_RULES)
        self.assertEqual(record['backend']['source_hashes'], backend_before)
        self.assertFalse(record['backend']['runtime_initialized'])
        self.assertEqual(record['backend']['network_access'], 'denied')
        self.assertEqual(record['backend']['external_writes'], 'denied')
        with patch.object(GithubConfigurationAdapter, 'from_environment',
                          side_effect=AssertionError('Historical evidence opened actual worker')):
            read = await patterns.get_learned_repository_patterns('actual')
            suggested = await patterns.suggest_learned_repository_patterns('actual', 'actual-target', 1)
        self.assertEqual(read['status'], 'learned')
        self.assertEqual(suggested['status'], 'suggestions')
        self.assertEqual(suggested['transfer']['total_candidates'], 3)
        self.assertEqual({relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
                          for relative in COMPONENT_FILES}, backend_before)


if __name__ == '__main__':
    unittest.main()

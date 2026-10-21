"""Content-free operation telemetry over disposable native and typed fixtures.

Fake SDK providers exercise the real telemetry facade without SDK dependencies or
exporters. Ladybug, LanceDB and ts-pack are real; analyzer responses and embedding
vectors are fixtures, so these checks make no model or learning-quality claim.
Run: .venv/bin/python test_telemetry_boundaries.py
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from graphrag_core import telemetry


class FakeProvider:
    def shutdown(self, **_kwargs):
        pass


class FakeInstrument:
    def __init__(self):
        self.values = []

    def record(self, value, attributes):
        self.values.append((value, dict(attributes)))

    add = record


class FakeSpan:
    def __init__(self, tracer, name, parent, attributes):
        self.tracer = tracer
        self.name, self.parent = name, parent
        self.attributes = dict(attributes)
        self.status = None

    def set_attribute(self, name, value):
        self.attributes[name] = value

    def set_status(self, value):
        self.status = value

    def end(self):
        self.tracer.finished.append(self)


class FakeTracer:
    def __init__(self):
        self.finished = []

    def start_span(self, name, *, context, attributes,
                   record_exception, set_status_on_exception):
        if record_exception or set_status_on_exception:
            raise AssertionError('SDK exception capture enabled')
        return FakeSpan(self, name, context, attributes)


class TextAdapter:
    def __init__(self, failure=False):
        self.failure = failure

    async def analyze(self, _operation, *, payload):
        if self.failure:
            return {'ok': False, 'error': 'secret-backend-failure'}
        text = payload['text']
        point = [text.count('\n'), len(text.rsplit('\n', 1)[-1])]
        node = {'type': 'fixture', 'kind': 'fixture', 'pattern_name': 'fixture', 'content': text,
                'start_char': 0, 'end_char': len(text), 'start_byte': 0, 'end_byte': len(text.encode()),
                'start_point': [0, 0], 'end_point': point, 'point_units': 'unicode-code-points',
                'relationship_templates': {}}
        return {'ok': True, 'backend': {'name': 'RepoAnalyzer'}, 'result': {
            'mode': 'pattern-regex-slice', 'source_id': payload['source_id'],
            'source_sha256': payload['source_sha256'], 'metadata': deepcopy(payload['metadata']),
            'ast': {**node, 'type': 'document', 'kind': 'document', 'children': [node]},
            'counts': {'nodes': 1, 'patterns': {'fixture': 1}}, 'limitations': ['typed fixture']}}


class ConfigurationAdapter:
    async def analyze_configuration(self, project_id, source_id, text, parser):
        import hashlib
        return {'ok': True, 'backend': {'name': 'GithubAnalyzer'}, 'result': {
            'source_id': source_id, 'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'parser': parser, 'metadata': {'project_id': project_id, 'source_kind': 'provided-text'},
            'observations': {'is_valid': True, 'errors': [], 'raw_data': {}, 'constraints': [],
                             'requirements': [{'name': 'private-package', 'version': '==1',
                                               'is_editable': False, 'extras': []}]},
            'limitations': ['typed fixture']}}


class TelemetryBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory(prefix='telemetry-boundaries-'))
        self.root = Path(directory).resolve()
        self.stack.enter_context(patch.dict(os.environ, {
            'PYTHON_DOTENV_DISABLED': '1', 'LM_PROXY_TELEMETRY_ENABLED': '1',
            'LM_PROXY_OTEL_ENDPOINT': '', 'LM_PROXY_STORAGE_BACKEND': 'embedded',
            'LM_PROXY_GRAPH_BACKEND': 'ladybug', 'LM_PROXY_MEMORY_ENABLED': '0',
            'LM_PROXY_MEMORY_ENABLE_PERSISTENCE': '0', 'LM_PROXY_MEMORY_ENABLE_REDIS': '0',
            'LM_PROXY_WATCHER_ENABLED': '0', 'LM_PROXY_EMBEDDED_WATCH_ENABLED': '0',
            'LM_PROXY_CONFIG_DIR': str(self.root / 'config'),
            'LM_PROXY_RUNTIME_DIR': str(self.root / 'runtime'),
            'LM_PROXY_REPOSITORY_LEARNING_STATE': str(self.root / 'learning'),
        }, clear=True))
        self.tracer = FakeTracer()
        self.duration, self.counter = FakeInstrument(), FakeInstrument()
        self.engine = telemetry._Engine(
            FakeProvider(), FakeProvider(), self.tracer, self.duration, self.counter, None,
            lambda: None, lambda span, _parent: span, 'fixed-error', 'local')
        self.stack.enter_context(patch.object(telemetry, '_engine', None))
        self.stack.enter_context(patch.object(telemetry, '_build_engine', return_value=self.engine))
        self.handle = telemetry.start('mcp')
        self.assertTrue(self.handle.enabled)
        self.addAsyncCleanup(telemetry.stop, self.handle)

    def spans(self, name):
        return [span for span in self.tracer.finished if span.name == name]

    def assert_private(self, *secrets):
        keys = {'operation.category', 'operation.name', 'operation.backend',
                'operation.surface', 'operation.outcome'}
        for span in self.tracer.finished:
            self.assertEqual(set(span.attributes), keys)
            self.assertIn(span.attributes['operation.outcome'], telemetry._OUTCOMES)
        for value, attributes in self.duration.values:
            self.assertGreaterEqual(value, 0)
            self.assertEqual(set(attributes), keys)
        for value, attributes in self.counter.values:
            self.assertEqual(value, 1)
            self.assertEqual(set(attributes), keys)
        exported = repr([(span.name, span.attributes, span.status) for span in self.tracer.finished]
                        + self.duration.values + self.counter.values)
        for secret in (*secrets, str(self.root)):
            self.assertNotIn(secret, exported)

    async def new_owner(self):
        from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner
        owner = await EmbeddedRepositoryOwner(str(self.root / 'state'), 3).__aenter__()
        self.addAsyncCleanup(owner.__aexit__)
        return owner

    async def test_native_ast_facts_publication_retrieval_and_thread_parentage(self):
        from graphrag_core.neo4j import execute_read
        from graphrag_core.repository_learning import patterns
        from graphrag_core.repository_learning.service import RepositoryLearningGateway
        owner = await self.new_owner()
        source = self.root / 'private-source'
        source.mkdir()
        path = source / 'private_module.py'
        path.write_text('def private_symbol(secret_argument):\n    return secret_argument\n')

        async def embed(_texts):
            return [[1., 0., 0.]]

        publication = await owner.index(str(source), 'private-project', [path.name],
                                        embed=embed, encoder_id='private-encoder')
        index = self.spans('repository.index')[-1]
        for name in ('repository.parse', 'repository.facts', 'repository.chunks',
                     'repository.embed', 'vector.stage', 'repository.publish'):
            self.assertIs(self.spans(name)[-1].parent, index, name)
        publish = self.spans('repository.publish')[-1]
        self.assertTrue(any(span.parent is publish for span in self.spans('graph.execute_write')))
        for mode in ('text', 'vector', 'hybrid'):
            rows = await owner.search('private-project', encoder_id='private-encoder',
                                       text='private_symbol', vector=[1., 0., 0.], mode=mode)
            self.assertEqual(rows[0]['run_id'], publication['run_id'])
            search = self.spans('repository.search')[-1]
            self.assertIs(self.spans('vector.search_' + mode)[-1].parent, search)
        snapshots = await owner.learning_snapshots(['private-project'])
        self.assertEqual(snapshots[0]['citation']['run_id'], publication['run_id'])
        self.assertEqual(self.spans('repository.learning_snapshot')[-1].attributes['operation.backend'], 'ladybug')
        self.assertEqual(await owner.search('private-project', encoder_id='private-encoder',
                                            text='zqxwunfindable', mode='text'), [])
        self.assertEqual(self.spans('vector.search_text')[-1].attributes['operation.outcome'], 'empty')
        self.assertEqual(self.spans('repository.search')[-1].attributes['operation.outcome'], 'empty')

        class Runtime:
            async def capture_learning_snapshots(self, projects, **bounds):
                return await owner.learning_snapshots(projects, **bounds)

            async def commit_learning_record(self, *args):
                return await owner.commit_learning_record(*args)

        class Adapter:
            async def analyze(self, _operation, *, payload):
                if owner._lock.locked():
                    raise AssertionError('Analysis retained the publication lock')
                return {'ok': True, 'backend': {'name': 'RepoAnalyzer'}, 'result': {
                    'repo_name': payload['repo_name'], 'metadata': deepcopy(payload['metadata']), 'patterns': []}}

        gateway = RepositoryLearningGateway(Runtime(), Adapter())
        with patch.object(patterns, '_configured_gateway', return_value=gateway):
            learned = await patterns.learn_repository_patterns('private-project', publication['run_id'], 0)
            self.assertEqual(learned['status'], 'stored')
            learning = self.spans('learning.repository')[-1]
            self.assertIs(self.spans('analyzer.profile')[-1].parent, learning)
            self.assertIs(self.spans('learning.save')[-1].parent, learning)
            conflict = await patterns.learn_repository_patterns('private-project', 'private-old-run', 1)
            self.assertEqual(conflict['status'], 'publication-conflict')
            self.assertEqual(self.spans('learning.repository')[-1].attributes['operation.outcome'], 'conflict')
        # The common Neo4j helper also accepts embedded sessions: one actual
        # managed transaction must remain one Ladybug span.
        before = len(self.spans('graph.execute_read'))
        async with owner.graph.session() as session:
            await execute_read(session, 'MATCH (n:File) RETURN n.path AS path', op='private-op')
        reads = self.spans('graph.execute_read')[before:]
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0].attributes['operation.backend'], 'ladybug')
        self.assert_private('private-project', 'private-encoder', 'private_symbol', 'private_module.py',
                            'secret_argument', 'private-op', publication['run_id'],
                            snapshots[0]['citation']['manifest_sha256'], learned['record_id'], 'private-old-run')

    async def test_embedding_cancellation_preserves_journal_and_outcome(self):
        owner = await self.new_owner()
        source = self.root / 'source'
        source.mkdir()
        (source / 'a.py').write_text('def private_cancel():\n    return 1\n')
        entered = asyncio.Event()

        async def embed(_texts):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(owner.index(str(source), 'private-project', ['a.py'],
                                              embed=embed, encoder_id='private-encoder'))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.spans('repository.embed')[-1].attributes['operation.outcome'], 'cancelled')
        self.assertEqual(self.spans('repository.index')[-1].attributes['operation.outcome'], 'cancelled')
        self.assertIsNone(await owner.overview('private-project'))
        attempt = await owner.indexing_attempt('private-project')
        self.assertEqual(attempt['status'], 'cancelled')
        self.assert_private('private-project', 'private-encoder', 'private_cancel')

    async def test_managed_neo4j_retry_consume_error_and_no_query_metadata(self):
        from graphrag_core.neo4j import execute_read, execute_write
        secret = 'SECRET-CREDENTIAL-QUERY-RESULT'

        class Result:
            async def data(self):
                return [{'private': secret}]

            async def consume(self):
                return None

        class Session:
            async def run(self, _query, **_params):
                return Result()

            async def execute_read(self, callback):
                await callback(self)
                return await callback(self)

            async def execute_write(self, callback):
                await callback(self)

        session = Session()
        rows = await execute_read(session, secret, op=secret, op_prefix=secret,
                                  base_metadata={'token': secret}, private=secret)
        self.assertEqual(rows, [{'private': secret}])
        self.assertEqual(len(self.spans('graph.execute_read')), 1)
        await execute_write(session, secret, op=secret, private=secret)
        failure = RuntimeError(secret)
        with patch.object(session, 'run', side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                await execute_read(session, secret)
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.spans('graph.execute_read')[-1].attributes['operation.outcome'], 'error')
        self.assertEqual(self.spans('graph.execute_read')[-1].status, 'fixed-error')
        self.assert_private(secret)

    async def test_learning_analyzer_persistence_history_conflicts_and_absence(self):
        from graphrag_core.repository_learning import patterns
        from graphrag_core.repository_learning.adapter import RepoAnalyzerAdapter
        from graphrag_core.repository_learning.github_configuration import GithubConfigurationAdapter
        text, project, label = 'private text café\n', 'private-project', 'private-label'
        with patch.object(RepoAnalyzerAdapter, 'from_environment', return_value=TextAdapter()):
            result = await patterns.learn_text_patterns(project, label, text, 0)
            self.assertEqual(result['status'], 'stored')
            learning = self.spans('learning.text')[-1]
            self.assertIs(self.spans('analyzer.plaintext_ast')[-1].parent, learning)
            self.assertIs(self.spans('learning.save')[-1].parent, learning)
            self.assertEqual((await patterns.learn_text_patterns(project, label, text, 1))['status'], 'unchanged')
            self.assertEqual((await patterns.learn_text_patterns(project, label, text, 0))['status'], 'conflict')
            self.assertEqual(self.spans('learning.save')[-1].attributes['operation.outcome'], 'conflict')
            self.assertEqual(self.spans('learning.text')[-1].attributes['operation.outcome'], 'conflict')
        retained = await patterns.get_learned_repository_patterns(project)
        self.assertEqual(retained['status'], 'learned')
        self.assertIs(self.spans('learning.memory_read')[-1].parent, self.spans('learning.read')[-1])
        self.assertEqual((await patterns.delete_repository_learning(project, 2))['status'], 'conflict')
        self.assertEqual(self.spans('learning.memory_delete')[-1].attributes['operation.outcome'], 'conflict')
        self.assertEqual((await patterns.delete_repository_learning(project, 1))['status'], 'deleted')
        with patch.object(RepoAnalyzerAdapter, 'from_environment', return_value=TextAdapter(failure=True)):
            self.assertEqual((await patterns.learn_text_patterns(project, label, text, 2))['status'], 'analyzer-unavailable')
        self.assertEqual(self.spans('analyzer.plaintext_ast')[-1].attributes['operation.outcome'], 'unavailable')
        self.assertEqual(self.spans('learning.text')[-1].attributes['operation.outcome'], 'unavailable')
        with patch.object(GithubConfigurationAdapter, 'from_environment', return_value=ConfigurationAdapter()):
            config = await patterns.learn_configuration_patterns('private-config', label, 'private-package==1',
                                                                  'requirements', 0)
        self.assertEqual(config['status'], 'stored')
        self.assertIs(self.spans('analyzer.configuration')[-1].parent, self.spans('learning.configuration')[-1])
        self.assert_private(text, project, label, retained['record_id'], 'private-package',
                            'private-config', 'secret-backend-failure')

    async def test_analyzer_profile_failure_and_partial_are_fixed_outcomes(self):
        from graphrag_core.repository_learning.service import RepositoryLearningGateway
        snapshot = {'payload': {'metadata': {'project_id': 'private-project', 'run_id': 'private-run',
                    'manifest_sha256': 'private-manifest', 'snapshot_sha256': 'private-snapshot',
                    'coverage': {'complete': False}}}, 'coverage': {'complete': False},
                    'citation': {'project_id': 'private-project', 'run_id': 'private-run',
                                 'manifest_sha256': 'private-manifest', 'snapshot_sha256': 'private-snapshot'},
                    'sources': []}

        class Adapter:
            async def analyze(self, _operation, *, payload):
                return {'ok': True, 'backend': {'name': 'RepoAnalyzer'},
                        'result': {'repo_name': payload['metadata']['project_id'], 'metadata': payload['metadata']}}

        gateway = RepositoryLearningGateway(None, Adapter())
        result = await gateway.analyze_captured('profile', [snapshot])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(self.spans('analyzer.profile')[-1].attributes['operation.outcome'], 'partial')
        with patch.object(gateway.adapter, 'analyze', return_value={'ok': False, 'error': 'secret-error'}):
            self.assertEqual((await gateway.analyze_captured('profile', [snapshot]))['status'], 'analyzer-unavailable')
        self.assertEqual(self.spans('analyzer.profile')[-1].attributes['operation.outcome'], 'unavailable')
        self.assert_private('private-project', 'private-run', 'private-manifest', 'private-snapshot', 'secret-error')

    async def test_cancelled_memory_write_drains_before_propagating(self):
        from graphrag_core.repository_learning import patterns
        from graphrag_core.repository_learning.adapter import RepoAnalyzerAdapter
        from graphrag_core.repository_learning.pattern_store import RepositoryPatternStore
        store = RepositoryPatternStore(str(self.root / 'learning'))
        save = store.save
        entered, release = threading.Event(), threading.Event()

        def held_save(*args):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError('Cancellation fixture was not released')
            return save(*args)

        with patch.object(patterns, '_store', return_value=store), \
                patch.object(store, 'save', side_effect=held_save), \
                patch.object(RepoAnalyzerAdapter, 'from_environment', return_value=TextAdapter()):
            task = asyncio.create_task(patterns.learn_text_patterns(
                'private-project', 'private-source', 'private text', 0))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done(), 'Native save was abandoned on cancellation')
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(store.read('private-project')['status'], 'stored')
        self.assertEqual(self.spans('learning.save')[-1].attributes['operation.outcome'], 'cancelled')
        self.assertEqual(self.spans('learning.text')[-1].attributes['operation.outcome'], 'cancelled')
        self.assert_private('private-project', 'private-source', 'private text')

    async def test_modified_boundaries_import_and_fail_open_without_sdk(self):
        script = """
import builtins, asyncio, os
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.startswith('opentelemetry'):
        raise ImportError('secret optional SDK failure')
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
from graphrag_core import telemetry
from graphrag_core.neo4j import execute_read
from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner
from graphrag_core.repository_learning.patterns import get_learned_repository_patterns
from memory.embedded_lance_runs import LanceRunStore
assert not telemetry.start('mcp').enabled
assert asyncio.run(get_learned_repository_patterns('private-project'))['status'] == 'disabled'
"""
        environment = dict(os.environ)
        environment.pop('LM_PROXY_REPOSITORY_LEARNING_STATE', None)
        result = await asyncio.to_thread(subprocess.run, [os.sys.executable, '-c', script],
                                        capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('secret optional SDK failure', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()

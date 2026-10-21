"""Offline embedded common-search contracts; no native engines, models or services."""
import hashlib
import json
import unittest
from unittest import mock

from memory.embedded_runtime import EmbeddedRuntime
from memory.embedded_search import OUTPUT_BYTES, prepare_search
from test_search_codebase_tool import FakeMCP, FakeMemoryStore, load_module


def publication():
    return {'run_id': 'run', 'manifest_sha256': 'manifest',
            'retrieval': {'encoder_id': 'fixture'}}


def row(project='p', file='main.py', content='  authenticate\n', ref='ref', **metadata):
    return {'project_id': project, 'run_id': 'run', 'ref_id': ref, 'file_path': file,
            'content': content, 'source_sha256': 'a' * 64,
            'metadata': {'content_sha256': hashlib.sha256(content.encode()).hexdigest(),
                         'language': 'python', **metadata}}


class EmbeddedSearchTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        runtime = EmbeddedRuntime('/unused', 3)
        owner = mock.AsyncMock()
        owner.resolve_project.side_effect = lambda value: (
            None if value == 'missing' else {'project_id': 'p' if value == 'alias' else value,
                'run_id': 'run', 'workspace_path': '/source/' + value, 'matched_by': 'id'})
        owner.overview.side_effect = lambda project: publication()
        owner.search.side_effect = lambda project, **options: [row(project)]
        runtime._owner = owner
        return runtime, owner

    async def test_text_exact_chunk_hash_and_aliases_without_encoder(self):
        runtime, owner = self.runtime()
        with mock.patch.object(runtime, '_get_encoder', side_effect=AssertionError('model')):
            result = await runtime.workspace_search(['p', 'alias', 'missing'], 'authenticate', mode='text')
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['content'], '  authenticate\n')
        self.assertEqual(result['results'][0]['origin'], 'stored_index_chunk')
        self.assertEqual(result['results'][0]['content_sha256'],
                         hashlib.sha256(b'  authenticate\n').hexdigest())
        self.assertEqual(result['projects'][-1]['status'], 'not_published')
        owner.search.assert_awaited_once()
        self.assertFalse(result['coverage_complete'])
        self.assertFalse(result['legacy_policy_parity'])

    async def test_precise_alias_hybrid_embeds_once_and_refuses_encoder_mismatch(self):
        runtime, owner = self.runtime()
        encoder = mock.AsyncMock()
        encoder.encoder_id = 'fixture'
        encoder.embed_texts.return_value = [[1., 0., 0.]]
        with mock.patch.object(runtime, '_get_encoder', return_value=encoder):
            result = await runtime.workspace_search(['p', 'q'], 'authenticate')
            self.assertEqual(result['requested_mode'], 'precise')
            self.assertEqual(result['mode'], 'hybrid')
            encoder.embed_texts.assert_awaited_once_with(['authenticate'])
            encoder.encoder_id = 'wrong'
            with self.assertRaisesRegex(ValueError, 'encoder must match'):
                await runtime.workspace_search(['p'], 'authenticate', mode='vector')
        self.assertEqual(owner.search.await_count, 2)

    async def test_filters_and_caps_are_project_scoped(self):
        runtime, owner = self.runtime()
        def results(project, **options):
            return [row(project, file='contest.py'), row(project, file='src/main.py', ref='one',
                        file_imports=['dep']), row(project, file='src/main.py', ref='two', file_imports=['dep']),
                    row(project, file='src/other.py', ref='three', file_imports=['dep']),
                    row(project, file='skip.py', ref='four', language='javascript')]
        owner.search.side_effect = results
        result = await runtime.workspace_search(['p', 'q'], 'authenticate', mode='text', k=10,
            languages=['python'], min_imports=1, include_paths=['src/*'], max_per_dir=1)
        self.assertEqual([hit['project_id'] for hit in result['results']], ['p', 'q'])
        self.assertTrue(all(hit['file_path'] == 'src/main.py' for hit in result['results']))
        default = await runtime.workspace_search(['p'], 'authenticate', mode='text', k=10)
        self.assertNotIn('contest.py', [hit['file_path'] for hit in default['results']])
        required = await runtime.workspace_search(['p'], 'authenticate', mode='text', require_context=True)
        self.assertEqual(required['results'], [])

    async def test_run_changes_and_scope_refusal(self):
        runtime, owner = self.runtime()
        owner.resolve_project.side_effect = None
        owner.resolve_project.return_value = {'project_id': 'p', 'run_id': 'old', 'workspace_path': '/old'}
        with self.assertRaisesRegex(RuntimeError, 'Publication changed'):
            await runtime.workspace_search(['p'], 'authenticate', mode='text')
        owner.search.assert_not_awaited()
        owner.resolve_project.return_value['run_id'] = 'run'
        owner.search.return_value = [row('foreign')]
        owner.search.side_effect = None
        with self.assertRaisesRegex(RuntimeError, 'escaped'):
            await runtime.workspace_search(['p'], 'authenticate', mode='text')
        owner.search.return_value = [row()]
        owner.overview.side_effect = [publication(), dict(publication(), run_id='new')]
        with self.assertRaisesRegex(RuntimeError, 'Publication changed'):
            await runtime.workspace_search(['p'], 'authenticate', mode='text')
        owner.resolve_project.side_effect = ValueError('Ambiguous project name')
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            await runtime.workspace_search(['p'], 'authenticate', mode='text')

    async def test_output_budget_preserves_whole_chunks_and_candidate_limits(self):
        runtime, owner = self.runtime()
        owner.search.side_effect = None
        owner.search.return_value = [row(file=f'src/{n}.py', content='é' * 8000, ref=str(n)) for n in range(100)]
        result = await runtime.workspace_search(['p'], 'authenticate', mode='text', k=100, max_per_dir=0)
        self.assertTrue(result['output_truncated'])
        self.assertTrue(result['projects'][0]['candidate_limit_reached'])
        self.assertGreater(len(result['results']), 0)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()), OUTPUT_BYTES)
        for hit in result['results']:
            self.assertEqual(hit['content'], 'é' * 8000)
            self.assertEqual(hashlib.sha256(hit['content'].encode()).hexdigest(), hit['content_sha256'])

    async def test_validation_and_unsupported_before_owner(self):
        runtime = EmbeddedRuntime('/unused', 3)
        with mock.patch.object(runtime, '_get_owner', side_effect=AssertionError('owner')):
            for options in [{'mode': 'broad'}, {'fallback': 'grep'}, {'include_debug': True},
                            {'crate_contains': 'crate'}, {'meta_boost': 0.2}]:
                result = await runtime.workspace_search(['p'], 'q', **options)
                self.assertEqual(result['status'], 'unsupported-options')
            for works, query, options in [('p', 'q', {}), ([], 'q', {}), (['p'] * 9, 'q', {}),
                    ([None], 'q', {}), (['p'], ' ', {}), (['p'], 'é' * 4097, {}),
                    (['p'], 'q', {'k': True}), (['p'], 'q', {'max_per_dir': -1}),
                    (['p'], 'q', {'include_paths': 'src/*'})]:
                with self.assertRaises(ValueError):
                    await runtime.workspace_search(works, query, **options)

    async def test_common_tool_dispatch_and_malformed_list_before_legacy(self):
        module = load_module(FakeMemoryStore({}))
        mcp = FakeMCP()
        module.register(mcp)
        runtime, owner = self.runtime()
        with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded'}), \
                mock.patch('memory.embedded_runtime.get_embedded_runtime', return_value=runtime), \
                mock.patch.object(module, 'get_memory_modules', side_effect=AssertionError('legacy')), \
                mock.patch.object(runtime, '_get_encoder', side_effect=AssertionError('encoder')):
            tool = mcp.tools['search_codebase']
            result = json.loads(await tool(workspace_id='p', query='authenticate', mode='text'))
            self.assertEqual(result['backend'], 'embedded')
            self.assertEqual(result['results'][0]['project_id'], 'p')
            before = owner.search.await_count
            malformed = await tool(workspace_ids='p', query='q', mode='text')
            self.assertIn('workspace_ids must be a list', malformed)
            self.assertEqual(owner.search.await_count, before)
        self.assertEqual(prepare_search(['p'], 'q', meta_boost=0)['mode'], 'hybrid')


if __name__ == '__main__':
    unittest.main()

"""Optional native publication → real RepoAnalyzer → MCP/REST acceptance.

Set LM_PROXY_REPOANALYZER_TEST_ROOT to an existing trusted checkout and optionally
LM_PROXY_REPOANALYZER_TEST_PYTHON to its compatible interpreter. Uses disposable
Ladybug/LanceDB state, real ts-pack parsing, synthetic vectors and no live models.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.applications import Starlette
from starlette.routing import Route

from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner
from memory.embedded_runtime import EmbeddedRuntime
from tools.brain import repository_learning
from tools.brain.embedded_rest import make_read_endpoint, make_write_endpoint


async def fixture_embed(texts):
    return [[1.0, 0.0, 0.0] for _ in texts]


@unittest.skipUnless(os.getenv('LM_PROXY_REPOANALYZER_TEST_ROOT'), 'Explicit real RepoAnalyzer checkout required')
class ActualLearningIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_real_learning_mutations_and_retained_evidence_through_both_transports(self):
        with tempfile.TemporaryDirectory(prefix='native-learning-mutations-') as directory:
            base = Path(directory).resolve()
            runtime = EmbeddedRuntime(str(base / 'state'), 3)
            async with EmbeddedRepositoryOwner(runtime.state, 3) as owner:
                runtime._owner = owner
                receipts = {}
                for project in ('reference', 'target'):
                    source = base / project
                    source.mkdir()
                    (source / 'main.py').write_text('class Policy:\n    def check(self):\n        return True\n')
                    receipts[project] = await owner.index(str(source), project, ['main.py'],
                                                        embed=fixture_embed, encoder_id='synthetic-fixture')
                    (source / 'main.py').unlink()
                environment = {
                    'PYTHON_DOTENV_DISABLED': '1', 'LM_PROXY_STORAGE_BACKEND': 'embedded',
                    'LM_PROXY_GRAPH_BACKEND': 'ladybug', 'LM_PROXY_EMBEDDED_STATE': runtime.state,
                    'LM_PROXY_REPOSITORY_LEARNING_STATE': str(base / 'learning'),
                    'LM_PROXY_REPOANALYZER_ROOT': os.environ['LM_PROXY_REPOANALYZER_TEST_ROOT'],
                    'LM_PROXY_REPOANALYZER_PYTHON': os.getenv('LM_PROXY_REPOANALYZER_TEST_PYTHON', ''),
                }
                with mock.patch.dict(os.environ, environment), mock.patch(
                    'memory.embedded_runtime.get_embedded_runtime', return_value=runtime,
                ), mock.patch.object(runtime, '_get_encoder', side_effect=AssertionError('Learning opened encoder')):
                    mcp = FastMCP('native-learning-mutations-fixture')
                    repository_learning.register(mcp)
                    app = Starlette(routes=[Route('/evidence/read', make_read_endpoint(mcp), methods=['POST']),
                                           Route('/learning/write', make_write_endpoint(mcp), methods=['POST'])])
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                 base_url='http://fixture') as client:
                        async with create_connected_server_and_client_session(mcp) as session:
                            async def rest(path, name, arguments):
                                response = await client.post(path, json={'tool': name, 'arguments': arguments})
                                self.assertEqual(response.status_code, 200, response.text)
                                return response.json()

                            async def mcp_call(name, arguments):
                                value = await session.call_tool(name, arguments)
                                self.assertFalse(value.isError, value)
                                return value.structuredContent

                            async def read_parity(name, arguments):
                                value = await rest('/evidence/read', name, arguments)
                                self.assertEqual(value, await mcp_call(name, arguments))
                                return value

                            arguments = {'project_id': 'reference', 'expected_revision': 0,
                                         'expected_run_id': receipts['reference']['run_id']}
                            saved = await rest('/learning/write', 'learn_repository_patterns', arguments)
                            self.assertEqual(saved['status'], 'stored', saved)
                            self.assertEqual(saved['revision'], 1)
                            same = await mcp_call('learn_repository_patterns', dict(arguments, expected_revision=1))
                            self.assertEqual(same['status'], 'unchanged', same)
                            self.assertEqual(same['record_id'], saved['record_id'])
                            await owner.delete_project('reference')
                            learned = await read_parity('get_learned_repository_patterns', {'project_id': 'reference'})
                            self.assertEqual(learned['status'], 'learned', learned)
                            self.assertEqual(learned['profile']['repo_name'], 'reference')
                            self.assertEqual(learned['publication']['run_id'], receipts['reference']['run_id'])
                            suggested = await read_parity('suggest_learned_repository_patterns', {
                                'reference_project_id': 'reference', 'target_project_id': 'target',
                                'reference_revision': 1})
                            self.assertEqual(suggested['status'], 'ok', suggested)
                            self.assertEqual(suggested['reference_memory']['record_id'], saved['record_id'])
                            text = '# Café rules\n- Use `Policy`\n'
                            saved_text = await rest('/learning/write', 'learn_text_patterns', {
                                'project_id': 'provided-notes', 'source_id': 'caller-note', 'text': text,
                                'expected_revision': 0})
                            self.assertEqual(saved_text['status'], 'stored', saved_text)
                            notes = await read_parity('get_learned_repository_patterns', {'project_id': 'provided-notes'})
                            self.assertEqual(notes['ast']['content'], text)
                            self.assertEqual(notes['ast']['end_byte'], len(text.encode()))
                            self.assertEqual(notes['backend']['name'], 'RepoAnalyzer')
                            conflict = await mcp_call('delete_repository_learning', {
                                'project_id': 'provided-notes', 'expected_revision': 2})
                            self.assertEqual(conflict['status'], 'conflict', conflict)
                            deleted = await rest('/learning/write', 'delete_repository_learning', {
                                'project_id': 'provided-notes', 'expected_revision': 1})
                            self.assertEqual(deleted['status'], 'deleted', deleted)
                            absent = await read_parity('get_learned_repository_patterns', {'project_id': 'provided-notes'})
                            self.assertEqual(absent['status'], 'not-learned')
                            self.assertEqual(absent['revision'], 2)
                            self.assertFalse((base / 'reference' / 'main.py').exists())
                            self.assertFalse((base / 'target' / 'main.py').exists())

    async def test_native_profiles_and_comparison_through_both_transports(self):
        with tempfile.TemporaryDirectory(prefix='native-learning-integration-') as directory:
            base = Path(directory)
            runtime = EmbeddedRuntime(str(base / 'state'), 3)
            async with EmbeddedRepositoryOwner(runtime.state, 3) as owner:
                runtime._owner = owner
                receipts, hashes = {}, {}
                for project, name in [('reference', 'Reference'), ('target', 'Target')]:
                    source = base / project
                    source.mkdir()
                    content = f'# λ published fixture\nclass {name}:\n    def inspect(self):\n        return 1\n'
                    (source / 'main.py').write_text(content)
                    receipts[project] = await owner.index(str(source), project, ['main.py'],
                                                        embed=fixture_embed, encoder_id='synthetic-fixture')
                    hashes[project] = hashlib.sha256(content.encode()).hexdigest()
                    (source / 'main.py').unlink()
                environment = {
                    'PYTHON_DOTENV_DISABLED': '1', 'LM_PROXY_STORAGE_BACKEND': 'embedded',
                    'LM_PROXY_GRAPH_BACKEND': 'ladybug', 'LM_PROXY_EMBEDDED_STATE': runtime.state,
                    'LM_PROXY_REPOANALYZER_ROOT': os.environ['LM_PROXY_REPOANALYZER_TEST_ROOT'],
                    'LM_PROXY_REPOANALYZER_PYTHON': os.getenv('LM_PROXY_REPOANALYZER_TEST_PYTHON', ''),
                }
                with mock.patch.dict(os.environ, environment), mock.patch(
                    'memory.embedded_runtime.get_embedded_runtime', return_value=runtime,
                ), mock.patch.object(runtime, '_get_encoder', side_effect=AssertionError('Profile opened encoder')):
                    mcp = FastMCP('native-repository-learning-fixture')
                    repository_learning.register(mcp)
                    app = Starlette(routes=[Route('/evidence/read', make_read_endpoint(mcp), methods=['POST'])])
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                 base_url='http://fixture') as client:
                        async with create_connected_server_and_client_session(mcp) as session:
                            for tool, arguments, projects in [
                                ('get_repository_learning_profile', {'project_id': 'reference'}, ['reference']),
                                ('compare_repository_learning_profiles', {
                                    'reference_project_id': 'reference', 'target_project_id': 'target'},
                                 ['reference', 'target']),
                            ]:
                                response = await client.post('/evidence/read', json={'tool': tool, 'arguments': arguments})
                                result = await session.call_tool(tool, arguments)
                                self.assertEqual(response.status_code, 200)
                                self.assertFalse(result.isError)
                                self.assertEqual(response.json(), result.structuredContent)
                                payload = response.json()
                                self.assertEqual(payload['status'], 'ok', payload)
                                self.assertEqual(payload['backend']['name'], 'RepoAnalyzer')
                                self.assertFalse(payload['backend']['runtime_initialized'])
                                self.assertEqual(payload['backend']['network_access'], 'denied')
                                self.assertEqual([citation['project_id'] for citation in payload['publications']], projects)
                                for citation in payload['publications']:
                                    project = citation['project_id']
                                    self.assertEqual(citation['run_id'], receipts[project]['run_id'])
                                    self.assertEqual(citation['manifest_sha256'], receipts[project]['manifest']['sha256'])
                                for citation in payload['sources']:
                                    self.assertEqual(citation['source_sha256'], hashes[citation['project_id']])
                                    self.assertTrue(any(symbol['kind'] == 'Class' for symbol in citation['symbols']))
                                profiles = ([payload['result']] if len(projects) == 1 else [
                                    payload['result']['reference_profile'], payload['result']['target_profile']])
                                for profile in profiles:
                                    self.assertEqual(profile['conventions']['symbol_kinds']['Class'], 1)
                                    self.assertTrue(profile['recommendations'])


if __name__ == '__main__':
    unittest.main()

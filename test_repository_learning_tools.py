"""Offline MCP JSON-RPC and REST learning contracts; no engines, files or models.

Run with the existing Python environment. The core service is replaced by an
in-memory fixture; MCP uses SDK memory streams and REST uses an ASGI transport.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.applications import Starlette
from starlette.routing import Route

from tools.brain import repository_learning, tool_catalog
from tools.brain.embedded_rest import LEARNING_WRITE_TOOLS, READ_TOOLS, make_read_endpoint, make_write_endpoint
from tools.brain.primary import apply_primary_tool_filter, register_primary_dispatcher

PROFILE = 'get_repository_learning_profile'
COMPARE = 'compare_repository_learning_profiles'
SERVICE_MODULE = 'graphrag_core.repository_learning.service'
PATTERNS_MODULE = 'graphrag_core.repository_learning.patterns'
PATTERN_READS = {'get_learned_repository_patterns', 'suggest_learned_repository_patterns'}
PATTERN_WRITES = {'learn_repository_patterns', 'learn_text_patterns', 'learn_configuration_patterns',
                  'delete_repository_learning'}
READ_NAMES = {PROFILE, COMPARE} | PATTERN_READS
EMBEDDED_ENV = {
    'PYTHON_DOTENV_DISABLED': '1',
    'LM_PROXY_STORAGE_BACKEND': 'embedded',
    'LM_PROXY_GRAPH_BACKEND': 'ladybug',
    'LM_PROXY_EMBEDDED_STATE': '/unused-learning-transport-state',
    'LM_PROXY_REPOSITORY_LEARNING_STATE': '',
}


class LearningTransports(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.service = types.ModuleType(SERVICE_MODULE)

        async def profile(project_id, *, max_files):
            self.calls.append((PROFILE, project_id, max_files))
            return {'status': 'ok', 'operation': 'profile', 'project_id': project_id,
                    'publication': {'run_id': 'fixture-run', 'source_sha256': 'fixture-hash'},
                    'coverage': {'max_files': max_files, 'complete': False},
                    'patterns': [{'name': 'fixture-pattern', 'origin': 'analyzer-observation'}]}

        async def compare(reference_project_id, target_project_id, *, max_files):
            self.calls.append((COMPARE, reference_project_id, target_project_id, max_files))
            return {'status': 'ok', 'operation': 'comparison',
                    'reference_project_id': reference_project_id, 'target_project_id': target_project_id,
                    'publications': {'reference': 'fixture-reference', 'target': 'fixture-target'},
                    'coverage': {'max_files': max_files, 'complete': False},
                    'suggestions': [{'kind': 'structural-transfer', 'verified_repository_fact': False}]}

        self.service.get_repository_learning_profile = mock.AsyncMock(side_effect=profile)
        self.service.compare_repository_learning_profiles = mock.AsyncMock(side_effect=compare)
        self.patterns = types.ModuleType(PATTERNS_MODULE)

        def operation(name):
            async def invoke(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return {'version': 1, 'status': 'ok', 'operation': name, 'arguments': list(args),
                        'options': kwargs, 'revision': 2, 'source_kind': 'transport-fixture'}
            return mock.AsyncMock(side_effect=invoke)

        for name in PATTERN_READS | PATTERN_WRITES:
            setattr(self.patterns, name, operation(name))
        patcher = mock.patch.dict('sys.modules', {SERVICE_MODULE: self.service, PATTERNS_MODULE: self.patterns})
        patcher.start()
        self.addCleanup(patcher.stop)
        env_patcher = mock.patch.dict(os.environ, EMBEDDED_ENV)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        self.mcp = FastMCP('repository-learning-transport-fixture')
        repository_learning.register(self.mcp)
        app = Starlette(routes=[Route('/evidence/read', make_read_endpoint(self.mcp), methods=['POST']),
                               Route('/learning/write', make_write_endpoint(self.mcp), methods=['POST'])])
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def rest(self, name, arguments):
        return await self.client.post('/evidence/read', json={'tool': name, 'arguments': arguments})

    async def write(self, name, arguments):
        return await self.client.post('/learning/write', json={'tool': name, 'arguments': arguments})

    def enable_mutations(self):
        for name in READ_NAMES:
            self.mcp.remove_tool(name)
        with mock.patch.dict(os.environ, {'LM_PROXY_REPOSITORY_LEARNING_STATE': '/unused-private-learning-state'}):
            repository_learning.register(self.mcp)

    async def test_profile_and_comparison_mcp_rest_parity(self):
        async with create_connected_server_and_client_session(self.mcp) as session:
            catalog = await session.list_tools()
            self.assertEqual({tool.name for tool in catalog.tools}, READ_NAMES)
            for tool in catalog.tools:
                self.assertTrue(tool.annotations.readOnlyHint)
                self.assertFalse(tool.annotations.destructiveHint)
                self.assertFalse(tool.annotations.openWorldHint)
            for name, arguments in [
                (PROFILE, {'project_id': 'target'}),
                (PROFILE, {'project_id': 'target', 'max_files': 64}),
                (COMPARE, {'reference_project_id': 'reference', 'target_project_id': 'target', 'max_files': 2}),
            ]:
                with self.subTest(name=name, arguments=arguments):
                    result = await session.call_tool(name, arguments)
                    response = await self.rest(name, arguments)
                    self.assertFalse(result.isError)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(result.structuredContent, response.json())
                    self.assertEqual(json.loads(result.content[0].text), response.json())
            self.assertIn((PROFILE, 'target', 20), self.calls)
            self.assertIn((PROFILE, 'target', 64), self.calls)
            self.assertIn((COMPARE, 'reference', 'target', 2), self.calls)

    async def test_strict_bounded_limits_and_required_fields(self):
        async with create_connected_server_and_client_session(self.mcp) as session:
            schemas = {tool.name: tool.inputSchema for tool in (await session.list_tools()).tools}
            for schema in (schemas[PROFILE], schemas[COMPARE]):
                self.assertEqual(schema['properties']['max_files']['minimum'], 1)
                self.assertEqual(schema['properties']['max_files']['maximum'], 64)
                self.assertEqual(schema['properties']['max_files']['default'], 20)
            for name, base in [(PROFILE, {'project_id': 'p'}),
                               (COMPARE, {'reference_project_id': 'r', 'target_project_id': 't'})]:
                for value in (0, 65, True, False, '2', 2.5, None):
                    arguments = dict(base, max_files=value)
                    with self.subTest(name=name, max_files=value):
                        self.assertEqual((await self.rest(name, arguments)).status_code, 422)
                        self.assertTrue((await session.call_tool(name, arguments)).isError)
                self.assertEqual((await self.rest(name, {})).status_code, 422)
                self.assertTrue((await session.call_tool(name, {})).isError)
        self.assertEqual(self.calls, [])

    async def test_disabled_status_is_preserved_without_runtime_access(self):
        disabled = {'status': 'disabled', 'reason': 'repository-learning-adapter-not-configured'}
        self.service.get_repository_learning_profile.side_effect = None
        self.service.get_repository_learning_profile.return_value = disabled
        self.service.compare_repository_learning_profiles.side_effect = None
        self.service.compare_repository_learning_profiles.return_value = disabled
        with mock.patch('memory.embedded_runtime.get_embedded_runtime',
                        side_effect=AssertionError('transport must not open an owner')):
            async with create_connected_server_and_client_session(self.mcp) as session:
                for name, arguments in [(PROFILE, {'project_id': 'p'}),
                                        (COMPARE, {'reference_project_id': 'r', 'target_project_id': 't'})]:
                    result = await session.call_tool(name, arguments)
                    response = await self.rest(name, arguments)
                    self.assertFalse(result.isError)
                    self.assertEqual(result.structuredContent, disabled)
                    self.assertEqual(response.json(), disabled)
        self.assertEqual(self.calls, [])

    async def test_read_allowlist_excludes_mutations_and_unknown_arguments(self):
        self.assertTrue(READ_NAMES <= READ_TOOLS)
        self.assertEqual(LEARNING_WRITE_TOOLS, PATTERN_WRITES)
        self.assertFalse(LEARNING_WRITE_TOOLS & READ_TOOLS)
        for name in ('index_embedded_repository', 'update_embedded_project_metadata',
                     'configure_embedded_project_watch', 'unknown-learning-operation'):
            self.assertEqual((await self.rest(name, {})).status_code, 422)
        response = await self.rest(PROFILE, {'project_id': 'p', 'source_root': '/never-read'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.calls, [])

    async def test_persistent_reads_and_explicit_mutations_mcp_rest_parity(self):
        self.enable_mutations()
        examples = [
            ('get_learned_repository_patterns', {'project_id': 'p', 'revision': 1, 'limit': 2, 'offset': 1}),
            ('suggest_learned_repository_patterns', {'reference_project_id': 'r', 'target_project_id': 't',
                                                     'reference_revision': 1, 'max_files': 2}),
            ('learn_repository_patterns', {'project_id': 'p', 'expected_run_id': 'run-1',
                                            'expected_revision': 0, 'max_files': 2}),
            ('learn_text_patterns', {'project_id': 'p', 'source_id': 'provided-note', 'text': '# Test\nA note',
                                    'expected_revision': 1}),
            ('learn_configuration_patterns', {'project_id': 'p', 'source_id': 'provided-config', 'text': 'requests==2',
                                               'parser': 'requirements', 'expected_revision': 1}),
            ('delete_repository_learning', {'project_id': 'p', 'expected_revision': 1}),
        ]
        async with create_connected_server_and_client_session(self.mcp) as session:
            catalog = {tool.name: tool for tool in (await session.list_tools()).tools}
            for name, arguments in examples:
                with self.subTest(name=name):
                    result = await session.call_tool(name, arguments)
                    response = await (self.write(name, arguments) if name in PATTERN_WRITES
                                      else self.rest(name, arguments))
                    self.assertFalse(result.isError)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(result.structuredContent, response.json())
                    self.assertEqual(catalog[name].annotations.readOnlyHint, name not in PATTERN_WRITES)
                    self.assertEqual(catalog[name].annotations.destructiveHint,
                                     name == 'delete_repository_learning')
            for name in PATTERN_WRITES:
                self.assertEqual((await self.rest(name, {})).status_code, 422)
            for name in READ_NAMES | {'index_embedded_repository', 'update_embedded_project_metadata'}:
                self.assertEqual((await self.write(name, {})).status_code, 422)

    async def test_mutations_are_unregistered_until_explicit_state(self):
        async with create_connected_server_and_client_session(self.mcp) as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            self.assertFalse(names & PATTERN_WRITES)
            result = await session.call_tool('delete_repository_learning',
                                             {'project_id': 'p', 'expected_revision': 0})
            self.assertTrue(result.isError)
            self.assertEqual((await self.write('delete_repository_learning',
                {'project_id': 'p', 'expected_revision': 0})).status_code, 503)
        self.assertEqual(self.calls, [])

    async def test_persistent_revision_and_paging_bounds_are_strict(self):
        self.enable_mutations()
        invalid = [
            ('learn_repository_patterns', {'project_id': 'p', 'expected_run_id': '', 'expected_revision': 0}),
            ('learn_repository_patterns', {'project_id': 'p', 'expected_run_id': 'r', 'expected_revision': -1}),
            ('learn_repository_patterns', {'project_id': 'p', 'expected_run_id': 'r', 'expected_revision': True}),
            ('learn_text_patterns', {'project_id': 'p', 'source_id': 'x' * 257, 'text': 'a', 'expected_revision': 0}),
            ('learn_text_patterns', {'project_id': 'p', 'source_id': 's', 'text': 'x' * 16385, 'expected_revision': 0}),
            ('learn_text_patterns', {'project_id': 'p', 'source_id': 's', 'text': '', 'expected_revision': 0}),
            ('learn_configuration_patterns', {'project_id': 'p', 'source_id': 's', 'text': 'x',
                                               'parser': 'yaml', 'expected_revision': 0}),
            ('delete_repository_learning', {'project_id': 'p', 'expected_revision': '1'}),
            ('get_learned_repository_patterns', {'project_id': 'p', 'revision': True}),
            ('get_learned_repository_patterns', {'project_id': 'p', 'limit': 0}),
            ('get_learned_repository_patterns', {'project_id': 'p', 'limit': 51}),
            ('get_learned_repository_patterns', {'project_id': 'p', 'offset': 1025}),
            ('suggest_learned_repository_patterns', {'reference_project_id': 'r', 'target_project_id': 't',
                                                     'reference_revision': -1}),
        ]
        async with create_connected_server_and_client_session(self.mcp) as session:
            for name, arguments in invalid:
                with self.subTest(name=name, arguments=arguments.keys()):
                    response = await (self.write(name, arguments) if name in PATTERN_WRITES
                                      else self.rest(name, arguments))
                    self.assertEqual(response.status_code, 422)
                    self.assertTrue((await session.call_tool(name, arguments)).isError)
        self.assertEqual(self.calls, [])

    async def test_write_body_limit_accepts_full_text_with_framing_and_refuses_overflow(self):
        self.enable_mutations()
        arguments = {'project_id': 'p', 'source_id': 's', 'text': 'x' * 16384, 'expected_revision': 0}
        self.assertEqual((await self.write('learn_text_patterns', arguments)).status_code, 200)
        self.assertEqual((await self.client.post('/learning/write', content='x' * (20 * 1024 + 1))).status_code, 413)
        self.assertEqual((await self.client.get('/learning/write')).status_code, 405)

    async def test_opted_in_mutations_are_discoverable_but_not_read_dispatched(self):
        self.enable_mutations()
        tool_catalog.register(self.mcp)
        register_primary_dispatcher(self.mcp)
        apply_primary_tool_filter(self.mcp)
        async with create_connected_server_and_client_session(self.mcp) as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            self.assertTrue(PATTERN_WRITES <= names)
            for name in PATTERN_WRITES:
                result = await session.call_tool('dispatch_deep_analysis', {'tool_name': name})
                self.assertTrue(result.isError)
            normal = await session.call_tool('get_mcp_tool_catalog', {'intent': 'repository learning'})
            admin = await session.call_tool('get_mcp_tool_catalog',
                                             {'intent': 'repository learning', 'include_admin': True})
            for name in PATTERN_WRITES:
                self.assertNotIn(name, normal.content[0].text)
                self.assertIn(name, admin.content[0].text)
        self.assertEqual(self.calls, [])

    def test_daemon_write_route_requires_rest_embedded_and_learning_state(self):
        script = ("import brain_server; print('WRITE_ROUTE=' + str(any("
                  "getattr(r, 'path', '') == '/learning/write' for r in brain_server.app.routes)))")
        for flag, backend, state, expected in [
            ('0', 'embedded', '/unused-learning-state', False),
            ('1', 'neo4j', '/unused-learning-state', False),
            ('1', 'embedded', '', False),
            ('1', 'embedded', '/unused-learning-state', True),
        ]:
            with self.subTest(flag=flag, backend=backend, learning_state=bool(state)):
                env = dict(os.environ, PYTHON_DOTENV_DISABLED='1', LM_PROXY_EMBEDDED_REST_ENABLED=flag,
                           LM_PROXY_STORAGE_BACKEND=backend,
                           LM_PROXY_GRAPH_BACKEND='ladybug' if backend == 'embedded' else 'neo4j',
                           LM_PROXY_REPOSITORY_LEARNING_STATE=state, LM_PROXY_EMBEDDED_STATE='/unused',
                           LM_PROXY_MEMORY_ENABLED='0', LM_PROXY_WATCHER_ENABLED='0',
                           LM_PROXY_EMBEDDED_WATCH_ENABLED='0')
                result = subprocess.run([sys.executable, '-c', script], env=env,
                                        cwd=Path(__file__).resolve().parent, capture_output=True,
                                        text=True, timeout=20)
                self.assertEqual(result.returncode, 0)
                self.assertIn('WRITE_ROUTE=' + str(expected), result.stdout)

    async def test_compact_discovery_and_catalog_keep_both_capabilities(self):
        tool_catalog.register(self.mcp)
        apply_primary_tool_filter(self.mcp)
        async with create_connected_server_and_client_session(self.mcp) as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            self.assertTrue(READ_NAMES <= names)
            for name in (PROFILE, COMPARE):
                result = await session.call_tool('get_mcp_tool_catalog', {'tool_name': name})
                self.assertFalse(result.isError)
                entry = json.loads(result.content[0].text)
                self.assertEqual(entry['name'], name)
                self.assertIn('max_files', entry['inputSchema']['properties'])
            result = await session.call_tool('get_mcp_tool_catalog', {'intent': 'repository learning'})
            self.assertFalse(result.isError)
            for name in (PROFILE, COMPARE):
                self.assertIn(name, result.content[0].text)
            self.assertIn('repository learning', result.content[0].text)

    def test_registration_requires_embedded_selection_and_explicit_state(self):
        for backend, graph, state, expected in [
            ('embedded', 'ladybug', '/unused', True),
            ('', 'ladybug', '/unused', True),
            ('embedded', 'ladybug', '', False),
            ('embedded', 'ladybug', '   ', False),
            ('neo4j', 'neo4j', '/unused', False),
        ]:
            with self.subTest(backend=backend, graph=graph, state=state):
                mcp = FastMCP('registration-only-fixture')
                with mock.patch.dict(os.environ, {'LM_PROXY_STORAGE_BACKEND': backend,
                        'LM_PROXY_GRAPH_BACKEND': graph, 'LM_PROXY_EMBEDDED_STATE': state}):
                    repository_learning.register(mcp)
                self.assertEqual({tool.name for tool in mcp._tool_manager.list_tools()},
                                 READ_NAMES if expected else set())
        self.service.get_repository_learning_profile.assert_not_called()
        self.service.compare_repository_learning_profiles.assert_not_called()


if __name__ == '__main__':
    unittest.main()

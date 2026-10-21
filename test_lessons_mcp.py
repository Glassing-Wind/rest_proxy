"""Offline standalone Free Man MCP acceptance; no sockets, models or personal data.

Run .venv/bin/python -W error::ResourceWarning test_lessons_mcp.py. JSON-RPC uses
the installed SDK memory streams and disposable synthetic corpus snapshots.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from mcp.shared.memory import create_connected_server_and_client_session

from graphrag_core.lessons_corpus.corpus import canonical, sha256
from graphrag_core.lessons_corpus.mcp import create_server
from graphrag_core.lessons_corpus.server import create_app
from scripts import free_man
import test_lessons_corpus as corpus_fixtures


NAMES = {'free_man_status', 'free_man_list_sources', 'free_man_read_source',
         'free_man_search_sources', 'free_man_list_references'}
SOURCE = 'post:11:content_html'
ROOT = Path(__file__).resolve().parent


def tree(path):
    return {str(item.relative_to(path)): (item.stat().st_mtime_ns, hashlib.sha256(item.read_bytes()).hexdigest())
            for item in path.rglob('*') if item.is_file()}


class LessonsMCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = corpus_fixtures.CorpusTests('test_source_round_trip_and_reference_occurrences')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.fixture()
        self.corpus = self.fixture.build()
        self.server = create_server(str(self.corpus.root))

    def assert_error(self, result, error):
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent, {'error': error})
        self.assertEqual(json.loads(result.content[0].text), {'error': error})
        self.assertNotIn(str(self.fixture.root), result.model_dump_json())
        self.assertNotIn('Traceback', result.model_dump_json())

    async def test_exact_small_catalog_annotations_and_strict_schemas(self):
        async with create_connected_server_and_client_session(self.server) as session:
            tools = (await session.list_tools()).tools
        self.assertEqual({tool.name for tool in tools}, NAMES)
        for tool in tools:
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertTrue(tool.annotations.idempotentHint)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertFalse(tool.annotations.openWorldHint)
            self.assertFalse(tool.inputSchema['additionalProperties'])
        schemas = {tool.name: tool.inputSchema for tool in tools}
        self.assertEqual(schemas['free_man_read_source']['required'], ['source_id'])
        self.assertEqual(schemas['free_man_list_sources']['properties']['limit']['maximum'], 100)
        self.assertEqual(schemas['free_man_search_sources']['properties']['limit']['maximum'], 50)
        self.assertEqual(schemas['free_man_search_sources']['properties']['query']['maxLength'], 1024)

    async def test_status_has_identity_and_qualified_coverage_without_paths(self):
        async with create_connected_server_and_client_session(self.server) as session:
            result = await session.call_tool('free_man_status')
        self.assertFalse(result.isError)
        data = result.structuredContent
        self.assertEqual(data['manifest_sha256'], sha256((self.corpus.root / 'manifest.json').read_bytes()))
        self.assertEqual(data['source_count'], len(self.corpus.sources))
        self.assertTrue(data['read_only'])
        self.assertTrue(data['representation_complete'])
        self.assertIn('not verified claim', data['interpretation_status'])
        self.assertNotIn(str(self.fixture.root), result.model_dump_json())

    async def test_rest_parity_pagination_search_filter_and_occurrences(self):
        capability = 'synthetic-free-man-parity-0123456789abcdef'
        app = create_app(str(self.corpus.root), capability, '127.0.0.1')
        cases = [
            ('free_man_list_sources', {'limit': 2, 'offset': 1}, '/sources'),
            ('free_man_read_source', {'source_id': SOURCE, 'start_char': 1, 'end_char': 8}, '/source'),
            ('free_man_search_sources', {'query': 'dignity', 'limit': 1,
                                        'source_kind': 'wordpress_post_content_html'}, '/search'),
            ('free_man_list_references', {'source_id': SOURCE, 'status': 'external_unacquired',
                                         'limit': 1}, '/references'),
        ]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)),
                                     base_url='http://127.0.0.1',
                                     headers={'Authorization': 'Bearer ' + capability}) as client:
            async with create_connected_server_and_client_session(self.server) as session:
                for name, arguments, route in cases:
                    with self.subTest(name=name):
                        result = await session.call_tool(name, arguments)
                        response = await client.get(route, params=arguments)
                        self.assertFalse(result.isError, result)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(result.structuredContent, response.json())
                        self.assertEqual(json.loads(result.content[0].text), response.json())
                first = (await session.call_tool('free_man_list_references', {
                    'source_id': SOURCE, 'status': 'external_unacquired', 'limit': 1,
                })).structuredContent
                second = (await session.call_tool('free_man_list_references', {
                    'source_id': SOURCE, 'status': 'external_unacquired', 'limit': 1, 'offset': 1,
                })).structuredContent
                self.assertEqual(first['total'], 2)
                self.assertEqual(first['next_offset'], 1)
                self.assertNotEqual(first['references'][0]['id'], second['references'][0]['id'])
                self.assertEqual(first['references'][0]['resolved_url'], second['references'][0]['resolved_url'])

    async def test_unicode_exact_half_open_citation_and_empty_end(self):
        source = self.corpus.source(SOURCE)
        quote = 'dignity 😀'
        start = source['text'].index(quote)
        async with create_connected_server_and_client_session(self.server) as session:
            result = await session.call_tool('free_man_read_source', {
                'source_id': SOURCE, 'start_char': start, 'end_char': start + len(quote),
            })
            empty = await session.call_tool('free_man_read_source', {
                'source_id': SOURCE, 'start_char': len(source['text']),
            })
        data = result.structuredContent
        self.assertEqual(data['text'], quote)
        self.assertEqual(data['citation'], {'source_id': SOURCE, 'source_sha256': source['source_sha256'],
            'text_sha256': source['text_sha256'], 'start_char': start, 'end_char': start + len(quote),
            'quote': quote})
        self.assertEqual(empty.structuredContent['text'], '')
        self.assertIsNone(empty.structuredContent['citation'])
        self.assertIsNone(empty.structuredContent['next_start_char'])
        self.assertNotIn('ast', data)

    async def test_strict_invalid_inputs_are_small_generic_errors(self):
        cases = [(name, {'extra': str(self.fixture.root)}) for name in NAMES]
        cases.extend(('free_man_list_sources', {'limit': value})
                     for value in (True, False, 2.0, '2', None, 0, 101, -1))
        cases.extend(('free_man_list_sources', {'offset': value}) for value in (-1, 1000000001, True))
        cases.extend([
            ('free_man_read_source', {}),
            ('free_man_read_source', {'source_id': ' '}),
            ('free_man_read_source', {'source_id': 'x\x00'}),
            ('free_man_read_source', {'source_id': SOURCE, 'start_char': 9, 'end_char': 1}),
            ('free_man_read_source', {'source_id': SOURCE, 'end_char': 4097}),
            ('free_man_read_source', {'source_id': SOURCE, 'start_char': 999}),
            ('free_man_search_sources', {'query': ' '}),
            ('free_man_search_sources', {'query': 'x' * 1025}),
            ('free_man_search_sources', {'query': 'care', 'source_kind': None}),
            ('free_man_search_sources', {'query': 'care', 'source_kind': 'x' * 129}),
            ('free_man_search_sources', {'query': 'care', 'limit': 51}),
            ('free_man_list_references', {'status': 'verified-truth'}),
        ])
        async with create_connected_server_and_client_session(self.server) as session:
            for name, arguments in cases:
                with self.subTest(name=name, arguments=arguments):
                    self.assert_error(await session.call_tool(name, arguments), 'invalid-query')

    async def test_no_mutations_operational_tools_or_unknown_dispatch(self):
        async with create_connected_server_and_client_session(self.server) as session:
            for name in ('index_workspace', 'review', 'propose', 'capture', 'bootstrap', 'free_man_unknown'):
                self.assert_error(await session.call_tool(name, {'secret': 'private'}), 'unknown-tool')

    async def test_missing_source_and_corrupt_artifacts_fail_without_path_leaks(self):
        async with create_connected_server_and_client_session(self.server) as session:
            for name in ('free_man_read_source', 'free_man_list_references'):
                self.assert_error(await session.call_tool(name, {'source_id': 'missing'}), 'source-not-found')
            for artifact, name, arguments in [
                ('sources.jsonl', 'free_man_list_sources', {}),
                ('references.jsonl', 'free_man_list_references', {}),
                ('search.sqlite3', 'free_man_search_sources', {'query': 'dignity'}),
                ('coverage.json', 'free_man_status', {}),
                (self.corpus.sources[SOURCE]['text_path'], 'free_man_read_source', {'source_id': SOURCE}),
                ('manifest.json', 'free_man_status', {}),
            ]:
                with self.subTest(artifact=artifact):
                    path = self.corpus.root / artifact
                    original = path.read_bytes()
                    path.write_bytes(original + b'corruption')
                    try:
                        self.assert_error(await session.call_tool(name, arguments), 'corpus-integrity-check-failed')
                    finally:
                        path.write_bytes(original)

    async def test_blocking_io_runs_in_worker_thread_and_unexpected_errors_are_private(self):
        main_thread, workers = threading.get_ident(), []

        def operation(reads):
            workers.append(threading.get_ident())
            raise OSError(str(self.fixture.root) + ' private-runtime-credential')

        with patch('graphrag_core.lessons_corpus.reads.CorpusReads.status', operation):
            async with create_connected_server_and_client_session(self.server) as session:
                result = await session.call_tool('free_man_status')
        self.assertTrue(workers)
        self.assertTrue(all(worker != main_thread for worker in workers))
        self.assert_error(result, 'corpus-operation-unavailable')

    async def test_recovery_qualifications_survive_catalog_excerpt_and_search(self):
        fixture = corpus_fixtures.CorpusTests('test_source_round_trip_and_reference_occurrences')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.fixture()
        path = fixture.capture / 'acquisitions.jsonl'
        acquisitions = [json.loads(line) for line in path.read_text().splitlines()]
        target = 'https://example.test/missing-historical-page/'
        acquisitions[0]['reference_recovery'] = {
            'parent_manifest_sha256': 'a' * 64, 'target_id': sha256(target.encode()),
            'original_target_url': target, 'relation': 'candidate_counterpart',
            'assessment': 'unreviewed', 'provenance': 'operator_selected_candidate',
        }
        path.write_bytes(b''.join(canonical(row) for row in acquisitions))
        corpus = fixture.build()
        server = create_server(str(corpus.root))
        offset = sorted(corpus.sources).index(SOURCE)
        async with create_connected_server_and_client_session(server) as session:
            calls = [('free_man_list_sources', {'offset': offset, 'limit': 1}),
                     ('free_man_read_source', {'source_id': SOURCE}),
                     ('free_man_search_sources', {'query': 'dignity', 'source_kind': 'wordpress_post_content_html'})]
            for name, arguments in calls:
                result = await session.call_tool(name, arguments)
                self.assertFalse(result.isError, result)
                data = result.structuredContent
                row = data['sources'][0] if name.endswith('list_sources') else (
                    data['results'][0] if name.endswith('search_sources') else data)
                self.assertIn('reference_recovery_unreviewed', row['interpretation_status'])
                self.assertIn('historical_equivalence_not_established', row['interpretation_status'])

    async def test_reads_do_not_create_state_fetch_targets_or_execute_source_instructions(self):
        before = tree(self.fixture.root)
        with patch('socket.getaddrinfo', side_effect=AssertionError('Unexpected network')), \
                patch('subprocess.run', side_effect=AssertionError('Unexpected worker')):
            async with create_connected_server_and_client_session(self.server) as session:
                for name, arguments in [
                    ('free_man_status', {}), ('free_man_list_sources', {}),
                    ('free_man_read_source', {'source_id': 'html:a3'}),
                    ('free_man_search_sources', {'query': 'page'}), ('free_man_list_references', {}),
                ]:
                    result = await session.call_tool(name, arguments)
                    self.assertFalse(result.isError, result)
        self.assertEqual(tree(self.fixture.root), before)
        self.assertFalse(list(self.fixture.root.rglob('lesson-ledger.sqlite3')))

    def test_factory_rejects_missing_relative_and_symlink_roots_without_creating_state(self):
        before = tree(self.fixture.root)
        link = self.fixture.root / 'alias'
        link.symlink_to(self.corpus.root, target_is_directory=True)
        for path in ('relative', '', None, str(self.fixture.root / 'absent'), str(link),
                     str(self.corpus.root / '..' / 'representation')):
            with self.subTest(path=path):
                with self.assertRaises((ValueError, TypeError)):
                    create_server(path)
        link.unlink()
        self.assertEqual(tree(self.fixture.root), before)

    def test_hostile_cwd_env_and_operational_import_sentinels(self):
        working = self.fixture.root / 'hostile'
        working.mkdir()
        (working / '.env').write_text('FASTMCP_DEBUG=true\nLM_PROXY_STORAGE_BACKEND=server\n')
        script = r'''
import builtins, importlib.abc, io, json, os, pathlib, socket, sys
root, corpus = sys.argv[1:]
sys.path.insert(0, root)
original_open = builtins.open
original_io_open = io.open
def guarded(operation):
    def check(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and pathlib.Path(file).name == '.env':
            raise AssertionError('dotenv file was opened')
        return operation(file, *args, **kwargs)
    return check
builtins.open, io.open = guarded(original_open), guarded(original_io_open)
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('_mcp', 'mcp_server', 'brain_server', 'graphrag_core.config') or fullname.split('.')[0] in ('tools', 'memory', 'proxy'):
            raise AssertionError('operational import attempted')
sys.meta_path.insert(0, Block())
def forbidden(*args, **kwargs):
    raise AssertionError('network attempted')
socket.getaddrinfo = forbidden
# The SDK package re-exports FastMCP transitively. Importing that class is inert;
# constructing FastMCP/Settings is forbidden because it reads ambient .env.
import mcp.server.fastmcp.server as sdk
sdk.Settings.__init__ = forbidden
sdk.FastMCP.__init__ = forbidden
before = dict(os.environ)
from graphrag_core.lessons_corpus.mcp import create_server
from scripts import free_man
server = create_server(corpus)
assert server.name == 'free-man-corpus'
assert before == dict(os.environ)
print(json.dumps({'ok': True}))
'''
        before = tree(self.fixture.root)
        result = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT), str(self.corpus.root)],
                                cwd=working, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'ok': True})
        self.assertEqual(tree(self.fixture.root), before)

    def test_launcher_requires_explicit_root_and_only_invokes_owned_stdio(self):
        with patch('scripts.free_man._serve', new_callable=AsyncMock) as serve:
            self.assertEqual(free_man.main(['--corpus', str(self.corpus.root)]), 0)
            serve.assert_awaited_once()
        with patch('sys.stderr') as stderr, patch('scripts.free_man._serve', new_callable=AsyncMock) as serve:
            self.assertEqual(free_man.main(['--corpus', 'relative']), 1)
            serve.assert_not_called()
            output = ''.join(call.args[0] for call in stderr.write.call_args_list)
            self.assertIn('free-man-corpus-unavailable', output)
            self.assertNotIn('relative', output)
            self.assertNotIn('Traceback', output)
        with patch('sys.stderr'), self.assertRaises(SystemExit) as error:
            free_man.main([])
        self.assertEqual(error.exception.code, 2)

    async def test_stdio_serve_passes_only_its_streams_and_initialization(self):
        class Streams:
            async def __aenter__(self):
                return 'reader-fixture', 'writer-fixture'

            async def __aexit__(self, *args):
                return False

        server = unittest.mock.Mock()
        server.create_initialization_options.return_value = 'options-fixture'
        server.run = AsyncMock()
        with patch('mcp.server.stdio.stdio_server', return_value=Streams()) as stdio:
            await free_man._serve(server)
        stdio.assert_called_once_with()
        server.run.assert_awaited_once_with('reader-fixture', 'writer-fixture', 'options-fixture')


if __name__ == '__main__':
    unittest.main()

"""Native common-tool bridges in disposable state; synthetic vectors, no models.

Requires Ladybug, LanceDB and the modified ts-pack parser with a prepared Python
grammar. Run under an OS network-denial profile to verify offline acceptance.
This file does not start a daemon, use operational state or evaluate semantic quality.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack, contextmanager
from unittest import mock

from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner
from memory.embedded_runtime import EmbeddedRuntime
from mcp.server.fastmcp import FastMCP


async def fixture_embed(texts):
    """Explicit constant fixture vectors; not a substitute embedding provider."""
    return [[1.0, 0.0, 0.0] for _ in texts]


@contextmanager
def override_modules(modules):
    """Restore only supplied aliases; retain newly imported native extensions."""
    absent = object()
    original = {name: sys.modules.get(name, absent) for name in modules}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for name, module in original.items():
            if module is absent:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class NativeToolBridges(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='embedded-tool-bridges-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.left = self.root / 'source-left'
        self.right = self.root / 'source-right'
        self.left.mkdir()
        self.right.mkdir()
        self.left_source = "def authenticate(value):\n    return 'left-only'\n"
        self.right_source = "def authenticate(value):\n    return 'right-only'\n"
        (self.left / 'main.py').write_text(self.left_source)
        (self.right / 'main.py').write_text(self.right_source)
        self.state = str(self.root / 'state')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {
            'PYTHON_DOTENV_DISABLED': '1',
            'LM_PROXY_STORAGE_BACKEND': 'embedded',
            'LM_PROXY_GRAPH_BACKEND': 'ladybug',
            'LM_PROXY_EMBEDDED_STATE': self.state,
            'LM_PROXY_MEMORY_EMBEDDING_DIM': '3',
            'LM_PROXY_MEMORY_ENABLED': '0',
            'LM_PROXY_WATCHER_ENABLED': '0',
            'LM_PROXY_EMBEDDED_WATCH_ENABLED': '0',
            'LM_PROXY_RUNTIME_DIR': str(self.root / 'runtime'),
            'LM_PROXY_CONFIG_DIR': str(self.root / 'config'),
        }))
        # Imports must not read operational .env/shared configuration or initialize
        # the proxy package merely to obtain its compatibility logging export.
        self.stack.enter_context(mock.patch('graphrag_core.config.load_env'))
        logging_module = types.ModuleType('proxy.logging')
        logging_module.debug_log = mock.Mock()
        legacy_embedding = types.ModuleType('embedding_service')
        legacy_embedding.get_embedding_service = mock.Mock(
            side_effect=AssertionError('legacy embedding service was requested'))
        legacy_graph = types.ModuleType('graph_bootstrap')
        legacy_graph.require_driver = mock.AsyncMock(
            side_effect=AssertionError('legacy graph driver was requested'))
        self.stack.enter_context(override_modules({
            'proxy.logging': logging_module,
            'embedding_service': legacy_embedding,
            'graph_bootstrap': legacy_graph,
        }))
        self.semantic = importlib.import_module('tools.brain.search.semantic')
        self.indexing = importlib.import_module('tools.hands.indexing')
        self.stack.enter_context(mock.patch.object(
            self.semantic, 'get_memory_modules',
            side_effect=AssertionError('legacy search store was requested')))
        self.stack.enter_context(mock.patch.object(
            self.indexing, 'get_memory_modules',
            side_effect=AssertionError('legacy health store was requested')))
        self.stack.enter_context(mock.patch.object(
            self.indexing, 'get_workspace_path',
            side_effect=AssertionError('legacy workspace resolution was requested')))

        async with EmbeddedRepositoryOwner(self.state, 3) as owner:
            self.left_publication = await owner.index(
                str(self.left), 'left', ['main.py'], embed=fixture_embed,
                encoder_id='native-bridge-fixture')
            self.right_publication = await owner.index(
                str(self.right), 'right', ['main.py'], embed=fixture_embed,
                encoder_id='native-bridge-fixture')
        # Exercise durable reopen through the runtime, not a second live owner.
        self.runtime = EmbeddedRuntime(self.state, 3)
        self.stack.enter_context(mock.patch.object(
            self.runtime, '_get_encoder',
            side_effect=AssertionError('text/health requested an embedding model')))
        self.stack.enter_context(mock.patch(
            'memory.embedded_runtime.get_embedded_runtime', return_value=self.runtime))
        self.mcp = FastMCP('native-common-tool-bridge-fixture')
        self.semantic.register(self.mcp)
        self.indexing.register(self.mcp)
        self.search_tool = self.mcp._tool_manager.get_tool('search_codebase')
        self.health_tool = self.mcp._tool_manager.get_tool('get_indexing_health')
        self.assertIsNotNone(self.search_tool)
        self.assertIsNotNone(self.health_tool)

    async def asyncTearDown(self):
        if hasattr(self, 'runtime'):
            await self.runtime.close()

    async def search(self, **arguments):
        supplied = dict(query='authenticate', mode='text', k=20, exclude_tests=False)
        supplied.update(arguments)
        return json.loads(await self.search_tool.run(supplied))

    async def health(self, workspace, **arguments):
        return json.loads(await self.health_tool.run({
            'workspace_id': workspace, **arguments}))

    def assert_left_hits(self, result):
        self.assertEqual(result['backend'], 'embedded')
        self.assertEqual(result['mode'], 'text')
        self.assertEqual(result['status'], 'published')
        self.assertFalse(result['coverage_complete'])
        self.assertTrue(result['results'])
        original_hash = hashlib.sha256(self.left_source.encode()).hexdigest()
        for row in result['results']:
            self.assertEqual(row['project_id'], 'left')
            self.assertEqual(row['run_id'], self.left_publication['run_id'])
            self.assertEqual(row['file_path'], 'main.py')
            self.assertEqual(row['source_sha256'], original_hash)
            self.assertEqual(row['content_sha256'],
                             hashlib.sha256(row['content'].encode()).hexdigest())
            self.assertNotIn('right-only', row['content'])
        self.assertIn('left-only', '\n'.join(row['content'] for row in result['results']))

    async def test_common_search_reopens_native_publication_and_preserves_scope(self):
        by_id = await self.search(workspace_id='left')
        self.assert_left_hits(by_id)
        by_path = await self.search(workspace_id=str(self.left))
        self.assertEqual(by_path['results'], by_id['results'])
        partial = await self.search(workspace_ids=['left', 'missing'])
        self.assert_left_hits(partial)
        self.assertTrue(any(project['status'] == 'not_published'
                            for project in partial['projects']))
        missing = await self.search(workspace_id='missing')
        self.assertEqual(missing['status'], 'not_published')
        self.assertEqual(missing['results'], [])
        # An explicitly selected second project is allowed; it must retain its
        # own identity rather than contaminating left-only retrieval.
        both = await self.search(workspace_ids=['left', 'right'])
        self.assertEqual({row['project_id'] for row in both['results']}, {'left', 'right'})
        for row in both['results']:
            publication = (self.left_publication if row['project_id'] == 'left'
                           else self.right_publication)
            self.assertEqual(row['run_id'], publication['run_id'])

    async def test_live_byte_edits_do_not_refresh_search_and_health_marks_freshness(self):
        before = await self.search(workspace_id='left')
        self.assert_left_hits(before)
        initial = await self.health('left', audit=True)
        self.assertEqual(initial['storage_backend'], 'embedded')
        self.assertEqual(initial['status'], 'published')
        self.assertEqual(initial['freshness']['status'], 'matches_manifest')
        self.assertEqual(initial['run_id'], self.left_publication['run_id'])
        self.assertFalse(initial['coverage_complete'])
        self.assertFalse(initial['model_required'])
        self.assertTrue(initial['audit']['requested'])
        self.assertFalse(initial['audit']['supported'])
        self.assertFalse(initial['audit']['performed'])
        file = self.left / 'main.py'
        original_stat = file.stat()
        file.write_text("def authenticate(value):\n    return 'changed-live-source'\n")
        os.utime(file, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        after = await self.search(workspace_id='left')
        self.assertEqual(after['results'], before['results'])
        self.assertNotEqual(hashlib.sha256(file.read_bytes()).hexdigest(),
                            after['results'][0]['source_sha256'])
        edited = await self.health(str(self.left))
        self.assertEqual(edited['run_id'], initial['run_id'])
        self.assertEqual(edited['freshness']['status'], 'differs_from_manifest')
        self.assertEqual(edited['freshness']['counts']['modified'], 1)
        self.assertFalse(edited['freshness']['unlisted_files_checked'])
        self.assertFalse(edited['freshness']['atomic_filesystem_snapshot'])
        file.unlink()
        deleted = await self.health('left')
        self.assertEqual(deleted['freshness']['status'], 'differs_from_manifest')
        self.assertEqual(deleted['freshness']['counts']['missing'], 1)
        file.symlink_to(self.right / 'main.py')
        redirected = await self.health('left')
        self.assertEqual(redirected['freshness']['status'], 'unknown')
        self.assertEqual(redirected['freshness']['counts']['scope_changed'], 1)
        self.assertFalse(redirected['freshness']['checks_complete'])
        self.assertEqual((await self.search(workspace_id='left'))['results'], before['results'])

    async def test_common_health_missing_project_does_not_use_legacy_or_model(self):
        missing = await self.health('missing')
        self.assertEqual(missing['storage_backend'], 'embedded')
        self.assertEqual(missing['status'], 'not_published')

    async def test_native_chunk_corruption_is_refused_at_runtime_and_common_tool(self):
        self.assert_left_hits(await self.search(workspace_id='left'))
        owner = self.runtime._owner
        table = owner.vectors._table
        rows = await owner.vectors._call(lambda: table.search().where(
            "project_id = 'left'").to_list())
        self.assertTrue(rows)
        corrupted = []
        for row in rows:
            row = dict(row)
            row['content'] += '\n# corrupted-native-fixture'
            corrupted.append(row)

        def corrupt_private_fixture():
            from lancedb.index import FTS

            table.merge_insert('id').when_matched_update_all().when_not_matched_insert_all().execute(corrupted)
            table.create_index('content', config=FTS(), replace=True)

        await owner.vectors._call(corrupt_private_fixture)
        with self.assertRaisesRegex(RuntimeError, 'chunk content hash mismatch'):
            await self.runtime.workspace_search(['left'], 'authenticate', mode='text', k=20,
                                                exclude_tests=False)
        rejected = await self.search_tool.run({
            'workspace_id': 'left', 'query': 'authenticate', 'mode': 'text',
            'k': 20, 'exclude_tests': False})
        self.assertTrue(rejected.startswith('Error searching codebase:'))
        self.assertIn('chunk content hash mismatch', rejected)
        self.assertNotIn('corrupted-native-fixture', rejected)
        # Corruption in one project is not an outage or leak for another scope.
        foreign = await self.search(workspace_id='right')
        self.assertTrue(foreign['results'])
        self.assertTrue(all(row['project_id'] == 'right' for row in foreign['results']))

    async def test_unsupported_fallback_is_explicit_before_owner_or_model_access(self):
        with mock.patch.object(self.runtime, '_get_owner',
                               side_effect=AssertionError('unsupported option opened owner')):
            rejected = await self.search(workspace_id='left', fallback='grep')
        self.assertEqual(rejected['backend'], 'embedded')
        self.assertEqual(rejected['status'], 'unsupported-options')
        self.assertEqual(rejected['options'], ['fallback'])
        self.assertIsNone(self.runtime._owner)


if __name__ == '__main__':
    unittest.main()

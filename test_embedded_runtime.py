"""Offline runtime/MCP contract checks; no engines or network required."""
import asyncio
import unittest
from unittest import mock

from memory.embedded_runtime import EmbeddedRuntime
from tools.brain import embedded
from mcp.server.fastmcp import FastMCP


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_without_encoder_and_matching_identity(self):
        runtime = EmbeddedRuntime('/unused', 3)
        owner = mock.AsyncMock()
        owner.overview.return_value = {'retrieval': {'encoder_id': 'fixture'}}
        owner.search.return_value = [{'run_id': 'r'}]
        runtime._owner = owner
        with mock.patch.object(runtime, '_get_encoder', side_effect=AssertionError('model touched')):
            self.assertEqual(await runtime.search('p', 'query', 'text', 5), [{'run_id': 'r'}])
            await runtime.describe_file('p', 'a.py')
        encoder = mock.AsyncMock()
        encoder.encoder_id = 'different'
        with mock.patch.object(runtime, '_get_encoder', return_value=encoder):
            with self.assertRaises(ValueError):
                await runtime.search('p', 'query', 'vector', 5)
        encoder.embed_texts.assert_not_called()
        await runtime.close()
        owner.__aexit__.assert_awaited_once()
        with self.assertRaises(RuntimeError):
            await runtime.overview('p')

    async def test_close_waits_for_operation(self):
        runtime = EmbeddedRuntime('/unused', 3)
        owner = mock.AsyncMock()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def describe(*args, **kwargs):
            entered.set()
            await release.wait()
            return {'source': '1: original'}

        owner.describe_file.side_effect = describe
        runtime._owner = owner
        read = asyncio.create_task(runtime.describe_file('p', 'a.py'))
        await entered.wait()
        close = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        owner.__aexit__.assert_not_called()
        release.set()
        self.assertEqual((await read)['source'], '1: original')
        await close
        owner.__aexit__.assert_awaited_once()

    async def test_mcp_registration_and_dispatch(self):
        mcp = FastMCP('test')
        with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'neo4j',
                                             'LM_PROXY_GRAPH_BACKEND': '', 'LM_PROXY_EMBEDDED_STATE': ''}):
            embedded.register(mcp)
            self.assertEqual(mcp._tool_manager.list_tools(), [])
        with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded',
                                             'LM_PROXY_EMBEDDED_STATE': '/unused'}):
            embedded.register(mcp)
        self.assertEqual(len(mcp._tool_manager.list_tools()), 14)
        runtime = mock.AsyncMock()
        runtime.describe_file.return_value = {'run_id': 'r', 'source_sha256': 'hash', 'source': '1: original'}
        with mock.patch.object(embedded, 'get_embedded_runtime', return_value=runtime):
            expected = await embedded.describe_embedded_file('p', 'a.py')
            tool = mcp._tool_manager.get_tool('describe_embedded_file')
            self.assertEqual(await tool.run({'project_id': 'p', 'file_path': 'a.py'}), expected)
            runtime.describe_file.assert_awaited_with('p', 'a.py', start_line=1, max_lines=80, max_chars=12000)

    async def test_standard_workspace_tools_use_embedded_metadata(self):
        import json
        from tools.brain.graph import tools as graph_tools
        from tools.brain.search import graph_query
        mcp = FastMCP('workspace-routing')
        graph_tools.register(mcp)
        graph_query.register(mcp)
        runtime = mock.AsyncMock()
        runtime.resolve_project.return_value = {'project_id': 'p', 'workspace_path': '/source', 'run_id': 'r'}
        runtime.workspace_overview.return_value = {'project_id': 'p', 'files': 2, 'run_id': 'r'}
        with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded'}), \
                mock.patch('memory.embedded_runtime.get_embedded_runtime', return_value=runtime), \
                mock.patch.object(graph_query, 'get_project_id', side_effect=AssertionError('legacy hash used')):
            resolution = mcp._tool_manager.get_tool('resolve_graph_project')
            result = json.loads(await resolution.run({'workspace_id': '/source'}))
            self.assertEqual(result['project_id'], 'p')
            runtime.resolve_project.assert_awaited_with('/source')
            overview = mcp._tool_manager.get_tool('get_project_overview')
            self.assertEqual(json.loads(await overview.run({'workspace_id': '/source'}))['files'], 2)
            runtime.resolve_project.return_value = None
            self.assertIsNone(json.loads(await resolution.run({'workspace_id': '/missing'}))['project_id'])

    async def test_standard_symbol_context_routes_without_legacy_modules(self):
        import json
        from tools.brain.code_intel import core
        mcp = FastMCP('symbol-routing')
        core.register(mcp)
        runtime = mock.AsyncMock()
        runtime.workspace_symbol_context.return_value = {'status': 'published', 'run_id': 'r'}
        with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded'}), \
                mock.patch('memory.embedded_runtime.get_embedded_runtime', return_value=runtime), \
                mock.patch.object(core, 'get_memory_modules', side_effect=AssertionError('legacy store imported')):
            result = json.loads(await mcp._tool_manager.get_tool('get_symbol_context').run(
                {'workspace_id': '/source', 'symbol_name': 'helper'}))
            self.assertEqual(result['run_id'], 'r')
            runtime.workspace_symbol_context.assert_awaited_once()

    async def test_metadata_mcp_dispatch_preserves_preconditions(self):
        mcp = FastMCP('metadata-routing')
        with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded',
                                          'LM_PROXY_EMBEDDED_STATE': '/unused'}):
            embedded.register(mcp)
        runtime = mock.AsyncMock()
        runtime.project_metadata.return_value = {'status': 'conflict', 'revision': 2}
        with mock.patch.object(embedded, 'get_embedded_runtime', return_value=runtime):
            await mcp._tool_manager.get_tool('update_embedded_project_metadata').run(
                {'project_id': 'p', 'metadata': {'tags': ['code']},
                 'expected_revision': 1, 'expected_run_id': 'r'})
            runtime.project_metadata.assert_awaited_once_with(
                'p', metadata={'tags': ['code']}, expected_revision=1, expected_run_id='r')

    async def test_index_attempt_mcp_read_dispatch(self):
        mcp = FastMCP('attempt-routing')
        with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded',
                                          'LM_PROXY_EMBEDDED_STATE': '/unused'}):
            embedded.register(mcp)
        runtime = mock.AsyncMock()
        runtime.indexing_attempt.return_value = {'status': 'interrupted'}
        with mock.patch.object(embedded, 'get_embedded_runtime', return_value=runtime):
            await mcp._tool_manager.get_tool('get_embedded_indexing_attempt').run({'project_id': 'p'})
            runtime.indexing_attempt.assert_awaited_once_with('p')

    async def test_activity_mcp_dispatch_does_not_call_legacy_watcher(self):
        mcp = FastMCP('activity-routing')
        with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded',
                                          'LM_PROXY_EMBEDDED_STATE': '/unused'}):
            embedded.register(mcp)
        runtime = mock.AsyncMock()
        runtime.workspace_activity.return_value = {'status': 'published', 'revision': 1}
        with mock.patch.object(embedded, 'get_embedded_runtime', return_value=runtime), \
                mock.patch('graphrag_core.indexing.watcher.add_watch', side_effect=AssertionError('legacy worker intent')):
            await mcp._tool_manager.get_tool('set_embedded_watch_intent').run(
                {'project_id': 'p', 'requested': True, 'expected_revision': 0, 'expected_run_id': 'r'})
            runtime.workspace_activity.assert_awaited_once_with(
                'p', watch_requested=True, expected_revision=0, expected_run_id='r')

    async def test_global_close_allows_new_owner(self):
        from memory import embedded_runtime
        runtime = mock.AsyncMock()
        with mock.patch.object(embedded_runtime, "_runtime", runtime):
            await embedded_runtime.close_embedded_runtime()
            runtime.close.assert_awaited_once()
            self.assertIsNone(embedded_runtime._runtime)

    async def test_failed_encoder_connect_is_closed(self):
        runtime = EmbeddedRuntime('/unused', 3, __file__)
        encoder = mock.AsyncMock()
        encoder.connect.side_effect = RuntimeError('not loaded')
        with mock.patch('local_embeddings.strict_lmstudio.StrictLMStudioEncoder', return_value=encoder):
            with self.assertRaises(RuntimeError):
                await runtime._get_encoder()
        encoder.close.assert_awaited_once()
        self.assertIsNone(runtime._encoder)


if __name__ == '__main__':
    unittest.main()

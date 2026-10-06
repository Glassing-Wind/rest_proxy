"""Native Ladybug/LanceDB/ts-pack tests; synthetic vectors, no quality claims."""
import asyncio
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from graphrag_core.indexing.embedded_outlines import read_outline_publication
from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner
from memory.embedded_lance_runs import LanceRunStore


async def fixture_embed(texts):
    return [[1., 0., 0.] if 'authenticate' in text else [0., 1., 0.] for text in texts]


class PublishedRepository(unittest.IsolatedAsyncioTestCase):
    async def test_publication_failures_reopen_and_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            source = root / 'a.py'
            source.write_text('def authenticate(token):\n    return token\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                first = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed,
                                          encoder_id='fixture-v1', encoder_metadata={'model': 'fixture'})
                self.assertEqual(first['manifest']['retrieval']['encoder_metadata'], {'model': 'fixture'})
                self.assertEqual((await read_outline_publication(owner.graph, 'p'))['manifest']['retrieval']['encoder_metadata'], {'model': 'fixture'})
                await owner.index(str(root), 'q', ['a.py'], embed=fixture_embed, encoder_id='fixture-v1')
                for mode in ('text', 'vector', 'hybrid'):
                    hits = await owner.search('p', encoder_id='fixture-v1', text='authenticate',
                                              vector=[1., 0., 0.], mode=mode)
                    self.assertTrue(hits, mode)
                    self.assertTrue(all(h['run_id'] == first['run_id'] and h['project_id'] == 'p' for h in hits))
                    self.assertNotIn('vector', hits[0])
                with self.assertRaises(ValueError):
                    await owner.search('p', encoder_id='wrong-model', vector=[1., 0., 0.], mode='vector')
                source.write_text('def updated():\n    return 1\n')
                with mock.patch.object(owner.vectors._table, 'create_index',
                                       side_effect=RuntimeError('deliberate FTS failure')):
                    with self.assertRaises(RuntimeError):
                        await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture-v1')
                self.assertEqual((await read_outline_publication(owner.graph, 'p'))['run_id'], first['run_id'])
                self.assertTrue(await owner.search('p', encoder_id='fixture-v1', text='authenticate', mode='text'))
                with mock.patch('graphrag_core.indexing.embedded_repository.publish_outline_snapshot',
                                side_effect=RuntimeError('deliberate graph failure')):
                    with self.assertRaises(RuntimeError):
                        await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture-v1')
                self.assertEqual((await read_outline_publication(owner.graph, 'p'))['run_id'], first['run_id'])
                self.assertTrue(await owner.search('p', encoder_id='fixture-v1', text='authenticate', mode='text'))
                self.assertEqual(await owner.search('p', encoder_id='fixture-v1', text='updated', mode='text'), [])
                with mock.patch.object(owner.vectors, 'stage_run', side_effect=RuntimeError('deliberate vector failure')):
                    with self.assertRaises(RuntimeError):
                        await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture-v1')
                second = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture-v1')
                self.assertEqual(await owner.search('p', encoder_id='fixture-v1', text='authenticate', mode='text'), [])
                self.assertTrue(await owner.search('p', encoder_id='fixture-v1', text='updated', mode='text'))
                await owner.cleanup_unpublished('p')
                self.assertEqual(await owner.vectors.count('p', first['run_id']), 0)
                self.assertGreater(await owner.vectors.count('p', second['run_id']), 0)
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                self.assertTrue(await owner.search('p', encoder_id='fixture-v1', text='updated', mode='text'))
                self.assertEqual((await owner.describe_file('p', 'a.py'))['run_id'], second['run_id'])
                await owner.delete_project('p')
                self.assertIsNone(await owner.describe_file('p', 'a.py'))
                self.assertEqual(await owner.search('p', encoder_id='fixture-v1', text='updated', mode='text'), [])
                self.assertTrue(await owner.search('q', encoder_id='fixture-v1', text='authenticate', mode='text'))

    async def test_runtime_owner_routing_and_model_free_reopen(self):
        from memory.embedded_runtime import EmbeddedRuntime
        from tools.brain import embedded
        from mcp.server.fastmcp import FastMCP
        import graph_bootstrap

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            source.mkdir()
            (source / 'a.py').write_text('def authenticate(token):\n    return token\n')
            state = str(Path(directory) / 'state')
            runtime = EmbeddedRuntime(state, 3)
            encoder = mock.AsyncMock()
            encoder.encoder_id = 'fixture-v1'
            encoder.descriptor = {'model': 'fixture'}
            encoder.embed_texts.side_effect = fixture_embed
            runtime._encoder = encoder
            mcp = FastMCP('native-owner')
            with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded',
                                                'LM_PROXY_GRAPH_BACKEND': 'ladybug',
                                                'LM_PROXY_EMBEDDED_STATE': state}), \
                    mock.patch('memory.embedded_runtime._runtime', runtime), \
                    mock.patch.object(graph_bootstrap, '_driver', None):
                embedded.register(mcp)
                publication = await embedded.index_embedded_repository(str(source), 'p', ['a.py'])
                self.assertEqual((await embedded.get_embedded_overview('p'))['symbols'], 1)
                driver = await graph_bootstrap.require_driver()
                self.assertIs(driver, runtime._owner.graph)
                with self.assertRaises(RuntimeError):
                    async with EmbeddedRepositoryOwner(state, 3):
                        pass
                (source / 'a.py').write_text('def changed():\n    pass\n')
                expected = await embedded.describe_embedded_file('p', 'a.py')
                self.assertIn('authenticate', expected['source'])
                self.assertEqual(expected['run_id'], publication['run_id'])
                self.assertEqual(await mcp._tool_manager.get_tool('describe_embedded_file').run(
                    {'project_id': 'p', 'file_path': 'a.py'}), expected)
                await graph_bootstrap.close_graph_db()
                encoder.close.assert_awaited_once()
                self.assertIsNone(runtime._owner)
            reopened = EmbeddedRuntime(state, 3)
            with mock.patch.object(reopened, '_get_encoder', side_effect=AssertionError('model touched')):
                rows = await reopened.search('p', 'authenticate', 'text', 5)
                self.assertTrue(rows)
                self.assertEqual(rows[0]['run_id'], publication['run_id'])
                self.assertEqual(await reopened.describe_file('p', 'a.py'), expected)
            await reopened.close()

    async def test_durable_project_discovery_resolution_and_rollback(self):
        from graphrag_core.indexing.embedded_outlines import build_outline_snapshot, publish_outline_snapshot
        from memory.embedded_runtime import EmbeddedRuntime
        from tools.brain.graph import tools as graph_tools
        from tools.brain.search import graph_query
        from mcp.server.fastmcp import FastMCP
        import shutil

        with tempfile.TemporaryDirectory() as directory:
            left = Path(directory).resolve() / 'left' / 'shared'
            right = Path(directory).resolve() / 'right' / 'shared'
            for root in (left, right):
                root.mkdir(parents=True)
                (root / 'a.py').write_text('def authenticate(token):\n    return token\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                first = await owner.index(str(left), 'alpha', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                await owner.index(str(right), "beta'quoted", ['a.py'], embed=fixture_embed, encoder_id='fixture')
                page = await owner.list_projects(limit=1)
                self.assertEqual(page['projects'][0]['project_id'], 'alpha')
                self.assertEqual(page['next_cursor'], 'alpha')
                last = await owner.list_projects(limit=1, after=page['next_cursor'])
                self.assertEqual(last['projects'][0]['project_id'], "beta'quoted")
                self.assertIsNone(last['next_cursor'])
                self.assertEqual((await owner.resolve_project(str(left / '..' / 'shared')))['project_id'], 'alpha')
                self.assertEqual((await owner.resolve_project("beta'quoted"))['workspace_path'], str(right))
                self.assertIsNone(await owner.resolve_project(str(Path(directory) / 'missing' / 'shared')))
                self.assertIsNone(await owner.resolve_project('missing'))
                with self.assertRaises(ValueError):
                    await owner.resolve_project('shared')
                bad = build_outline_snapshot(str(right), 'alpha', ['a.py'])
                bad['files'][0]['symbols'].append(dict(bad['files'][0]['symbols'][0]))
                with self.assertRaises(Exception):
                    await publish_outline_snapshot(owner.graph, bad)
                resolved = await owner.resolve_project('alpha')
                self.assertEqual(resolved['run_id'], first['run_id'])
                self.assertEqual(resolved['workspace_path'], str(left))
            # Metadata resolves from durable receipts even after removing the source and reopening.
            shutil.rmtree(left)
            runtime = EmbeddedRuntime(state, 3)
            mcp = FastMCP('workspace-bridge')
            graph_tools.register(mcp)
            graph_query.register(mcp)
            with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded'}), \
                    mock.patch('memory.embedded_runtime.get_embedded_runtime', return_value=runtime), \
                    mock.patch.object(runtime, '_get_encoder', side_effect=AssertionError('model touched')):
                result = json.loads(await mcp._tool_manager.get_tool('resolve_graph_project').run(
                    {'workspace_id': str(left)}))
                self.assertEqual(result['project_id'], 'alpha')
                overview = json.loads(await mcp._tool_manager.get_tool('get_project_overview').run(
                    {'workspace_id': str(left)}))
                self.assertEqual(overview['run_id'], first['run_id'])
                self.assertEqual(overview['files'], 1)
                self.assertEqual(overview['workspace_path'], str(left))
                self.assertIn('published-source', overview['capabilities'])
                missing = json.loads(await mcp._tool_manager.get_tool('resolve_graph_project').run(
                    {'workspace_id': 'missing'}))
                self.assertIsNone(missing['project_id'])
                self.assertEqual(missing['status'], 'not_published')
                await runtime._owner.delete_project('alpha')
                self.assertIsNone(await runtime.resolve_project('alpha'))
                self.assertEqual((await runtime.list_projects())['projects'][0]['project_id'], "beta'quoted")
            await runtime.close()

    async def test_published_facts_citations_bounds_reopen_and_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            source = root / 'a.py'
            source.write_text('from util import helper\ndef caller():\n    return helper(helper())\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                publication = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                evidence = await owner.file_facts('p', 'a.py', limit=1)
                self.assertEqual(evidence['counts']['calls'], 2)
                self.assertEqual(len(evidence['facts']['calls']), 1)
                self.assertEqual(evidence['next_offset'], 1)
                page = await owner.file_facts('p', 'a.py', limit=1, offset=1)
                self.assertIsNone(page['next_offset'])
                self.assertGreater(page['facts']['calls'][0]['start_byte'], evidence['facts']['calls'][0]['start_byte'])
                self.assertIn('calls', evidence['truncated_groups'])
                self.assertEqual(evidence['facts']['calls'][0]['owner_name'], 'caller')
                self.assertEqual(evidence['run_id'], publication['run_id'])
                self.assertEqual(evidence['source_sha256'], publication['manifest']['files'][0]['sha256'])
                source.write_text('def changed():\n    pass\n')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                self.assertEqual(await owner.file_facts('p', 'a.py', limit=1), evidence)
                self.assertIsNone(await owner.file_facts('q', 'a.py'))
                with self.assertRaises(ValueError):
                    await owner.file_facts('p', 'a.py', limit=0)
                async with owner.graph.session() as session:
                    async def corrupt(tx):
                        await tx.run('MATCH (s:SourceEvidence {id:$fid}) SET s.facts_json=$facts',
                                     fid='p:file:a.py', facts='{}')
                    await session.execute_write(corrupt)
                with self.assertRaises(RuntimeError):
                    await owner.file_facts('p', 'a.py')
                await owner.delete_project('p')
                self.assertIsNone(await owner.file_facts('p', 'a.py'))

    async def test_relationship_publication_queries_rollback_reopen_and_delete(self):
        from graphrag_core.indexing.embedded_outlines import build_outline_snapshot, publish_outline_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            (root / 'util.py').write_text('def helper():\n    return 1\n')
            main = root / 'main.py'
            main.write_text('from util import helper as h\ndef caller():\n    return h() + h()\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                first = await owner.index(str(root), 'p', ['main.py', 'util.py'], embed=fixture_embed, encoder_id='fixture')
                calls = await owner.relationships('p', kind='calls', file_path='util.py', direction='in', limit=1)
                self.assertEqual(len(calls['relationships']), 1)
                self.assertIsNotNone(calls['next_cursor'])
                second = await owner.relationships('p', kind='calls', file_path='util.py', direction='in',
                                                   limit=1, after=calls['next_cursor'])
                self.assertIsNone(second['next_cursor'])
                self.assertNotEqual(calls['relationships'][0]['start_byte'], second['relationships'][0]['start_byte'])
                self.assertEqual(calls['run_id'], first['run_id'])
                context = await owner.symbol_context('p', 'helper', full_source=True)
                self.assertEqual(context['status'], 'published')
                self.assertEqual(len(context['callers']['relationships']), 2)
                self.assertIn('def helper', context['source']['source'])
                caller = await owner.symbol_context('p', 'caller', include_source=False)
                self.assertNotIn('source', caller)
                self.assertEqual(len(caller['callees']['relationships']), 2)
                self.assertEqual((await owner.symbol_context('p', 'missing'))['status'], 'symbol_not_published')
                imports = await owner.relationships('p', kind='imports')
                self.assertEqual(len(imports['relationships']), 1)
                self.assertEqual(imports['relationships'][0]['target_file'], 'util.py')
                self.assertIsNone(await owner.relationships('q'))
                # Actual transaction failure after deleting old data must restore old links/receipt.
                bad = build_outline_snapshot(str(root), 'p', ['main.py', 'util.py'])
                bad['files'][0]['symbols'].append(dict(bad['files'][0]['symbols'][0]))
                with self.assertRaises(Exception):
                    await publish_outline_snapshot(owner.graph, bad)
                self.assertEqual(await owner.relationships('p', kind='imports'), imports)
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                self.assertEqual(await owner.relationships('p', kind='imports'), imports)
                async with owner.graph.session() as session:
                    async def corrupt(tx):
                        await tx.run('MATCH (:File)-[r:EVIDENCE_LINK]->(:File) '
                                     'WHERE r.project_id=$project AND r.kind=$kind SET r.payload_json=$payload',
                                     project='p', kind='calls', payload='{}')
                    await session.execute_write(corrupt)
                with self.assertRaises(RuntimeError):
                    await owner.relationships('p')
                main.write_text('def caller():\n    return 2\n')
                await owner.index(str(root), 'p', ['main.py'], embed=fixture_embed, encoder_id='fixture')
                self.assertEqual((await owner.relationships('p'))['relationships'], [])
                await owner.delete_project('p')
                self.assertIsNone(await owner.relationships('p'))

    async def test_watch_setup_preview_readiness_preconditions_and_enable(self):
        from memory.embedded_runtime import EmbeddedRuntime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'source'
            root.mkdir()
            (root / 'a.py').write_text('def first():\n    return 1\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                pub = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
            runtime = EmbeddedRuntime(state, 3)
            try:
                with mock.patch.dict('os.environ', {'LM_PROXY_EMBEDDED_WATCH_ENABLED': '0'}):
                    blocked = await runtime.configure_project_watch('p', enable=True,
                        expected_revision=0, expected_run_id=pub['run_id'])
                    self.assertEqual(blocked['status'], 'blocked')
                    self.assertEqual(blocked['scope']['paths'], ['a.py'])
                    self.assertFalse((await runtime.workspace_activity('p'))['watch_requested'])
                encoder = mock.Mock(encoder_id='wrong', descriptor={'model': 'fixture'})
                encoder.embed_texts = mock.AsyncMock(side_effect=fixture_embed)
                encoder.close = mock.AsyncMock()
                runtime._encoder = encoder
                await runtime.start_watch(interval=30)
                with mock.patch.dict('os.environ', {'LM_PROXY_EMBEDDED_WATCH_ENABLED': '1'}):
                    mismatch = await runtime.configure_project_watch('p')
                    self.assertFalse(mismatch['ready'])
                    encoder.encoder_id = 'fixture'
                    preview = await runtime.configure_project_watch('p')
                    self.assertTrue(preview['ready'])
                    self.assertFalse((await runtime.workspace_activity('p'))['watch_requested'])
                    conflict = await runtime.configure_project_watch('p', enable=True,
                        expected_revision=1, expected_run_id=pub['run_id'])
                    self.assertEqual(conflict['status'], 'conflict')
                    enabled = await runtime.configure_project_watch('p', enable=True,
                        expected_revision=preview['revision'], expected_run_id=preview['run_id'])
                    self.assertEqual(enabled['status'], 'enabled')
                    self.assertTrue((await runtime.workspace_activity('p'))['watch_requested'])
                self.assertTrue(encoder.embed_texts.await_count > 0)
                self.assertTrue(all(call.args[0] == ['FIRE watch readiness probe']
                                    for call in encoder.embed_texts.call_args_list))
            finally:
                await runtime.close()

    async def test_watcher_shutdown_cancels_active_index_and_preserves_receipt(self):
        from memory.embedded_runtime import EmbeddedRuntime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'source'
            root.mkdir()
            file = root / 'a.py'
            file.write_text('def first():\n    return 1\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                pub = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                await owner.workspace_activity('p', watch_requested=True, expected_revision=0, expected_run_id=pub['run_id'])
            file.write_text('def changed():\n    return 2\n')
            entered = asyncio.Event()
            async def blocked(texts):
                entered.set()
                await asyncio.Event().wait()
            runtime = EmbeddedRuntime(state, 3)
            encoder = mock.Mock(encoder_id='fixture', descriptor={'model': 'fixture'})
            encoder.embed_texts = blocked
            encoder.close = mock.AsyncMock()
            runtime._encoder = encoder
            task = await runtime.start_watch(interval=1)
            try:
                await asyncio.wait_for(entered.wait(), 10)
            finally:
                await runtime.close()
            self.assertTrue(task.done())
            encoder.close.assert_awaited_once()
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                self.assertEqual((await owner.overview('p'))['run_id'], pub['run_id'])
                self.assertEqual((await owner.indexing_attempt('p'))['status'], 'cancelled')

    async def test_owned_watcher_content_changes_deletion_failure_and_shutdown(self):
        from memory.embedded_runtime import EmbeddedRuntime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'source'
            root.mkdir()
            file = root / 'a.py'
            file.write_text('def first():\n    return 1\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                pub = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                await owner.workspace_activity('p', watch_requested=True, expected_revision=0, expected_run_id=pub['run_id'])
            runtime = EmbeddedRuntime(state, 3)
            encoder = mock.Mock(encoder_id='fixture', descriptor={'model': 'fixture'})
            encoder.embed_texts = mock.AsyncMock(side_effect=fixture_embed)
            encoder.close = mock.AsyncMock()
            runtime._encoder = encoder
            try:
                await runtime.watch_tick()
                encoder.embed_texts.assert_not_called()
                (root / 'outside.py').write_text('def ignored():\n    pass\n')
                file.write_text('def changed():\n    return 2\n')
                await runtime.watch_tick()
                updated = await runtime.overview('p')
                self.assertNotEqual(updated['run_id'], pub['run_id'])
                self.assertEqual(updated['files'], 1)
                self.assertEqual((await runtime.indexing_attempt('p'))['status'], 'published')
                file.write_text('def failure():\n    return 3\n')
                encoder.embed_texts.side_effect = ValueError('fixture failure')
                await runtime.watch_tick()
                self.assertEqual((await runtime.overview('p'))['run_id'], updated['run_id'])
                self.assertEqual((await runtime.workspace_activity('p'))['last_watch_result'], 'ValueError')
                encoder.embed_texts.side_effect = fixture_embed
                task = await runtime.start_watch(interval=1)
                self.assertIs(task, await runtime.start_watch(interval=1))
                for _ in range(50):
                    if (await runtime.overview('p'))['run_id'] != updated['run_id']:
                        break
                    await asyncio.sleep(0.1)
                else:
                    self.fail('Background watcher did not publish changed content')
                self.assertTrue((await runtime.workspace_activity('p'))['embedded_watch_worker_active'])
                file.unlink()
                await runtime.watch_tick()
                self.assertEqual((await runtime.overview('p'))['files'], 0)
            finally:
                await runtime.close()
            self.assertTrue(task.done())
            encoder.close.assert_awaited_once()

    async def test_session_discovery_expiry_ambiguity_root_changes_and_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = [Path(directory) / name for name in ('one', 'two')]
            for root in roots:
                root.mkdir()
                (root / 'a.py').write_text('def first():\n    return 1\n')
            async with EmbeddedRepositoryOwner(str(Path(directory) / 'state'), 3) as owner:
                pubs = [await owner.index(str(root), name, ['a.py'], embed=fixture_embed, encoder_id='fixture')
                        for root, name in zip(roots, ('p', 'q'))]
                session_id = 'ide"quoted-é'
                with mock.patch('memory.embedded_activity.time.time_ns', return_value=1000000000000):
                    self.assertEqual((await owner.resolve_session(session_id))['status'], 'not_found')
                    await owner.workspace_activity('p', session_id=session_id, lease_seconds=60,
                        expected_revision=0, expected_run_id=pubs[0]['run_id'])
                    resolved = await owner.resolve_session(session_id)
                    self.assertEqual(resolved['project_id'], 'p')
                    self.assertEqual(resolved['workspace_path'], str(roots[0].resolve()))
                    await owner.workspace_activity('q', session_id=session_id, lease_seconds=60,
                        expected_revision=0, expected_run_id=pubs[1]['run_id'])
                    self.assertEqual((await owner.resolve_session(session_id))['status'], 'ambiguous')
                    await owner.delete_project('q')
                    self.assertEqual((await owner.resolve_session(session_id))['status'], 'resolved')
                with mock.patch('memory.embedded_activity.time.time_ns', return_value=1060000000000):
                    self.assertEqual((await owner.resolve_session(session_id))['status'], 'not_found')
                with mock.patch('memory.embedded_activity.time.time_ns', return_value=1000000000000):
                    await owner.index(str(roots[1]), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                    self.assertEqual((await owner.resolve_session(session_id))['status'], 'not_found')

    async def test_activity_leases_intent_conflicts_expiry_reopen_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            (root / 'a.py').write_text('def first():\n    return 1\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                pub = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                empty = await owner.workspace_activity('p')
                self.assertEqual(empty['revision'], 0)
                self.assertFalse(empty['watch_requested'])
                with mock.patch('memory.embedded_activity.time.time_ns', return_value=1000000000000):
                    intent = await owner.workspace_activity('p', watch_requested=True,
                        expected_revision=0, expected_run_id=pub['run_id'])
                    self.assertFalse(intent['embedded_watch_worker_active'])
                    lease = await owner.workspace_activity('p', session_id='client', lease_seconds=60,
                        expected_revision=1, expected_run_id=pub['run_id'])
                    self.assertEqual(lease['sessions'], {'client': 1060000})
                    stale = await owner.workspace_activity('p', watch_requested=False,
                        expected_revision=1, expected_run_id=pub['run_id'])
                    self.assertEqual(stale['status'], 'conflict')
                with mock.patch('memory.embedded_activity.time.time_ns', return_value=1060000000000):
                    expired = await owner.workspace_activity('p')
                    self.assertEqual(expired['sessions'], {})
                    self.assertTrue(expired['watch_requested'])
                    self.assertEqual(expired['revision'], 2)
                    released = await owner.workspace_activity('p', session_id='client', lease_seconds=0,
                        expected_revision=2, expected_run_id=pub['run_id'])
                    self.assertEqual(released['revision'], 3)
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                self.assertTrue((await owner.workspace_activity('p'))['watch_requested'])
                for i in range(32):
                    activity = await owner.workspace_activity('p', session_id=str(i),
                        expected_revision=3+i, expected_run_id=pub['run_id'])
                self.assertEqual(len(activity['sessions']), 32)
                renewed = await owner.workspace_activity('p', session_id='0',
                    expected_revision=35, expected_run_id=pub['run_id'])
                self.assertEqual(len(renewed['sessions']), 32)
                with self.assertRaises(ValueError):
                    await owner.workspace_activity('p', session_id='overflow',
                        expected_revision=36, expected_run_id=pub['run_id'])
                self.assertEqual((await owner.workspace_activity('p'))['revision'], 36)
                replacement = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                self.assertTrue((await owner.workspace_activity('p'))['watch_requested'])
                stale = await owner.workspace_activity('p', watch_requested=False,
                    expected_revision=35, expected_run_id=pub['run_id'])
                self.assertEqual(stale['status'], 'conflict')
                self.assertNotEqual(replacement['run_id'], pub['run_id'])
                await owner.index(str(root), 'q', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                await owner.delete_project('p')
                self.assertEqual((await owner.workspace_activity('p'))['status'], 'not_published')
                self.assertEqual((await owner.workspace_activity('q'))['sessions'], {})

    async def test_index_attempt_success_failure_cancellation_and_postcommit_error(self):
        from graphrag_core.indexing.embedded_outlines import publish_outline_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            (root / 'a.py').write_text('def first():\n    return 1\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                self.assertEqual((await owner.indexing_attempt('p'))['status'], 'no_attempt')
                pub = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                success = await owner.indexing_attempt('p')
                self.assertEqual(success['status'], 'published')
                self.assertEqual(success['attempt_id'], pub['attempt_id'])
                self.assertEqual(success['candidate_run_id'], success['published_run_id'])
                async def failed(texts):
                    raise ValueError('secret detail must not be journaled')
                with self.assertRaises(ValueError):
                    await owner.index(str(root), 'p', ['a.py'], embed=failed, encoder_id='fixture')
                failure = await owner.indexing_attempt('p')
                self.assertEqual(failure['status'], 'failed')
                self.assertEqual(failure['phase'], 'embedding')
                self.assertEqual(failure['error_type'], 'ValueError')
                self.assertNotIn('secret detail', json.dumps(failure))
                self.assertEqual(failure['published_run_id'], pub['run_id'])
                entered = asyncio.Event()
                async def blocked(texts):
                    entered.set()
                    await asyncio.Event().wait()
                task = asyncio.create_task(owner.index(str(root), 'p', ['a.py'], embed=blocked, encoder_id='fixture'))
                await asyncio.wait_for(entered.wait(), 10)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                cancelled = await owner.indexing_attempt('p')
                self.assertEqual(cancelled['status'], 'cancelled')
                self.assertEqual(cancelled['published_run_id'], pub['run_id'])
                async def committed_then_error(graph, snapshot):
                    await publish_outline_snapshot(graph, snapshot)
                    raise RuntimeError('delivery failed after commit')
                with mock.patch('graphrag_core.indexing.embedded_repository.publish_outline_snapshot', committed_then_error):
                    with self.assertRaises(RuntimeError):
                        await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                committed = await owner.indexing_attempt('p')
                self.assertEqual(committed['status'], 'published')
                self.assertEqual(committed['candidate_run_id'], committed['published_run_id'])
                self.assertEqual(committed['error_type'], '')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                self.assertEqual(await owner.indexing_attempt('p'), committed)
                await owner.delete_project('p')
                self.assertEqual((await owner.indexing_attempt('p'))['status'], 'no_attempt')

    async def test_metadata_revision_reopen_reindex_and_scoped_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            (root / 'a.py').write_text('def first():\n    return 1\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                pub = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                self.assertEqual((await owner.project_metadata('missing'))['status'], 'not_published')
                empty = await owner.project_metadata('p')
                self.assertEqual(empty['revision'], 0)
                first = await owner.project_metadata('p', metadata={'description': 'fixture', 'tags': ['code']},
                                                     expected_revision=0, expected_run_id=pub['run_id'])
                self.assertEqual(first['revision'], 1)
                self.assertFalse(first['repository_evidence_verified'])
                results = await asyncio.gather(*[owner.project_metadata('p', metadata={'winner': i},
                                                expected_revision=1, expected_run_id=pub['run_id']) for i in range(2)])
                self.assertEqual(sorted(r['status'] for r in results), ['conflict', 'published'])
                saved = await owner.project_metadata('p')
                self.assertEqual(saved['revision'], 2)
                async with owner.graph.session() as session:
                    async def rollback(tx):
                        await tx.run('MATCH (m:WorkspaceMetadata {id:$id}) DELETE m', id='p')
                        raise RuntimeError('injected failure')
                    with self.assertRaises(RuntimeError):
                        await session.execute_write(rollback)
                self.assertEqual(await owner.project_metadata('p'), saved)
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                self.assertEqual(await owner.project_metadata('p'), saved)
                new = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                self.assertEqual((await owner.project_metadata('p'))['metadata'], saved['metadata'])
                stale = await owner.project_metadata('p', metadata={}, expected_revision=2, expected_run_id=pub['run_id'])
                self.assertEqual(stale['status'], 'conflict')
                cleared = await owner.project_metadata('p', metadata={}, expected_revision=2, expected_run_id=new['run_id'])
                self.assertEqual(cleared['revision'], 3)
                self.assertEqual(cleared['metadata'], {})
                await owner.index(str(root), 'q', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                await owner.delete_project('p')
                self.assertEqual((await owner.project_metadata('p'))['status'], 'not_published')
                self.assertEqual((await owner.project_metadata('q'))['status'], 'published')
                again = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                self.assertEqual((await owner.project_metadata('p'))['revision'], 0)
                # A prior project lifetime's run ID cannot overwrite reused project IDs.
                self.assertNotEqual(again['run_id'], new['run_id'])
                self.assertEqual((await owner.project_metadata('p', metadata={}, expected_revision=0,
                                                              expected_run_id=new['run_id']))['status'], 'conflict')

    async def test_metadata_changed_root_hides_previous_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / 'state')
            roots = [Path(directory) / name for name in ('old', 'new')]
            for root in roots:
                root.mkdir()
                (root / 'a.py').write_text('def first():\n    return 1\n')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                pub = await owner.index(str(roots[0]), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                await owner.workspace_activity('p', watch_requested=True, expected_revision=0, expected_run_id=pub['run_id'])
                await owner.workspace_activity('p', session_id='old-client', expected_revision=1, expected_run_id=pub['run_id'])
                await owner.project_metadata('p', metadata={'private': 'old workspace'},
                                             expected_revision=0, expected_run_id=pub['run_id'])
                new = await owner.index(str(roots[1]), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                activity = await owner.workspace_activity('p')
                self.assertEqual(activity['status'], 'workspace_changed')
                self.assertFalse(activity['watch_requested'])
                self.assertEqual(activity['sessions'], {})
                hidden = await owner.project_metadata('p')
                self.assertEqual(hidden['status'], 'workspace_changed')
                self.assertEqual(hidden['metadata'], {})
                replaced = await owner.project_metadata('p', metadata={'new': True},
                                                       expected_revision=1, expected_run_id=new['run_id'])
                self.assertEqual(replaced['status'], 'published')
                self.assertEqual(replaced['metadata'], {'new': True})

    async def test_call_chain_native_cycles_and_standard_bridge(self):
        from memory.embedded_runtime import EmbeddedRuntime
        from tools.brain.code_intel import core
        from mcp.server.fastmcp import FastMCP
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'source'
            root.mkdir()
            (root / 'a.py').write_text('def first():\n    return second()\ndef second():\n    return first()\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                indexed = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture')
                chain = await owner.call_chain('p', 'first', depth=5)
                self.assertEqual(chain['run_id'], indexed['run_id'])
                self.assertEqual(len(chain['relationships']), 2)
                self.assertTrue(chain['relationships'][1]['revisits_symbol'])
                self.assertEqual(len((await owner.call_chain('p', 'first', depth=1))['relationships']), 1)
                self.assertEqual(len((await owner.call_chain('p', 'first', direction='up'))['relationships']), 2)
                self.assertEqual((await owner.call_chain('p', 'missing'))['status'], 'symbol_not_published')
            runtime = EmbeddedRuntime(state, 3)
            try:
                mcp = FastMCP('call-chain-bridge')
                core.register(mcp)
                with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded'}), \
                        mock.patch('memory.embedded_runtime.get_embedded_runtime', return_value=runtime), \
                        mock.patch.object(core, 'get_project_id', side_effect=AssertionError('legacy hash used')):
                    result = json.loads(await mcp._tool_manager.get_tool('get_call_chain').run(
                        {'workspace_id': str(root), 'symbol_name': 'first', 'file_path': str(root / 'a.py')}))
                    self.assertEqual(result['run_id'], indexed['run_id'])
                    self.assertEqual(len(result['relationships']), 2)
            finally:
                await runtime.close()

    async def test_standard_symbol_context_ambiguity_and_snapshot_source(self):
        from memory.embedded_runtime import EmbeddedRuntime
        from tools.brain.code_intel import core
        from mcp.server.fastmcp import FastMCP
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'source'
            root.mkdir()
            for file in ('a.py', 'b.py'):
                (root / file).write_text('def helper():\n    return 1\n')
            state = str(Path(directory) / 'state')
            runtime = EmbeddedRuntime(state, 3)
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                await owner.index(str(root), 'p', ['a.py', 'b.py'], embed=fixture_embed, encoder_id='fixture')
            mcp = FastMCP('symbol-bridge')
            core.register(mcp)
            with mock.patch.dict('os.environ', {'LM_PROXY_STORAGE_BACKEND': 'embedded'}), \
                    mock.patch('memory.embedded_runtime.get_embedded_runtime', return_value=runtime), \
                    mock.patch.object(core, 'get_project_id', side_effect=AssertionError('legacy hash used')):
                tool = mcp._tool_manager.get_tool('get_symbol_context')
                args = {'workspace_id': str(root), 'symbol_name': 'helper'}
                ambiguous = json.loads(await tool.run(args))
                self.assertEqual(ambiguous['status'], 'ambiguous')
                self.assertEqual(len(ambiguous['candidates']), 2)
                (root / 'a.py').write_text('def changed():\n    pass\n')
                context = json.loads(await tool.run(dict(args, file_path=str(root / 'a.py'))))
                self.assertEqual(context['status'], 'published')
                self.assertIn('def helper', context['source']['source'])
                self.assertEqual(context['semantics'], 'static-source-candidates')
            await runtime.close()

    async def test_kill_after_staging_preserves_published_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            source = root / 'a.py'
            source.write_text('def authenticate():\n    return 1\n')
            state = str(Path(directory) / 'state')
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                first = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture-v1')
            source.write_text('def updated():\n    return 2\n')
            code = """
import asyncio,json,sys
from unittest import mock
from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner
async def embed(texts):
    return [[0.,1.,0.] for text in texts]
async def paused(graph,snapshot):
    print(json.dumps({'staged_run':snapshot['run_id']}),flush=True)
    await asyncio.Event().wait()
async def main():
    async with EmbeddedRepositoryOwner(sys.argv[1],3) as owner:
        with mock.patch('graphrag_core.indexing.embedded_repository.publish_outline_snapshot',paused):
            await owner.index(sys.argv[2],'p',['a.py'],embed=embed,encoder_id='fixture-v1')
asyncio.run(main())
"""
            child = await asyncio.create_subprocess_exec(
                sys.executable, '-c', code, state, str(root),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                line = await asyncio.wait_for(child.stdout.readline(), 15)
                staged_run = json.loads(line)['staged_run']
            finally:
                if child.returncode is None:
                    child.kill()
                await child.communicate()
            async with EmbeddedRepositoryOwner(state, 3) as owner:
                self.assertEqual((await read_outline_publication(owner.graph, 'p'))['run_id'], first['run_id'])
                attempt = await owner.indexing_attempt('p')
                self.assertEqual(attempt['status'], 'interrupted')
                self.assertEqual(attempt['phase'], 'publishing')
                self.assertEqual(attempt['candidate_run_id'], staged_run)
                self.assertEqual(attempt['published_run_id'], first['run_id'])
                self.assertGreater(await owner.vectors.count('p', staged_run), 0)
                self.assertTrue(await owner.search('p', encoder_id='fixture-v1', text='authenticate', mode='text'))
                self.assertEqual(await owner.search('p', encoder_id='fixture-v1', text='updated', mode='text'), [])
                await owner.cleanup_unpublished('p')
                self.assertEqual(await owner.vectors.count('p', staged_run), 0)
                self.assertGreater(await owner.vectors.count('p', first['run_id']), 0)

    async def test_run_upserts_quoted_scopes_validation_and_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LanceRunStore(str(Path(directory) / 'vectors'), 3)
            project = "p' OR project_id='q"
            chunk = {'ref_id': 'c', 'file_path': "a'b.py", 'content': 'authenticate token',
                     'source_sha256': 'test-fixture', 'metadata': {}, 'vector': [1., 0., 0.]}
            try:
                await store.stage_run(project, 'r', [chunk])
                await store.stage_run(project, 'r', [{**chunk, 'content': 'updated authenticate token'}])
                await store.stage_run('q', 'r', [chunk])
                self.assertEqual(store.validate_vector([1e100, 0., 0.]), [1., 0., 0.])
                self.assertEqual(await store.count(project, 'r'), 1)
                hits = await store.search(project, 'r', text='authenticate', mode='text')
                self.assertEqual([hit['project_id'] for hit in hits], [project])
                self.assertEqual(await store.search(project, 'unpublished', text='authenticate', mode='text'), [])
                for vector in ([1., 0.], [float('nan'), 0., 1.], [0., 0., 0.]):
                    with self.assertRaises(ValueError):
                        await store.stage_run(project, 'bad', [{**chunk, 'vector': vector}])
                await store.cleanup(project, None)
                self.assertEqual(await store.count('q', 'r'), 1)
            finally:
                await store.close()
            with self.assertRaises(RuntimeError):
                LanceRunStore(str(Path(directory) / 'vectors'), 4)

    async def test_cancelled_embedding_preserves_published_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            (root / 'a.py').write_text('def authenticate():\n    return 1\n')
            async with EmbeddedRepositoryOwner(str(Path(directory) / 'state'), 3) as owner:
                first = await owner.index(str(root), 'p', ['a.py'], embed=fixture_embed, encoder_id='fixture-v1')
                ready = asyncio.Event()
                async def waiting(texts):
                    ready.set()
                    await asyncio.Event().wait()
                task = asyncio.create_task(owner.index(str(root), 'p', ['a.py'], embed=waiting, encoder_id='fixture-v1'))
                await asyncio.wait_for(ready.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual((await read_outline_publication(owner.graph, 'p'))['run_id'], first['run_id'])
                self.assertTrue(await owner.search('p', encoder_id='fixture-v1', text='authenticate', mode='text'))


if __name__ == '__main__':
    unittest.main()

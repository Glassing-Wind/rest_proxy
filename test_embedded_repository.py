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

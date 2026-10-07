"""Offline call-chain bounds, cycles and publication isolation; no services required."""
import unittest
from unittest import mock

from graphrag_core.indexing.embedded_symbols import call_chain


def root():
    return {'status': 'published', 'run_id': 'r', 'symbol': {'id': 'a'},
            'source_sha256': 'hash', 'call_resolution_scope': 'fixture'}


def page(links=(), run='r', cursor=None):
    return {'run_id': run, 'relationships': [dict(id=a + b, caller_id=a, callee_id=b)
                                            for a, b in links], 'next_cursor': cursor}


class CallChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycles_directions_and_depth(self):
        graph = {'a': page([('a', 'b')]), 'b': page([('b', 'a')])}
        async def read(*args, **kwargs):
            return graph[kwargs['symbol_id']]
        with mock.patch('graphrag_core.indexing.embedded_symbols.symbol_context', return_value=root()), \
                mock.patch('graphrag_core.indexing.embedded_symbols.read_relationships', side_effect=read) as reader:
            result = await call_chain(None, 'p', 'a', depth=5)
            self.assertEqual([edge['hop'] for edge in result['relationships']], [1, 2])
            self.assertTrue(result['relationships'][1]['revisits_symbol'])
            self.assertEqual(result['expanded_symbols'], 2)
            self.assertFalse(result['truncated'])
            shallow = await call_chain(None, 'p', 'a', depth=1)
            self.assertEqual(len(shallow['relationships']), 1)
            await call_chain(None, 'p', 'a', depth=1, direction='up')
            self.assertEqual(reader.call_args.kwargs['direction'], 'in')

    async def test_truncation_and_publication_change(self):
        with mock.patch('graphrag_core.indexing.embedded_symbols.symbol_context', return_value=root()), \
                mock.patch('graphrag_core.indexing.embedded_symbols.read_relationships', return_value=page(cursor='more')) as reader:
            result = await call_chain(None, 'p', 'a')
            self.assertEqual(result['truncation_reasons'], ['adjacency-limit-20'])
            reader.return_value = page(run='changed')
            with self.assertRaises(RuntimeError):
                await call_chain(None, 'p', 'a')
            reader.return_value = page([('a', 'x' * 45000)])
            result = await call_chain(None, 'p', 'a')
            self.assertEqual(result['relationships'], [])
            self.assertEqual(result['truncation_reasons'], ['output-byte-budget'])

    async def test_global_symbol_and_relationship_caps(self):
        async def fanout(*args, **kwargs):
            node = kwargs['symbol_id']
            return page([(node, node + str(i)) for i in range(20)])
        async def dense(*args, **kwargs):
            node = kwargs['symbol_id']
            return page([(node, str(i)) for i in range(20)])
        with mock.patch('graphrag_core.indexing.embedded_symbols.symbol_context', return_value=root()), \
                mock.patch('graphrag_core.indexing.embedded_symbols.read_relationships', side_effect=fanout) as reader:
            result = await call_chain(None, 'p', 'a', depth=5)
            self.assertEqual(result['discovered_symbols'], 64)
            self.assertIn('symbol-limit-64', result['truncation_reasons'])
            self.assertLessEqual(result['expanded_symbols'], 64)
            reader.side_effect = dense
            result = await call_chain(None, 'p', 'a', depth=5)
            self.assertEqual(len(result['relationships']), 128)
            self.assertIn('relationship-limit-128', result['truncation_reasons'])

    async def test_ambiguity_and_validation(self):
        with mock.patch('graphrag_core.indexing.embedded_symbols.symbol_context', return_value={'status': 'ambiguous'}), \
                mock.patch('graphrag_core.indexing.embedded_symbols.read_relationships') as reader:
            self.assertEqual((await call_chain(None, 'p', 'a'))['status'], 'ambiguous')
            reader.assert_not_called()
        for kwargs in ({'depth': 0}, {'depth': 6}, {'direction': 'sideways'}):
            with self.assertRaises(ValueError):
                await call_chain(None, 'p', 'a', **kwargs)


if __name__ == '__main__':
    unittest.main()

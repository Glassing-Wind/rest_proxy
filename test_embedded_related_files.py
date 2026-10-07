"""Offline related-file bounds and publication guards; no engines or services needed."""
import json
import unittest
from unittest import mock

from graphrag_core.indexing import embedded_related as related


class RelatedBounds(unittest.IsolatedAsyncioTestCase):
    async def test_missing_and_older_publications_are_explicit(self):
        with mock.patch.object(related, 'read_outline_file', mock.AsyncMock(return_value=None)):
            self.assertEqual((await related.related_files(None, 'p', 'a.py'))['status'], 'file_not_published')
        with mock.patch.object(related, 'read_outline_file', mock.AsyncMock(return_value={
                'run_id': 'r', 'source_sha256': 'h'})), \
                mock.patch.object(related, 'read_relationships', mock.AsyncMock(return_value={
                    'run_id': 'r', 'status': 'reindex-required-for-relationships-v1'})):
            result = await related.related_files(None, 'p', 'a.py')
            self.assertEqual(result['status'], 'reindex-required-for-relationships-v1')
            self.assertEqual(result['groups'], [])

    async def test_mixed_runs_are_refused(self):
        with mock.patch.object(related, 'read_outline_file', mock.AsyncMock(return_value={
                'run_id': 'r', 'source_sha256': 'h'})), \
                mock.patch.object(related, 'read_relationships', mock.AsyncMock(return_value={'run_id': 'other'})):
            with self.assertRaisesRegex(RuntimeError, 'publication'):
                await related.related_files(None, 'p', 'a.py')

    async def test_output_bounds_cursors_and_self_links(self):
        async def page(*args, **kwargs):
            return {'run_id': 'r', 'next_cursor': 'f' * 64, 'relationships': [
                {'id': f'{i:064x}', 'source_file': 'a.py', 'target_file': 'a.py' if i == 0 else 'b.py',
                 'expression': 'x' * 7000} for i in range(10)]}

        with mock.patch.object(related, 'read_outline_file', mock.AsyncMock(return_value={
                'run_id': 'r', 'source_sha256': 'h'})), \
                mock.patch.object(related, 'read_relationships', side_effect=page):
            result = await related.related_files(None, 'p', 'a.py')
            self.assertTrue(result['truncated'])
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 48000)
            self.assertEqual(len(result['groups']), 6)
            self.assertTrue(all(edge['source_file'] != edge['target_file']
                                for group in result['groups'] for edge in group['relationships']))
            self.assertTrue(any(group['next_cursor'] != 'f' * 64 for group in result['groups']))


if __name__ == '__main__':
    unittest.main()

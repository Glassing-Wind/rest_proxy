"""Offline import-overview output and old-publication checks; no services required."""
import hashlib
import json
import unittest
from unittest import mock

from graphrag_core.indexing import embedded_imports as imports


class ImportOverviewBounds(unittest.IsolatedAsyncioTestCase):
    async def test_missing_old_and_invalid_limit(self):
        driver = mock.MagicMock()
        with mock.patch.object(imports, 'read_outline_publication', mock.AsyncMock(return_value=None)):
            self.assertEqual((await imports.import_overview(driver, 'p'))['status'], 'not_published')
        for limit in (0, 101, True):
            with self.assertRaises(ValueError):
                await imports.import_overview(driver, 'p', limit=limit)
        old = {'run_id': 'r', 'manifest': {'files': [{'path': 'a.py', 'sha256': 'h'}]}}
        with mock.patch.object(imports, 'read_outline_publication', mock.AsyncMock(return_value=old)):
            self.assertEqual((await imports.import_overview(driver, 'p'))['status'],
                             'reindex-required-for-fact-contract-v1')
        driver.session.assert_not_called()

    async def test_byte_budget_and_missing_span_do_not_invent_citations(self):
        source = 'fixture'
        facts = json.dumps({'version': 1, 'imports': [{'source': 'm' * 400,
            'items': [f'name{i:03d}' + 'n' * 400 for i in range(100)]}]})
        source_hash = hashlib.sha256(source.encode()).hexdigest()
        fact_hash = hashlib.sha256(facts.encode()).hexdigest()
        publication = {'run_id': 'r', 'manifest': {'files': [
            {'path': 'a.py', 'sha256': source_hash, 'facts_sha256': fact_hash}]}}
        driver = mock.MagicMock()
        session = mock.AsyncMock()
        driver.session.return_value.__aenter__.return_value = session
        tx = mock.AsyncMock()

        async def run(query, **params):
            result = mock.AsyncMock()
            if 'source_chars' in query:
                result.data.return_value = [{'source_chars': len(source), 'fact_chars': len(facts), 'run_id': 'r'}]
            else:
                result.data.return_value = [{'content': source, 'facts': facts, 'sha256': source_hash, 'run_id': 'r'}]
            return result

        tx.run.side_effect = run
        async def execute(callback):
            return await callback(tx)

        session.execute_read.side_effect = execute
        with mock.patch.object(imports, 'read_outline_publication', mock.AsyncMock(return_value=publication)):
            result = await imports.import_overview(driver, 'p', limit=100)
        self.assertEqual(result['named_items'], 100)
        self.assertTrue(result['ranked_rows_truncated'])
        self.assertIn('output-byte-budget', result['truncation_reasons'])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 48000)
        self.assertIsNone(result['top_named_imports'][0]['citations'][0]['start_line'])
        self.assertFalse(result['resolved_symbol_edges'])


if __name__ == '__main__':
    unittest.main()

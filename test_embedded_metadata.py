"""Offline annotation validation; no native engines or network required."""
import unittest
from memory.embedded_metadata import project_metadata


class MetadataValidation(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_documents_refused_before_storage(self):
        for document in ([], {'a': float('nan')}, {'a': 'x' * 17000},
                         {str(i): i for i in range(51)}):
            with self.assertRaises((ValueError, TypeError)):
                await project_metadata(None, 'p', metadata=document, expected_revision=0, expected_run_id='r')

    async def test_writes_require_explicit_preconditions(self):
        for values in ({}, {'expected_revision': -1, 'expected_run_id': 'r'},
                       {'expected_revision': True, 'expected_run_id': 'r'}, {'expected_revision': 0}):
            with self.assertRaises(ValueError):
                await project_metadata(None, 'p', metadata={}, **values)


if __name__ == '__main__':
    unittest.main()

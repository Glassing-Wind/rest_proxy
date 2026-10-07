"""Offline activity validation; no model or native storage dependencies."""
import unittest
from memory.embedded_activity import workspace_activity
from tools.brain.embedded import refresh_embedded_session


class ActivityValidation(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_values_refused_before_storage(self):
        for values in ({'watch_requested': 1}, {'session_id': 'x' * 129},
                       {'session_id': 'x', 'lease_seconds': -1},
                       {'session_id': 'x', 'lease_seconds': 59},
                       {'session_id': 'x', 'lease_seconds': 3601}):
            with self.assertRaises(ValueError):
                await workspace_activity(None, 'p', expected_revision=0, expected_run_id='r', **values)

    async def test_preconditions_and_explicit_session_required(self):
        with self.assertRaises(ValueError):
            await workspace_activity(None, 'p', watch_requested=True)
        with self.assertRaises(ValueError):
            await workspace_activity(None, 'p', watch_requested=True, expected_revision=True, expected_run_id='r')
        with self.assertRaises(ValueError):
            await refresh_embedded_session('p', '', 0, 'r')


if __name__ == '__main__':
    unittest.main()

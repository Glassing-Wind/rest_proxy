"""End-to-end offline local subprocess worker/reviewer handoff acceptance."""
import unittest
from scripts.check_task_handoff import run_demo


class HandoffTests(unittest.TestCase):
    def test_fresh_finding_completes_after_process_reopens(self):
        receipt = run_demo()
        self.assertEqual(receipt['status'], 'completed')
        self.assertTrue(receipt['checkpoint_reopened'])
        self.assertTrue(receipt['review_reopened'])
        self.assertEqual(receipt['operation_count'], 8)
        self.assertFalse(receipt['inference_used'])

    def test_source_change_requests_correction_preserving_original(self):
        receipt = run_demo(True)
        self.assertEqual(receipt['status'], 'queued')
        self.assertFalse(receipt['source_hash_matches'])
        self.assertTrue(receipt['original_finding_retained'])


if __name__ == '__main__':
    unittest.main()

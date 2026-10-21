"""Offline exact-bundle advisory handoff acceptance."""
import copy
import tempfile
import unittest
from memory.task_registry import TaskRegistry
from scripts.task_review_export import review_bundle
from scripts.task_review_capture import capture_assessment


class CaptureReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = TaskRegistry(self.temp.name)
        task = self.registry.create('fixture', self.temp.name, 'Explain', [], ['read_source'])
        task = self.registry.claim('fixture', task['id'], 1, 'worker')
        self.task = self.registry.submit('fixture', task['id'], task['revision'],
            task['claim']['token'], {'answer': 'Synthetic', 'retained_evidence': [{'source': 'return 1'}]})
        self.bundle = review_bundle(self.task, 1)

    def test_reopen_exact_advisory_and_replay_rejection(self):
        result = capture_assessment(self.registry, 'fixture', self.bundle, 'Needs correction')
        saved = TaskRegistry(self.temp.name).get('fixture', self.task['id'])
        self.assertEqual(saved['status'], 'review_pending')
        self.assertEqual(saved['assessments'][0]['text'], 'Needs correction')
        self.assertEqual(saved['revision'], result['revision'])
        with self.assertRaises(ValueError):
            capture_assessment(self.registry, 'fixture', self.bundle, 'Replay')

    def test_edited_evidence_and_cancellation_reject_import(self):
        edited = copy.deepcopy(self.bundle)
        edited['finding']['answer'] = 'Altered'
        with self.assertRaises(ValueError):
            capture_assessment(self.registry, 'fixture', edited, 'Advice')
        self.registry.cancel('fixture', self.task['id'], self.task['revision'], 'Stopped')
        with self.assertRaises(ValueError):
            capture_assessment(self.registry, 'fixture', self.bundle, 'Advice')
        saved = self.registry.get('fixture', self.task['id'])
        self.assertNotIn('assessments', saved)
        self.assertEqual(saved['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()

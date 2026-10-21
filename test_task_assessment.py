"""Local advisory retention fixture; no models or external services."""
import tempfile
import unittest
from memory.task_registry import TaskRegistry


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = TaskRegistry(self.temp.name)
        task = self.registry.create('fixture', self.temp.name, 'Explain', [], ['read_source'])
        task = self.registry.claim('fixture', task['id'], 1, 'worker')
        self.task = self.registry.submit('fixture', task['id'], task['revision'],
                                         task['claim']['token'], {'answer': 'Fixture'})

    def record(self, revision=None, submission=1, text='Unsupported claim'):
        return self.registry.record_assessment('fixture', self.task['id'],
            revision or self.task['revision'], submission, 'apple-cloud', text)

    def test_reopen_retains_advice_without_approval(self):
        result = self.record()
        saved = TaskRegistry(self.temp.name).get('fixture', self.task['id'])
        self.assertEqual(saved['status'], 'review_pending')
        self.assertNotIn('reviews', saved)
        self.assertEqual(saved['submissions'], self.task['submissions'])
        self.assertEqual(saved['assessments'][0]['submission'], 1)
        self.assertIn('unverified advisory', saved['assessments'][0]['trust'])
        self.assertEqual(saved['revision'], result['revision'])

    def test_stale_or_invalid_advice_cannot_mutate_task(self):
        for args in [dict(revision=1), dict(submission=True), dict(submission=2),
                     dict(text=''), dict(text='x' * 8193)]:
            with self.assertRaises(ValueError):
                self.record(**args)
        self.assertEqual(self.registry.get('fixture', self.task['id']), self.task)


if __name__ == '__main__':
    unittest.main()

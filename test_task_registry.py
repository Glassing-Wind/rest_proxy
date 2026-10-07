"""Offline registry acceptance. No external services or inference required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory.task_registry import TaskRegistry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = str(Path(self.temp.name) / 'state')
        self.registry = TaskRegistry(self.state)
        self.task = self.registry.create('fixture', self.temp.name, 'Read source',
                                         [{'source': 'fixture.py', 'historical': True}], ['read_source'])

    def test_claim_checkpoint_reopen(self):
        task = self.registry.claim('fixture', self.task['id'], 1, 'worker')
        task = self.registry.checkpoint('fixture', task['id'], 2,
                                       task['claim']['token'], {'finding': 'pending review'})
        self.assertEqual(TaskRegistry(self.state).get('fixture', task['id']), task)
        self.assertEqual(task['revision'], 3)
        self.assertEqual(task['evidence'], self.task['evidence'])

    def test_second_claim_rejected(self):
        task = self.registry.claim('fixture', self.task['id'], 1, 'worker')
        with self.assertRaises(ValueError):
            self.registry.claim('fixture', task['id'], 2, 'other')
        self.assertEqual(self.registry.get('fixture', task['id']), task)

    def test_scope_and_capability(self):
        with self.assertRaises(KeyError):
            self.registry.get('other', self.task['id'])
        with self.assertRaises(ValueError):
            self.registry.create('fixture', self.temp.name, 'Write', [], ['write_source'])

    def test_stale_expired_and_wrong_token(self):
        task = self.registry.claim('fixture', self.task['id'], 1, 'worker')
        for revision, token in [(1, task['claim']['token']), (2, 'wrong')]:
            with self.assertRaises(ValueError):
                self.registry.checkpoint('fixture', task['id'], revision, token, {})
        with patch('memory.task_registry.time.time', return_value=task['claim']['expires_at'] + 1):
            with self.assertRaises(ValueError):
                self.registry.checkpoint('fixture', task['id'], 2, task['claim']['token'], {})
        self.assertEqual(self.registry.get('fixture', task['id']), task)

    def test_oversize_checkpoint_rolls_back(self):
        task = self.registry.claim('fixture', self.task['id'], 1, 'worker')
        with self.assertRaises(ValueError):
            self.registry.checkpoint('fixture', task['id'], 2, task['claim']['token'], {'x': 'x'*70000})
        self.assertEqual(self.registry.get('fixture', task['id']), task)

    def test_cancellation_survives_restart_and_rejects_worker(self):
        task = self.registry.claim('fixture', self.task['id'], 1, 'worker')
        cancelled = self.registry.cancel('fixture', task['id'], 2, 'User stopped task')
        registry = TaskRegistry(self.state)
        self.assertEqual(registry.get('fixture', task['id']), cancelled)
        with self.assertRaises(ValueError):
            registry.checkpoint('fixture', task['id'], 3, task['claim']['token'], {})
        with patch('memory.task_registry.time.time', return_value=task['claim']['expires_at'] + 1):
            with self.assertRaises(ValueError):
                registry.reclaim('fixture', task['id'], 3, 'other', 'Expired')
        self.assertEqual(registry.get('fixture', task['id']), cancelled)

    def test_reclaim_preserves_evidence_and_fences_old_worker(self):
        task = self.registry.claim('fixture', self.task['id'], 1, 'worker')
        old_token = task['claim']['token']
        task = self.registry.checkpoint('fixture', task['id'], 2, old_token, {'finding': 'partial'})
        with self.assertRaises(ValueError):
            self.registry.reclaim('fixture', task['id'], 3, 'other', 'Still active')
        with patch('memory.task_registry.time.time', return_value=task['claim']['expires_at'] + 1):
            task = TaskRegistry(self.state).reclaim('fixture', task['id'], 3, 'other', 'Restart recovery')
            with self.assertRaises(ValueError):
                self.registry.checkpoint('fixture', task['id'], 4, old_token, {})
            updated = self.registry.checkpoint('fixture', task['id'], 4, task['claim']['token'],
                                               {'finding': 'continued'})
        self.assertEqual(task['checkpoint'], {'finding': 'partial'})
        self.assertEqual(task['evidence'], self.task['evidence'])
        self.assertEqual(task['attempts'], 2)
        self.assertEqual(updated['checkpoint'], {'finding': 'continued'})
        self.assertEqual(task['claim_history'][0]['worker'], 'worker')

    def test_reclaim_attempt_limit_and_stale_revision(self):
        task = self.registry.claim('fixture', self.task['id'], 1, 'worker')
        while task['attempts'] < 5:
            with patch('memory.task_registry.time.time', return_value=task['claim']['expires_at'] + 1):
                task = self.registry.reclaim('fixture', task['id'], task['revision'], 'other', 'Expired')
        with patch('memory.task_registry.time.time', return_value=task['claim']['expires_at'] + 1):
            with self.assertRaisesRegex(ValueError, 'attempt limit'):
                self.registry.reclaim('fixture', task['id'], task['revision'], 'other', 'Expired')
        with self.assertRaisesRegex(ValueError, 'Stale'):
            self.registry.cancel('fixture', task['id'], 1, 'Stale request')
        self.assertEqual(self.registry.get('fixture', task['id']), task)

    def submit_fixture(self):
        task = self.registry.claim('fixture', self.task['id'], 1, 'worker')
        return self.registry.submit('fixture', task['id'], 2, task['claim']['token'],
                                    {'answer': 'original', 'source': 'fixture.py'})

    def test_review_completion_reopens_and_is_terminal(self):
        task = self.submit_fixture()
        self.assertIsNone(task['claim'])
        with self.assertRaises(ValueError):
            self.registry.review('fixture', task['id'], 3, 'worker', 'accept', 'Self review')
        task = self.registry.review('fixture', task['id'], 3, 'reviewer', 'accept', 'Checked evidence')
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(TaskRegistry(self.state).get('fixture', task['id']), task)
        for operation in [
            lambda: self.registry.cancel('fixture', task['id'], 4, 'Stop'),
            lambda: self.registry.claim('fixture', task['id'], 4, 'worker'),
            lambda: self.registry.review('fixture', task['id'], 4, 'reviewer', 'accept', 'Again'),
        ]:
            with self.assertRaises(ValueError):
                operation()

    def test_correction_preserves_original_and_fences_old_claim(self):
        task = self.submit_fixture()
        task = self.registry.review('fixture', task['id'], 3, 'reviewer',
                                    'request_correction', 'Check source again')
        task = self.registry.claim('fixture', task['id'], 4, 'worker2')
        task = self.registry.submit('fixture', task['id'], 5, task['claim']['token'],
                                    {'answer': 'corrected'})
        task = self.registry.review('fixture', task['id'], 6, 'reviewer', 'accept', 'Verified')
        self.assertEqual([s['finding']['answer'] for s in task['submissions']],
                         ['original', 'corrected'])
        self.assertEqual([r['decision'] for r in task['reviews']], ['request_correction', 'accept'])
        self.assertEqual(task['reviews'][-1]['submission'], 2)

    def test_cancel_review_rejects_late_acceptance(self):
        task = self.submit_fixture()
        task = self.registry.cancel('fixture', task['id'], 3, 'Stop pending review')
        with self.assertRaises(ValueError):
            self.registry.review('fixture', task['id'], 4, 'reviewer', 'accept', 'Late')
        self.assertEqual(self.registry.get('fixture', task['id']), task)

    def test_submit_expiry_wrong_token_and_stale_review(self):
        task = self.registry.claim('fixture', self.task['id'], 1, 'worker')
        with self.assertRaises(ValueError):
            self.registry.submit('fixture', task['id'], 2, 'wrong', {'answer': 'bad'})
        with patch('memory.task_registry.time.time', return_value=task['claim']['expires_at']):
            with self.assertRaises(ValueError):
                self.registry.submit('fixture', task['id'], 2, task['claim']['token'], {'answer': 'late'})
        task = self.registry.submit('fixture', task['id'], 2, task['claim']['token'], {'answer': 'ok'})
        with self.assertRaises(ValueError):
            self.registry.review('fixture', task['id'], 2, 'reviewer', 'accept', 'Stale')
        self.assertEqual(self.registry.get('fixture', task['id']), task)


if __name__ == '__main__':
    unittest.main()

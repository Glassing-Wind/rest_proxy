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


if __name__ == '__main__':
    unittest.main()

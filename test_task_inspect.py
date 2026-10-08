"""Sanitized status acceptance; private temporary state, no services required."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from memory.task_registry import TaskRegistry


class InspectTests(unittest.TestCase):
    def test_failure_and_expiry_without_private_data_or_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            registry = TaskRegistry(str(Path(root) / 'state'))
            task = registry.create('fixture', root, 'private-goal', [], ['read_source'])
            with patch('memory.task_registry.time.time', return_value=100):
                task = registry.claim('fixture', task['id'], 1, 'private-worker', lease_seconds=1)
                task = registry.checkpoint('fixture', task['id'], task['revision'],
                                           task['claim']['token'], {'source': 'private-source'})
                task = registry.record_failure('fixture', task['id'], task['revision'],
                                               task['claim']['token'], 'timeout')
            with patch('memory.task_registry.time.time', return_value=102):
                result = registry.inspect('fixture', task['id'])
            self.assertTrue(result['claim_expired'])
            self.assertEqual(result['failures'][0]['category'], 'timeout')
            self.assertFalse(result['failure_history_complete'])
            for secret in ['private-goal', 'private-worker', 'private-source', task['claim']['token'], root]:
                self.assertNotIn(secret, json.dumps(result))
            self.assertEqual(registry.get('fixture', task['id']), task)
            output = subprocess.run([sys.executable, '-m', 'scripts.task_local',
                '--state', str(Path(root) / 'state')], input=json.dumps(dict(operation='inspect',
                arguments=dict(project='fixture', task_id=task['id']))),
                text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(output.stdout)['result']['revision'], task['revision'])

    def test_empty_history_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as root:
            registry = TaskRegistry(root)
            task = registry.create('fixture', root, 'Explain', [], ['read_source'])
            result = registry.inspect('fixture', task['id'])
            self.assertEqual(result['failures'], [])
            self.assertFalse(result['claim_present'])
            self.assertIn('absence is not success', result['note'])


if __name__ == '__main__':
    unittest.main()

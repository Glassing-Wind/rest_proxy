"""Offline local source dispatch and subprocess interface acceptance."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from memory.task_dispatch import TaskDispatcher
from memory.task_registry import TaskRegistry


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'repo'
        self.workspace.mkdir()
        self.source = self.workspace / 'fixture.py'
        self.source.write_text('first\nsecond\nthird\n')
        self.registry = TaskRegistry(str(self.root / 'state'))
        task = self.registry.create('fixture', str(self.workspace), 'Read', [], ['read_source'])
        self.task = self.registry.claim('fixture', task['id'], 1, 'worker')
        self.dispatch = TaskDispatcher(self.registry)

    def read(self, path='fixture.py', **kwargs):
        return self.dispatch.read_source('fixture', self.task['id'], 2,
                                        self.task['claim']['token'], path, **kwargs)

    def test_bounded_source_and_hash(self):
        result = self.read(start_line=2, end_line=3)
        self.assertEqual(result['source'], '2: second\n3: third')
        self.assertEqual(result['sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_traversal_hidden_absolute_and_symlink_denied(self):
        (self.workspace / 'link.py').symlink_to(self.source)
        (self.workspace / 'linked').symlink_to(self.workspace, target_is_directory=True)
        for path in ['../fixture.py', str(self.source), '.env', 'link.py', 'linked/fixture.py']:
            with self.assertRaises((ValueError, OSError)):
                self.read(path)

    def test_cancelled_claim_cannot_read(self):
        self.registry.cancel('fixture', self.task['id'], 2, 'Stop')
        with self.assertRaises(ValueError):
            self.read()

    def test_binary_size_and_output_limits(self):
        for data in [b'\0', b'x' * 1048577, b'x' * 17000]:
            self.source.write_bytes(data)
            with self.assertRaises(ValueError):
                self.read()

    def test_scope_token_and_line_limits(self):
        with self.assertRaises(KeyError):
            self.dispatch.read_source('other', self.task['id'], 2, self.task['claim']['token'], 'fixture.py')
        with self.assertRaises(ValueError):
            self.dispatch.read_source('fixture', self.task['id'], 2, 'wrong', 'fixture.py')
        for start, end in [(0, 1), (1, 201), (5, 6)]:
            with self.assertRaises(ValueError):
                self.read(start_line=start, end_line=end)

    def test_local_interface_reopens_state_and_returns_json(self):
        request = {'operation': 'read_source', 'arguments': dict(
            project='fixture', task_id=self.task['id'], revision=2,
            claim_token=self.task['claim']['token'], path='fixture.py')}
        result = subprocess.run([sys.executable, '-m', 'scripts.task_local', '--state',
                                 str(self.root / 'state')], input=json.dumps(request),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        request['operation'] = 'connect'
        result = subprocess.run([sys.executable, '-m', 'scripts.task_local', '--state',
                                 str(self.root / 'state')], input=json.dumps(request),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(self.task['claim']['token'], result.stdout)


if __name__ == '__main__':
    unittest.main()

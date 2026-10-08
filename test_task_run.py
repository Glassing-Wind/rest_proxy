"""CLI permission check; no live provider used."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RunTests(unittest.TestCase):
    def test_no_execute_creates_no_state_or_request(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            result = subprocess.run([sys.executable, '-m', 'scripts.task_run',
                '--state', str(state), '--project', 'fixture', '--task-id', 'fixture',
                '--revision', '1', '--path', 'fixture.py', '--endpoint',
                'http://127.0.0.1:1/v1/chat/completions', '--model', 'fixture'],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('Require --execute', result.stderr)
            self.assertFalse(state.exists())


if __name__ == '__main__':
    unittest.main()

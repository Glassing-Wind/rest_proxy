"""Offline disposable Shortcut intake checks; no Siri or network access."""
import tempfile
import unittest
from pathlib import Path
from scripts.task_capture import capture
from memory.task_registry import TaskRegistry


class CaptureTests(unittest.TestCase):
    def test_literal_input_reopens_as_queued_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / 'state')
            goal = 'Inspect $(touch /tmp/never-execute) and `commands`'
            task = capture(goal, state, directory)
            saved = TaskRegistry(state).get('rest_proxy', task['id'])
            self.assertEqual(saved['goal'], goal)
            self.assertEqual(saved['status'], 'queued')
            self.assertEqual(saved['actions'], ['read_source'])
            self.assertIsNone(saved['claim'])

    def test_invalid_request_creates_no_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            for value in ['', '  ', 'x' * 4097]:
                with self.assertRaises(ValueError):
                    capture(value, str(state), directory)
            self.assertFalse(state.exists())


if __name__ == '__main__':
    unittest.main()

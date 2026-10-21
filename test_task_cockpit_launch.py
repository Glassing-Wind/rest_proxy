"""Offline launch acceptance; only owned synthetic state and ephemeral loopback ports.

No environment variables or external services required. Never starts/stops MCP.
Run with .venv/bin/python test_task_cockpit_launch.py.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from memory.task_registry import TaskRegistry
from scripts.task_cockpit import _runtime


ROOT = Path(__file__).resolve().parent


class TaskCockpitLaunchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.runtime = self.root / 'runtime'

    def cli(self, *args):
        return [sys.executable, '-m', 'scripts.task_cockpit', '--runtime', str(self.runtime), *args]

    def test_runtime_refuses_public_and_symlink_directories_without_repair(self):
        self.runtime.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            _runtime(str(self.runtime))
        self.assertEqual(self.runtime.stat().st_mode & 0o777, 0o755)
        link = self.root / 'linked'
        link.symlink_to(self.runtime)
        with self.assertRaises(ValueError):
            _runtime(str(link))

    def test_missing_registry_is_not_created_and_error_is_sanitized(self):
        state = self.root / 'missing-private-state'
        result = subprocess.run(self.cli('--state', str(state), '--project', 'fixture', '--task', 'absent'),
                                cwd=ROOT, capture_output=True, text=True, timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(state.exists())
        self.assertFalse((self.runtime / 'launch.json').exists())
        self.assertNotIn(str(state), result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_existing_launch_is_preserved_without_creating_demo(self):
        self.runtime.mkdir(mode=0o700)
        path = self.runtime / 'launch.json'
        path.write_text('existing owner')
        result = subprocess.run(self.cli('--demo'), cwd=ROOT, capture_output=True, text=True, timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), 'existing owner')
        self.assertEqual(list(self.runtime.iterdir()), [path])

    def test_state_must_be_absolute_and_separate_before_runtime_creation(self):
        state = self.root / 'existing-state'
        state.mkdir(mode=0o700)
        nested = state / 'nested-launch'
        result = subprocess.run(
            [sys.executable, '-m', 'scripts.task_cockpit', '--runtime', str(nested),
             '--state', str(state), '--project', 'fixture', '--task', 'fixture'],
            cwd=ROOT, capture_output=True, text=True, timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(state.iterdir()), [])
        result = subprocess.run(self.cli('--state', 'relative-state', '--project', 'fixture', '--task', 'fixture'),
                                cwd=ROOT, capture_output=True, text=True, timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.runtime.exists())

    def test_incompatible_sqlite_error_is_sanitized_without_state_changes(self):
        state = self.root / 'incompatible-state'
        state.mkdir(mode=0o700)
        database = state / 'tasks.sqlite3'
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE unrelated (value TEXT)')
        os.chmod(database, 0o600)
        before = database.read_bytes()
        result = subprocess.run(self.cli('--state', str(state), '--project', 'fixture', '--task', 'fixture'),
                                cwd=ROOT, capture_output=True, text=True, timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('unrelated', result.stderr)
        self.assertNotIn(str(state), result.stderr)
        self.assertEqual(database.read_bytes(), before)
        self.assertFalse((self.runtime / 'launch.json').exists())

    def test_demo_cannot_select_existing_state(self):
        result = subprocess.run(self.cli('--demo', '--state', str(self.root / 'not-created')),
                                cwd=ROOT, capture_output=True, text=True, timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.runtime.exists())

    def test_owned_demo_reads_refreshes_and_sigterm_cleans_only_capability(self):
        child = subprocess.Popen(self.cli('--demo'), cwd=ROOT, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        opener = build_opener(ProxyHandler({}))
        launch = self.runtime / 'launch.json'
        try:
            deadline = time.monotonic() + 8
            ready = False
            while time.monotonic() < deadline and child.poll() is None:
                if launch.is_file():
                    try:
                        record = json.loads(launch.read_text())
                        url = urlsplit(record['url'])
                        base = f'{url.scheme}://{url.netloc}'
                        with opener.open(base + '/live', timeout=0.25) as response:
                            ready = json.load(response)['surface'] == 'local-task-cockpit'
                        if ready:
                            break
                    except (ValueError, OSError, URLError):
                        pass
                time.sleep(0.02)
            self.assertTrue(ready, 'Only the owned demo must start within the bound')
            self.assertEqual(launch.stat().st_mode & 0o777, 0o600)
            self.assertEqual(record['pid'], child.pid)
            token = url.fragment.removeprefix('token=')
            with self.assertRaises(HTTPError) as error:
                opener.open(base + '/api/v1/task-status', timeout=2)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()

            def read_status():
                request = Request(base + '/api/v1/task-status', headers={'Authorization': 'Bearer ' + token})
                with opener.open(request, timeout=2) as response:
                    return json.load(response)

            first = read_status()
            self.assertEqual(first['status'], 'queued')
            self.assertTrue(first['read_only'])
            registry_directory = next(self.runtime.glob('synthetic-*/registry'))
            writer = TaskRegistry(str(registry_directory))
            writer.claim(first['project'], first['task_id'], first['revision'], 'synthetic-worker')
            second = read_status()
            self.assertEqual(second['status'], 'claimed')
            self.assertGreater(second['revision'], first['revision'])
            self.assertNotIn('goal', second)
            self.assertNotIn('workspace', second)
            self.assertNotIn('token', json.dumps(second))
            request = Request(base + '/api/v1/task-status',
                              headers={'Authorization': 'Bearer ' + token, 'X-Forwarded-For': '203.0.113.8'})
            with opener.open(request, timeout=2) as response:
                self.assertEqual(json.load(response)['status'], 'claimed')
            child.terminate()
            stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 143, stderr)
            self.assertNotIn(token, stdout + stderr)
            self.assertFalse(launch.exists())
            self.assertTrue(writer.path.is_file())
            self.assertEqual(writer.inspect(first['project'], first['task_id'])['status'], 'claimed')
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()

"""Offline startup failure tests; stub executables, no Docker or databases required."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class EntrypointTests(unittest.TestCase):
    def run_entrypoint(self, reachable, bootstrap):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'nc').write_text(f'#!/bin/sh\nexit {0 if reachable else 1}\n')
            (path / 'python').write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$PROBE_LOG"\n'
                f'if [ "$1" = mcp_server.py ]; then exit {bootstrap}; fi\nexit 0\n')
            for executable in ('nc', 'python'):
                (path / executable).chmod(0o755)
            log = path / 'calls'
            env = {**os.environ, 'PATH': directory + ':' + os.environ['PATH'],
                   'PROBE_LOG': str(log), 'LM_PROXY_STARTUP_TIMEOUT_SECONDS': '0',
                   'LM_PROXY_MEMORY_ENABLE_REDIS': '0'}
            result = subprocess.run(['bash', str(ROOT / 'scripts/docker_entrypoint.sh')],
                                    env=env, capture_output=True, text=True, timeout=5)
            return result, log.read_text() if log.exists() else ''

    def test_unreachable_database_exits_without_launch(self):
        result, calls = self.run_entrypoint(False, 0)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Timed out', result.stderr)
        self.assertEqual(calls, '')

    def test_failed_bootstrap_does_not_launch(self):
        result, calls = self.run_entrypoint(True, 1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('uvicorn', calls)

    def test_success_binds_container_health_port(self):
        result, calls = self.run_entrypoint(True, 0)
        self.assertEqual(result.returncode, 0)
        self.assertIn('brain_server:app --host 0.0.0.0 --port 8000', calls)


if __name__ == '__main__':
    unittest.main()

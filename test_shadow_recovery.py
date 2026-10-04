"""Offline local-owner proof checks; live termination/recovery fixtures are in test_shadow_lifecycle.py."""

import os
import socket
import unittest
import importlib.util
import tempfile
import sys
from pathlib import Path
from unittest import mock

from graphrag_core.indexing.shadow_recovery import require_dead_local_owner
from graphrag_core.indexing.shadow_admin import snapshot_digest


class LocalOwnerProofTests(unittest.TestCase):
    def test_apply_cli_claims_lock_and_saves_private_preview(self):
        import _jobs
        spec = importlib.util.spec_from_file_location('owned_recovery_cli', 'scripts/recover_owned_shadow.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        preview = {'namespace': 'repo::shadow::repo:run',
                   'owner': {'project_id': 'repo', 'pid': 123},
                   'staging': {'nodes': [], 'relationships': []}}
        session = mock.MagicMock()
        session.execute_read.return_value = preview
        session.execute_write.side_effect = lambda callback: callback(mock.Mock())
        driver = mock.MagicMock()
        driver.session.return_value.__enter__.return_value = session
        with tempfile.TemporaryDirectory() as root:
            module.ROOT = Path(root)
            with (mock.patch.object(module, 'load_dotenv'),
                  mock.patch.object(module.GraphDatabase, 'driver') as factory,
                  mock.patch.object(module, 'check_jobs'),
                  mock.patch.object(module, 'recover_owned_shadow', return_value={'data_deleted': False}),
                  mock.patch.object(_jobs, 'claim_project_job_lock', return_value=(True, None)) as claim,
                  mock.patch.object(_jobs, '_release_project_job_lock') as release,
                  mock.patch.dict(os.environ, {'LM_PROXY_NEO4J_URI': 'unused',
                                               'LM_PROXY_NEO4J_USER': 'unused',
                                               'LM_PROXY_NEO4J_PASSWORD': 'unused'}),
                  mock.patch.object(sys, 'argv', ['recover_owned_shadow.py', '--namespace', preview['namespace'],
                                                 '--apply', '--expected-digest', snapshot_digest(preview)]),
                  mock.patch('builtins.print')):
                factory.return_value.__enter__.return_value = driver
                self.assertEqual(module.main(), 0)
                self.assertEqual(claim.call_args.kwargs, {'project_path': ''})
                release.assert_called_once()
            snapshot = next(Path(root).rglob('preview.json'))
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
    def test_live_and_reused_pids_are_protected(self):
        with self.assertRaisesRegex(RuntimeError, 'alive or reused'):
            require_dead_local_owner({'host': socket.gethostname(), 'pid': os.getpid()})

    def test_remote_and_invalid_identity_are_protected(self):
        for owner in ({'host': 'other-host', 'pid': 123},
                      {'host': socket.gethostname(), 'pid': 0},
                      {'host': socket.gethostname(), 'pid': '123'}):
            with self.subTest(owner=owner), self.assertRaisesRegex(RuntimeError, 'unknown worker'):
                require_dead_local_owner(owner)

    def test_permission_denial_is_not_death(self):
        with mock.patch('os.kill', side_effect=PermissionError()):
            with self.assertRaisesRegex(RuntimeError, 'inspection is uncertain'):
                require_dead_local_owner({'host': socket.gethostname(), 'pid': 123})

    def test_explicit_missing_process_establishes_death(self):
        with mock.patch('os.kill', side_effect=ProcessLookupError()):
            require_dead_local_owner({'host': socket.gethostname(), 'pid': 123})


if __name__ == '__main__':
    unittest.main()

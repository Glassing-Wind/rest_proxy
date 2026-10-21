"""Offline embedded health bounds and routing; no services, models or native engines."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from memory import embedded_health as health


def publication(files):
    manifest = {'files': files, 'symbols': 1, 'source_bytes': 1,
                'retrieval': {'encoder_id': 'fixture', 'run_id': 'r', 'chunks': 1}}
    manifest['sha256'] = hashlib.sha256(health._json(manifest).encode()).hexdigest()
    return {'run_id': 'r', 'manifest': manifest}


class HealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
            self.skipTest('Current-file hash fixtures require the supported POSIX safe reader')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve() / 'source'
        self.root.mkdir()
        self.source = b'def first():\n    return 1\n'
        (self.root / 'a.py').write_bytes(self.source)
        self.owner = mock.Mock()
        self.owner._lock = asyncio.Lock()
        self.owner.resolve_project = mock.AsyncMock(return_value={
            'project_id': 'p', 'run_id': 'r', 'workspace_path': str(self.root), 'matched_by': 'project_id'})
        self.files = [{'path': 'a.py', 'sha256': hashlib.sha256(self.source).hexdigest()}]
        self.attempt = {'project_id': 'p', 'status': 'published', 'published_run_id': 'r',
                        'scope': 'latest-owned-embedded-attempt', 'automatic_resume': False}

    async def check(self, *, files=None, audit=False):
        with mock.patch.object(health, 'read_outline_publication',
                               mock.AsyncMock(return_value=publication(self.files if files is None else files))), \
                mock.patch.object(health, 'read_attempt', mock.AsyncMock(return_value=self.attempt)):
            return await health.workspace_indexing_health(self.owner, 'p', audit=audit)

    async def test_matches_manifest_without_engine_or_model(self):
        self.owner.search = mock.AsyncMock(side_effect=AssertionError('search must not run'))
        result = await self.check()
        self.assertEqual(result['storage_backend'], 'embedded')
        self.assertEqual(result['status'], 'published')
        self.assertFalse(result['model_required'])
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['freshness']['status'], 'matches_manifest')
        self.assertEqual(result['freshness']['counts']['matching'], 1)
        self.assertTrue(result['freshness']['checks_complete'])
        self.assertFalse(result['freshness']['unlisted_files_checked'])
        self.assertFalse(result['freshness']['atomic_filesystem_snapshot'])
        self.owner.resolve_project.assert_awaited_once_with('p')
        self.owner.search.assert_not_awaited()

    async def test_byte_change_detected_even_when_mtime_preserved(self):
        path = self.root / 'a.py'
        original_stat = path.stat()
        path.write_bytes(self.source.replace(b'1', b'2'))
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        result = await self.check()
        self.assertEqual(result['freshness']['status'], 'differs_from_manifest')
        self.assertEqual(result['freshness']['counts']['modified'], 1)
        self.assertTrue(result['freshness']['checks_complete'])
        detail = result['freshness']['details'][0]
        self.assertEqual(detail['published_sha256'], self.files[0]['sha256'])
        self.assertNotEqual(detail['published_sha256'], detail['current_sha256'])

    async def test_missing_file_and_missing_root_are_explicit(self):
        (self.root / 'a.py').unlink()
        result = await self.check()
        self.assertEqual(result['freshness']['counts']['missing'], 1)
        self.assertEqual(result['freshness']['status'], 'differs_from_manifest')
        self.root.rmdir()
        result = await self.check()
        self.assertEqual(result['freshness']['root_status'], 'missing')
        self.assertEqual(result['freshness']['counts']['missing'], 1)

    async def test_unlisted_files_are_never_opened(self):
        (self.root / 'unlisted.py').write_bytes(b'private unlisted text')
        with mock.patch.object(health, '_read_hash', wraps=health._read_hash) as read:
            result = await self.check()
        self.assertEqual(result['freshness']['manifest_files'], 1)
        self.assertEqual([call.args[1].as_posix() for call in read.call_args_list], ['a.py'])
        self.assertNotIn('private unlisted text', json.dumps(result))

    async def test_invalid_manifest_paths_do_not_read_source(self):
        files = [{'path': path, 'sha256': self.files[0]['sha256']}
                 for path in ('../private.py', '/private.py', 'sub/../a.py', './a.py', 'a.py\0')]
        with mock.patch.object(health, '_read_hash', side_effect=AssertionError('unsafe read')):
            result = await self.check(files=files)
        self.assertEqual(result['freshness']['counts']['invalid_path'], 5)
        self.assertEqual(result['freshness']['status'], 'unknown')
        self.assertFalse(result['freshness']['checks_complete'])

    async def test_file_and_parent_symlinks_are_never_read(self):
        outside = Path(self.directory.name) / 'outside.py'
        outside.write_bytes(b'private outside text')
        (self.root / 'a.py').unlink()
        (self.root / 'a.py').symlink_to(outside)
        result = await self.check()
        self.assertEqual(result['freshness']['counts']['scope_changed'], 1)
        self.assertEqual(result['freshness']['status'], 'unknown')
        self.assertEqual(result['freshness']['bytes_read'], 0)
        (self.root / 'sub').symlink_to(outside.parent, target_is_directory=True)
        result = await self.check(files=[{'path': 'sub/outside.py', 'sha256': self.files[0]['sha256']}])
        self.assertEqual(result['freshness']['counts']['scope_changed'], 1)
        self.assertEqual(result['freshness']['bytes_read'], 0)
        self.assertNotIn('private outside text', json.dumps(result))

    async def test_nonregular_unreadable_and_unavailable_reader(self):
        (self.root / 'a.py').unlink()
        (self.root / 'a.py').mkdir()
        result = await self.check()
        self.assertEqual(result['freshness']['counts']['not_regular_file'], 1)
        with mock.patch.object(health, '_open_directory', side_effect=PermissionError('sensitive message')):
            result = await self.check()
        self.assertEqual(result['freshness']['counts']['unreadable'], 1)
        self.assertNotIn('sensitive message', json.dumps(result))
        with mock.patch.object(health.os, 'supports_dir_fd', set()):
            result = await self.check()
        self.assertEqual(result['freshness']['counts']['safe_reader_unavailable'], 1)
        self.assertEqual(result['freshness']['status'], 'unknown')

    async def test_scan_budgets_and_issue_details(self):
        with mock.patch.object(health, '_MAX_FILE_BYTES', 1):
            result = await self.check()
        self.assertEqual(result['freshness']['counts']['budget_exceeded'], 1)
        self.assertFalse(result['freshness']['checks_complete'])
        (self.root / 'b.py').write_bytes(self.source)
        two_files = self.files + [{'path': 'b.py', 'sha256': self.files[0]['sha256']}]
        with mock.patch.object(health, '_MAX_TOTAL_BYTES', len(self.source)):
            result = await self.check(files=two_files)
        self.assertEqual(result['freshness']['counts']['matching'], 1)
        self.assertEqual(result['freshness']['counts']['budget_exceeded'], 1)
        self.assertEqual(result['freshness']['bytes_read'], len(self.source))
        files = [{'path': f'missing-{number}.py', 'sha256': self.files[0]['sha256']} for number in range(55)]
        result = await self.check(files=files)
        self.assertEqual(result['freshness']['counts']['missing'], 55)
        self.assertEqual(len(result['freshness']['details']), 50)
        self.assertTrue(result['freshness']['details_truncated'])

    async def test_changed_during_read_and_replaced_root_are_unknown(self):
        with mock.patch.object(health, '_read_hash', return_value=('changed_during_read', None, 3)):
            result = await self.check()
        self.assertEqual(result['freshness']['status'], 'unknown')
        self.assertEqual(result['freshness']['counts']['changed_during_read'], 1)
        self.assertEqual(result['freshness']['hashes_checked'], 0)
        moved = self.root.with_name('moved')
        self.root.rename(moved)
        self.root.symlink_to(moved, target_is_directory=True)
        result = await self.check()
        self.assertEqual(result['freshness']['root_status'], 'scope_changed')
        self.assertEqual(result['freshness']['bytes_read'], 0)

    async def test_failed_latest_attempt_does_not_replace_publication_health(self):
        self.attempt.update(status='failed', candidate_run_id='new', error_type='ValueError')
        result = await self.check()
        self.assertEqual(result['run_id'], 'r')
        self.assertEqual(result['freshness']['status'], 'matches_manifest')
        self.assertEqual(result['latest_indexing_attempt']['status'], 'failed')
        self.assertFalse(result['latest_indexing_attempt']['automatic_resume'])

    async def test_audit_request_has_explicit_unsupported_deep_checks(self):
        result = await self.check(audit=True)
        self.assertTrue(result['audit']['requested'])
        self.assertFalse(result['audit']['supported'])
        self.assertFalse(result['audit']['performed'])
        self.assertIn('parser-fidelity', result['audit']['unsupported_checks'])
        self.assertIn('vector-integrity', result['audit']['unsupported_checks'])

    async def test_missing_ambiguous_and_changed_publications(self):
        self.owner.resolve_project.return_value = None
        with mock.patch.object(health, 'read_outline_publication', side_effect=AssertionError('unexpected read')):
            result = await health.workspace_indexing_health(self.owner, 'missing')
        self.assertEqual(result['status'], 'not_published')
        self.owner.resolve_project.side_effect = ValueError('Ambiguous embedded workspace')
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            await health.workspace_indexing_health(self.owner, 'same-basename')
        self.owner.resolve_project.side_effect = None
        self.owner.resolve_project.return_value = {'project_id': 'p', 'run_id': 'old', 'workspace_path': str(self.root)}
        with mock.patch.object(health, '_scan_manifest', side_effect=AssertionError('mixed receipt')):
            result = await self.check()
        self.assertEqual(result['status'], 'publication_changed')

    async def test_empty_manifest_does_not_claim_repository_freshness(self):
        result = await self.check(files=[])
        self.assertEqual(result['freshness']['status'], 'empty_manifest')
        self.assertEqual(result['freshness']['hashes_checked'], 0)
        self.assertFalse(result['coverage_complete'])

    async def test_manifest_identity_and_input_output_limits(self):
        corrupted = publication(self.files)
        corrupted['manifest']['symbols'] = 99
        with mock.patch.object(health, 'read_outline_publication', mock.AsyncMock(return_value=corrupted)), \
                mock.patch.object(health, 'read_attempt', mock.AsyncMock(return_value=self.attempt)):
            with self.assertRaisesRegex(RuntimeError, 'recorded hash'):
                await health.workspace_indexing_health(self.owner, 'p')
        for value in ('', None, 'x' * 4097, 'bad\0path'):
            with self.assertRaises(ValueError):
                await health.workspace_indexing_health(self.owner, value)
        with self.assertRaises(ValueError):
            await health.workspace_indexing_health(self.owner, 'p', audit=1)
        with mock.patch.object(health, '_MAX_FILES', 0):
            with self.assertRaisesRegex(ValueError, '5000'):
                await self.check()
        with mock.patch.object(health, '_MAX_OUTPUT_BYTES', 10):
            with self.assertRaisesRegex(ValueError, 'output budget'):
                await self.check()

    async def test_common_tool_dispatches_before_legacy_storage(self):
        with mock.patch('graphrag_core.config.load_env'):
            from tools.hands import indexing
        runtime = mock.Mock()
        runtime.workspace_indexing_health = mock.AsyncMock(return_value={
            'storage_backend': 'embedded', 'status': 'not_published'})
        with mock.patch.dict(os.environ, {'LM_PROXY_STORAGE_BACKEND': 'embedded'}), \
                mock.patch('memory.embedded_runtime.get_embedded_runtime', return_value=runtime), \
                mock.patch.object(indexing, 'get_memory_modules', side_effect=AssertionError('Postgres')), \
                mock.patch.object(indexing, 'get_workspace_path', side_effect=AssertionError('legacy path')), \
                mock.patch('graph_bootstrap.require_driver', side_effect=AssertionError('Neo4j')):
            result = json.loads(await indexing.get_indexing_health('missing', audit=True))
        self.assertEqual(result['storage_backend'], 'embedded')
        runtime.workspace_indexing_health.assert_awaited_once_with('missing', audit=True)


if __name__ == '__main__':
    unittest.main()

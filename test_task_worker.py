"""Injected synthetic generator tests; no inference services or model lifecycle."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from memory.task_registry import TaskRegistry
from memory.task_worker import run_worker


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'fixture.py').write_text('return 1\n')
        self.registry = TaskRegistry(str(root / 'state'))
        self.task = self.registry.create('fixture', str(root), 'Explain line', [], ['read_source'])
        self.calls = 0

    async def generate(self, prompt):
        self.calls += 1
        self.assertNotIn('claim', prompt)
        evidence = prompt['evidence']
        return dict(schema_version=1, answer='Fixture returns one', limits=['Synthetic generator'],
                    citations=[{key: evidence[key] for key in ('path', 'start_line', 'end_line', 'sha256')}])

    async def run_adapter(self, generate=None, **kwargs):
        return await run_worker(self.registry, 'fixture', self.task['id'], 1, 'fixture-worker',
                                'fixture.py', generate or self.generate, end_line=1, **kwargs)

    async def test_expired_recovery_preserves_old_checkpoint_and_rereads_source(self):
        with patch('memory.task_registry.time.time', return_value=100):
            claimed = self.registry.claim('fixture', self.task['id'], 1, 'old', lease_seconds=1)
            saved = self.registry.checkpoint('fixture', self.task['id'], claimed['revision'],
                                             claimed['claim']['token'], {'partial': 'old evidence'})
        (Path(self.temp.name) / 'fixture.py').write_text('return 2\n')
        with patch('memory.task_registry.time.time', return_value=102):
            result = await run_worker(self.registry, 'fixture', self.task['id'], saved['revision'],
                'new', 'fixture.py', self.generate, enabled=True, end_line=1,
                recovery_reason='Expired interrupted fixture')
        reopened = self.registry.get('fixture', self.task['id'])
        self.assertEqual(reopened['claim_history'][0]['checkpoint'], {'partial': 'old evidence'})
        self.assertIn('return 2', result['finding']['retained_evidence'][0]['source'])
        self.assertEqual(reopened['attempts'], 2)
        self.assertEqual(self.calls, 1)

    async def test_active_recovery_rejects_without_inference(self):
        claimed = self.registry.claim('fixture', self.task['id'], 1, 'old')
        with self.assertRaises(ValueError):
            await run_worker(self.registry, 'fixture', self.task['id'], claimed['revision'],
                'new', 'fixture.py', self.generate, enabled=True, recovery_reason='Still active')
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.registry.get('fixture', self.task['id'])['revision'], claimed['revision'])

    async def test_generation_failure_reopens_without_private_error_text(self):
        async def failing(prompt):
            raise TimeoutError('private-provider-body')
        with self.assertRaises(TimeoutError):
            await self.run_adapter(failing, enabled=True)
        saved = TaskRegistry(str(Path(self.temp.name) / 'state')).get('fixture', self.task['id'])
        self.assertEqual(saved['status'], 'claimed')
        self.assertEqual(saved['failures'][0]['category'], 'timeout')
        self.assertEqual(saved['failures'][0]['attempt'], 1)
        self.assertEqual(saved['failures'][0]['remote_termination'], 'unknown')
        self.assertNotIn('private-provider-body', str(saved))
        self.assertTrue(saved['checkpoint'])

    async def test_failure_after_cancellation_does_not_overwrite_task(self):
        async def failing(prompt):
            task = self.registry.get('fixture', self.task['id'])
            self.registry.cancel('fixture', task['id'], task['revision'], 'User stopped')
            raise RuntimeError('private')
        with self.assertRaises(RuntimeError):
            await self.run_adapter(failing, enabled=True)
        saved = self.registry.get('fixture', self.task['id'])
        self.assertEqual(saved['status'], 'cancelled')
        self.assertNotIn('failures', saved)

    async def test_wrong_citation_records_rejection_without_model_text(self):
        async def invalid(prompt):
            return dict(schema_version=1, answer='private-generated-output', citations=[], limits=[])
        with self.assertRaises(ValueError):
            await self.run_adapter(invalid, enabled=True)
        saved = self.registry.get('fixture', self.task['id'])
        self.assertEqual(saved['status'], 'claimed')
        self.assertNotIn('submissions', saved)
        self.assertEqual(saved['failures'][0]['category'], 'finding_rejected')
        self.assertEqual(saved['failures'][0]['stage'], 'returned_finding')
        self.assertNotIn('private-generated-output', str(saved))
        self.assertEqual(self.registry.inspect('fixture', self.task['id'])['failures'], saved['failures'])

    async def test_source_change_before_submit_retains_rejection(self):
        async def changed(prompt):
            result = await self.generate(prompt)
            (Path(self.temp.name) / 'fixture.py').write_text('return 999\n')
            return result
        with self.assertRaises(ValueError):
            await self.run_adapter(changed, enabled=True)
        saved = self.registry.get('fixture', self.task['id'])
        self.assertEqual(saved['failures'][0]['category'], 'finding_rejected')
        self.assertNotIn('submissions', saved)

    async def test_disabled_makes_no_claim_or_call(self):
        with self.assertRaises(ValueError):
            await self.run_adapter()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.registry.get('fixture', self.task['id'])['status'], 'queued')

    async def test_one_call_retains_evidence_and_requires_review(self):
        result = await self.run_adapter(enabled=True)
        self.assertEqual(self.calls, 1)
        self.assertEqual(result['status'], 'review_pending')
        self.assertEqual(result['finding']['retained_evidence'][0]['source'], '1: return 1')
        self.assertNotIn('claim', result)

    async def test_multi_source_retention_after_correction_and_source_deletion(self):
        root = Path(self.temp.name)
        (root / 'other.py').write_text('return 2\n')
        async def generate(prompt):
            return dict(schema_version=1, answer='Two fixture return statements',
                limits=['Synthetic; no semantic correctness attestation'],
                citations=[{key: item[key] for key in ('path', 'start_line', 'end_line', 'sha256')}
                           for item in prompt['evidence_bundle']])
        result = await self.run_adapter(generate, enabled=True, additional_sources=[
            dict(path='other.py', start_line=1, end_line=1)])
        self.assertEqual(len(result['finding']['retained_evidence']), 2)
        self.registry.review('fixture', self.task['id'], result['revision'], 'reviewer',
                             'request_correction', 'Need stronger conclusion')
        (root / 'fixture.py').unlink()
        (root / 'other.py').unlink()
        saved = TaskRegistry(str(root / 'state')).get('fixture', self.task['id'])
        self.assertEqual(saved['status'], 'queued')
        self.assertEqual(saved['submissions'][0]['finding'], result['finding'])
        self.assertEqual(len(saved['checkpoint']['evidence_bundle']), 2)

    async def test_retry_receives_latest_correction_feedback(self):
        result = await self.run_adapter(enabled=True)
        reviewed = self.registry.review('fixture', self.task['id'], result['revision'],
            'reviewer', 'request_correction', 'Explain the evidence limitations')
        async def generate(prompt):
            self.assertEqual(prompt['review_feedback'], 'Explain the evidence limitations')
            return await self.generate(prompt)
        retried = await run_worker(self.registry, 'fixture', self.task['id'],
            reviewed['revision'], 'fixture-worker', 'fixture.py', generate,
            enabled=True, end_line=1)
        self.assertEqual(retried['status'], 'review_pending')
        self.assertEqual(len(self.registry.get('fixture', self.task['id'])['submissions']), 2)

    async def test_multi_source_missing_citation_rejected(self):
        (Path(self.temp.name) / 'other.py').write_text('return 2\n')
        with self.assertRaises(ValueError):
            await self.run_adapter(enabled=True, additional_sources=[
                dict(path='other.py', start_line=1, end_line=1)])
        self.assertNotIn('submissions', self.registry.get('fixture', self.task['id']))

    async def test_too_many_sources_rejected_before_claim(self):
        with self.assertRaises(ValueError):
            await self.run_adapter(enabled=True, additional_sources=[{}] * 3)
        self.assertEqual(self.registry.get('fixture', self.task['id'])['status'], 'queued')

    async def test_unsupplied_citation_rejected(self):
        async def generate(prompt):
            result = await self.generate(prompt)
            result['citations'][0]['path'] = 'other.py'
            return result
        with self.assertRaises(ValueError):
            await self.run_adapter(generate, enabled=True)
        task = self.registry.get('fixture', self.task['id'])
        self.assertEqual(task['status'], 'claimed')
        self.assertNotIn('submissions', task)
        self.assertIsNotNone(task['checkpoint'])

    async def test_cancellation_during_generation_rejects_submission(self):
        async def generate(prompt):
            task = self.registry.get('fixture', self.task['id'])
            self.registry.cancel('fixture', task['id'], task['revision'], 'Stopped')
            return await self.generate(prompt)
        with self.assertRaises(ValueError):
            await self.run_adapter(generate, enabled=True)
        self.assertEqual(self.registry.get('fixture', self.task['id'])['status'], 'cancelled')

    async def test_timeout_keeps_recoverable_checkpoint(self):
        async def generate(prompt):
            await asyncio.sleep(2)
        with self.assertRaises(TimeoutError):
            await self.run_adapter(generate, enabled=True, timeout_seconds=1)
        task = self.registry.get('fixture', self.task['id'])
        self.assertEqual(task['status'], 'claimed')
        self.assertIsNotNone(task['checkpoint'])


if __name__ == '__main__':
    unittest.main()

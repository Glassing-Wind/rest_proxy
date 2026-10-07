"""Injected synthetic generator tests; no inference services or model lifecycle."""
import asyncio
from pathlib import Path
import tempfile
import unittest

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

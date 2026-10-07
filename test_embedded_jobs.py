"""Offline embedded journal cancellation draining and read bounds; no native dependencies."""
import asyncio
import unittest

from memory.embedded_jobs import drain, read_attempt


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancellation_drains_update(self):
        entered = asyncio.Event()
        finish = asyncio.Event()
        async def update():
            entered.set()
            await finish.wait()
            return 'committed'
        task = asyncio.create_task(drain(update()))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        finish.set()
        self.assertEqual(await task, 'committed')

    async def test_failures_and_unbounded_ids_are_not_hidden(self):
        async def failed():
            raise ValueError('fixture')
        with self.assertRaises(ValueError):
            await drain(failed())
        for project in ('', 'x' * 129, 'a:b'):
            with self.assertRaises(ValueError):
                await read_attempt(None, project)


if __name__ == '__main__':
    unittest.main()

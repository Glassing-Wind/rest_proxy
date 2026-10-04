"""Disposable native Ladybug tests; requires ladybug, no service environment vars."""
import asyncio
from pathlib import Path
import tempfile
import threading
import unittest

from memory.embedded_ladybug import LadybugGraphDriver, _finish_thread


class Transactions(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_drains_native_work(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def operation():
            started.set()
            release.wait(5)
            finished.set()

        task = asyncio.create_task(_finish_thread(operation))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(finished.is_set())
        finally:
            release.set()

    async def test_commit_rollback_cancel_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'graph')
            driver = LadybugGraphDriver(path)
            async with driver.session() as a, driver.session() as b:
                self.assertIsNot(a._connection, b._connection)

                async def schema(tx):
                    await tx.run('CREATE NODE TABLE File(id STRING PRIMARY KEY)')
                await a.execute_write(schema)

                async def add(tx, identity):
                    await tx.run('CREATE (:File {id:$id})', id=identity)
                await a.execute_write(add, 'kept')

                async def failing(tx):
                    await add(tx, 'rollback')
                    raise ValueError('deliberate')
                with self.assertRaises(ValueError):
                    await a.execute_write(failing)

                ready = asyncio.Event()
                async def cancelled(tx):
                    await add(tx, 'cancelled')
                    ready.set()
                    await asyncio.Event().wait()
                task = asyncio.create_task(a.execute_write(cancelled))
                await asyncio.wait_for(ready.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

                async def ids(tx):
                    return await (await tx.run('MATCH (f:File) RETURN f.id AS id ORDER BY id')).data()
                self.assertEqual(await b.execute_read(ids), [{'id': 'kept'}])
                with self.assertRaises(Exception):
                    await b.execute_read(add, 'read-only')
                self.assertEqual(await b.execute_read(ids), [{'id': 'kept'}])
            await driver.close()
            driver = LadybugGraphDriver(path)
            async with driver.session() as session:
                self.assertEqual(await session.execute_read(ids), [{'id': 'kept'}])
            await driver.close()


if __name__ == '__main__':
    unittest.main()

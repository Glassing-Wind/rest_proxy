"""Disposable native Ladybug tests; requires ladybug, no service environment vars."""
import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from memory.embedded_ladybug import LadybugGraphDriver, _finish_thread


class Transactions(unittest.IsolatedAsyncioTestCase):
    async def test_native_bootstrap_and_reject_incompatible_schema(self):
        import graph_bootstrap

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'graph')
            with mock.patch.dict('os.environ', {
                'LM_PROXY_GRAPH_BACKEND': 'ladybug', 'LM_PROXY_STORAGE_BACKEND': '',
                'LM_PROXY_LADYBUG_PATH': path,
            }), mock.patch.object(graph_bootstrap.AsyncGraphDatabase, 'driver',
                                  side_effect=AssertionError('external graph forbidden')):
                try:
                    driver = await graph_bootstrap.require_driver()
                    self.assertIs(await graph_bootstrap.require_driver(), driver)
                    self.assertEqual(await driver.describe_file_symbols('missing'), [])
                finally:
                    await graph_bootstrap.close_graph_db()
            driver = LadybugGraphDriver(str(Path(directory) / 'wrong'))
            try:
                async with driver.session() as session:
                    async def bad_schema(tx):
                        await tx.run('CREATE NODE TABLE File(id STRING PRIMARY KEY)')
                    await session.execute_write(bad_schema)
                with self.assertRaisesRegex(RuntimeError, 'Incompatible Ladybug schema table: File'):
                    await driver.initialize_schema()
            finally:
                await driver.close()

    async def test_schema_and_all_outline_labels(self):
        from memory.embedded_schema import SYMBOL_LABELS

        with tempfile.TemporaryDirectory() as directory:
            driver = LadybugGraphDriver(str(Path(directory) / 'graph'))
            try:
                await driver.initialize_schema()
                await driver.initialize_schema()
                async with driver.session() as session:
                    async def populate(tx):
                        await tx.run("CREATE (:File {id:'p:file:a', project_id:'p'})")
                        await tx.run("CREATE (:File {id:'q:file:a', project_id:'q'})")
                        for index, label in enumerate(SYMBOL_LABELS):
                            await tx.run(
                                f"CREATE (:{label} {{id:$id, name:$name, start_line:$line, "
                                "end_line:$line, project_id:'p'})",
                                id='p:' + label, name=label, line=index + 1,
                            )
                            await tx.run(f"MATCH (f:File {{id:'p:file:a'}}), "
                                         f"(s:{label} {{id:$id}}) CREATE (f)-[:CONTAINS]->(s)",
                                         id='p:' + label)
                    await session.execute_write(populate)
                rows = await driver.describe_file_symbols('p:file:a')
                self.assertEqual([row['kind'] for row in rows], list(SYMBOL_LABELS))
                self.assertTrue(all(row['sig'] is None for row in rows))
                self.assertEqual(await driver.describe_file_symbols('q:file:a'), [])
                self.assertEqual(await driver.describe_file_symbols('absent'), [])
                async with driver.session() as session:
                    async def unsupported_version(tx):
                        await tx.run("MATCH (s:EmbeddedSchema) SET s.version=99")
                    await session.execute_write(unsupported_version)
                with self.assertRaisesRegex(RuntimeError, 'Unsupported Ladybug file-outline schema'):
                    await driver.initialize_schema()
                # A refused bootstrap does not remove the previously stored outline.
                self.assertEqual(len(await driver.describe_file_symbols('p:file:a')),
                                 len(SYMBOL_LABELS))
            finally:
                await driver.close()

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

"""Native Ladybug/ts-pack fixture tests, no external service environment required."""
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from graphrag_core.indexing.embedded_outlines import (
    build_outline_snapshot, index_outline_manifest, publish_outline_snapshot,
    read_outline_publication, read_outline_file,
)
from memory.embedded_ladybug import LadybugGraphDriver


class OutlineIndexing(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_replacement_failure_scoping_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            a = root / 'a.py'
            a.write_text('class Container:\n    def method(self):\n        return 1\n')
            (root / 'b.py').write_text('def removed():\n    return 2\n')
            path = str(Path(directory) / 'graph')
            driver = LadybugGraphDriver(path)
            try:
                await driver.initialize_schema()
                first = await index_outline_manifest(driver, str(root), 'p', ['a.py', 'b.py'])
                self.assertEqual(first['manifest']['symbols'], 3)
                await index_outline_manifest(driver, str(root), 'q', ['b.py'])
                a.write_text('def updated():\n    return 3\n')
                failed = build_outline_snapshot(str(root), 'p', ['a.py'])
                # Duplicate PK fails after deletion and partial insertion inside the transaction.
                failed['files'][0]['symbols'].append(copy.deepcopy(failed['files'][0]['symbols'][0]))
                with self.assertRaises(Exception):
                    await publish_outline_snapshot(driver, failed)
                self.assertEqual((await read_outline_publication(driver, 'p'))['run_id'], first['run_id'])
                self.assertEqual([s['name'] for s in await driver.describe_file_symbols('p:file:a.py')],
                                 ['Container', 'method'])
                second = await index_outline_manifest(driver, str(root), 'p', ['a.py'])
                self.assertNotEqual(first['run_id'], second['run_id'])
                self.assertEqual([s['name'] for s in await driver.describe_file_symbols('p:file:a.py')],
                                 ['updated'])
                self.assertEqual(await driver.describe_file_symbols('p:file:b.py'), [])
                self.assertEqual([s['name'] for s in await driver.describe_file_symbols('q:file:b.py')],
                                 ['removed'])
                a.write_text('def broken(:\n')
                with self.assertRaises(ValueError):
                    await index_outline_manifest(driver, str(root), 'p', ['a.py'])
                self.assertEqual((await read_outline_publication(driver, 'p'))['run_id'], second['run_id'])
            finally:
                await driver.close()
            driver = LadybugGraphDriver(path)
            try:
                await driver.initialize_schema()
                self.assertEqual((await read_outline_publication(driver, 'p'))['run_id'], second['run_id'])
                async with driver.session() as session:
                    async def evidence(tx):
                        return await (await tx.run(
                            "MATCH (s:SourceEvidence {id:'p:file:a.py'}) "
                            'RETURN s.content AS content, s.sha256 AS hash, s.run_id AS run'
                        )).data()
                    rows = await session.execute_read(evidence)
                self.assertEqual(rows[0]['content'], 'def updated():\n    return 3\n')
                self.assertEqual(rows[0]['run'], second['run_id'])
                self.assertEqual(rows[0]['hash'], second['manifest']['files'][0]['sha256'])
                bundle = await read_outline_file(driver, 'p', 'a.py', max_lines=1)
                self.assertEqual(bundle['source'], '1: def updated():')
                self.assertEqual(bundle['next_start_line'], 2)
                self.assertEqual(bundle['run_id'], second['run_id'])
                self.assertEqual(bundle['source_sha256'], rows[0]['hash'])
                self.assertEqual([s['name'] for s in bundle['symbols']], ['updated'])
                self.assertIsNone(await read_outline_file(driver, 'p', 'b.py'))
                self.assertIsNone(await read_outline_file(driver, 'other', 'a.py'))
            finally:
                await driver.close()

    async def test_killed_uncommitted_writer_preserves_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            (root / 'a.py').write_text('def preserved():\n    return 1\n')
            path = str(Path(directory) / 'graph')
            driver = LadybugGraphDriver(path)
            try:
                await driver.initialize_schema()
                original = await index_outline_manifest(driver, str(root), 'p', ['a.py'])
            finally:
                await driver.close()
            code = """
import asyncio,sys
from memory.embedded_ladybug import LadybugGraphDriver
async def main():
    driver=LadybugGraphDriver(sys.argv[1])
    async with driver.session() as session:
        async def interrupted(tx):
            await tx.run("MATCH (f:File {project_id:'p'}) DETACH DELETE f")
            print('uncommitted',flush=True)
            await asyncio.Event().wait()
        await session.execute_write(interrupted)
asyncio.run(main())
"""
            child = await asyncio.create_subprocess_exec(
                sys.executable, '-c', code, path, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                self.assertEqual(await asyncio.wait_for(child.stdout.readline(), 10), b'uncommitted\n')
            finally:
                if child.returncode is None:
                    child.kill()
                await child.communicate()
            driver = LadybugGraphDriver(path)
            try:
                self.assertEqual((await read_outline_publication(driver, 'p'))['run_id'], original['run_id'])
                self.assertEqual([s['name'] for s in await driver.describe_file_symbols('p:file:a.py')],
                                 ['preserved'])
            finally:
                await driver.close()

    async def test_owner_rejects_other_process_and_releases_after_close(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'graph')
            driver = LadybugGraphDriver(path)
            code = ('from memory.embedded_owner import EmbeddedOwnerLock; import sys; '
                    'EmbeddedOwnerLock(sys.argv[1])')
            try:
                result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code, path],
                                                 capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'already has an owner', result.stderr)
                with self.assertRaises(RuntimeError):
                    LadybugGraphDriver(path)
            finally:
                await driver.close()
            result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code, path],
                                             capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_manifest_rejects_escape_duplicates_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.py').write_text('x = 1\n')
            (root / 'alias.py').symlink_to(root / 'a.py')
            for paths in [['../outside.py'], ['a.py', 'a.py'], ['alias.py']]:
                with self.assertRaises(ValueError):
                    build_outline_snapshot(str(root), 'p', paths)
            self.assertTrue(json.dumps(build_outline_snapshot(str(root), 'p', ['a.py'])))


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Offline artifact checks; needs installed setuptools and pip, not storage services.

Builds from tracked source in a disposable directory. The no-dependency install
checks artifact layout and stdlib-only continuity components, not a full install.
"""
import json
from email.parser import Parser
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parent
REQUIRED = {
    'scripts/index_workspace.py', 'scripts/run_struct_index.py',
    'memory/fire_store.py', 'memory/context_bundle.py',
    'proxy/context_forwarding.py', 'mcp_server.py', 'brain_server.py', 'ts_diagnostics.py',
}


class DistributionTests(unittest.TestCase):
    def test_offline_build_and_isolated_install(self):
        with tempfile.TemporaryDirectory(prefix='rest-proxy-distribution-') as directory:
            base = Path(directory)
            source = base / 'source'
            source.mkdir()
            paths = subprocess.check_output(
                ['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
            # Include the new package marker before its first commit, too.
            paths.append('scripts/__init__.py')
            paths.extend(['requirements-core.txt', 'requirements-embedded.txt', 'requirements-dev.txt'])
            for name in sorted(set(paths)):
                original = ROOT / name
                if not name or not original.is_file():
                    continue
                destination = source / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, destination)
            environment = dict(os.environ, PYTHONPATH='')
            result = subprocess.run(
                [sys.executable, '-c',
                 "from setuptools.build_meta import build_wheel, build_sdist; "
                 "build_wheel('dist'); build_sdist('dist')"],
                cwd=source, env=environment, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr[-4000:])
            wheel = next((source / 'dist').glob('*.whl'))
            with zipfile.ZipFile(wheel) as archive:
                names = set(archive.namelist())
                self.assertTrue(REQUIRED <= names, REQUIRED - names)
                self.assertFalse(any(name.endswith('.env') or '/.runtime/' in name for name in names))
                self.assertTrue(any(name.endswith('/licenses/LICENSE') for name in names))
                metadata_path = next(name for name in names if name.endswith('.dist-info/METADATA'))
                metadata = Parser().parsestr(archive.read(metadata_path).decode())
                dependencies = metadata.get_all('Requires-Dist', [])
                core = [item for item in dependencies if ';' not in item]
                self.assertEqual(len(core), 6)
                self.assertFalse(any('crawlee' in item or 'lancedb' in item for item in core))
                self.assertEqual(set(metadata.get_all('Provides-Extra', [])), {'embedded', 'full', 'dev'})
                self.assertTrue(any('ladybug==0.21.2' in item and 'embedded' in item for item in dependencies))
                self.assertTrue(any('6fcead43fc13b0049481ea5b5c491e02eab4ac68' in item
                                    and 'embedded' in item for item in dependencies))
            sdist = next((source / 'dist').glob('*.tar.gz'))
            with tarfile.open(sdist) as archive:
                names = {name.split('/', 1)[-1] for name in archive.getnames()}
                self.assertTrue(REQUIRED <= names, REQUIRED - names)
            installed = base / 'installed'
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-index',
                 '--target', str(installed), str(wheel)],
                cwd=base, env=environment, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            code = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import memory.fire_store, memory.context_bundle
root = pathlib.Path(sys.argv[1])
import os
runtime = (root.parent / 'runtime').resolve()
os.environ['LM_PROXY_RUNTIME_DIR'] = str(runtime)
import _jobs
assert _jobs._ensure_jobs_runtime_dir() == runtime / 'jobs'
assert _jobs._ensure_project_locks_dir() == runtime / 'project_locks'
assert pathlib.Path(memory.fire_store.__file__).is_relative_to(root)
assert pathlib.Path(memory.context_bundle.__file__).is_relative_to(root)
for name in json.loads(sys.argv[2]):
    path = root / name
    assert path.is_file(), name
    compile(path.read_text(), str(path), 'exec')
print('isolated artifact imports and worker compilation passed')
"""
            result = subprocess.run(
                [sys.executable, '-I', '-S', '-c', code, str(installed), json.dumps(sorted(REQUIRED))],
                cwd=base, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])


if __name__ == '__main__':
    unittest.main()

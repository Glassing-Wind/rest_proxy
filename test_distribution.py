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
    'proxy/http_clients.py', 'scripts/runtime_hardware.py', 'scripts/benchmark_embedded_arm.py',
    'graphrag_core/readiness.py',
    'graphrag_core/telemetry.py', 'graphrag_core/telemetry_transport.py',
    'graphrag_core/repository_learning/adapter.py', 'graphrag_core/repository_learning/worker.py',
    'graphrag_core/repository_learning/service.py', 'graphrag_core/repository_learning/snapshot.py',
    'graphrag_core/repository_learning/pattern_store.py', 'graphrag_core/repository_learning/patterns.py',
    'graphrag_core/repository_learning/github_configuration.py',
    'graphrag_core/repository_learning/github_worker.py',
    'tools/brain/repository_learning.py',
    'graphrag_core/evidence_workspace/__init__.py',
    'graphrag_core/evidence_workspace/contracts.py',
    'graphrag_core/evidence_workspace/demo.py',
    'graphrag_core/evidence_workspace/server.py',
    'graphrag_core/evidence_workspace/static/index.html',
    'graphrag_core/evidence_workspace/static/app.js',
    'graphrag_core/evidence_workspace/static/style.css',
    'memory/evidence_workspace.py', 'scripts/evidence_workspace.py',
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
            paths.extend(['requirements-core.txt', 'requirements-embedded.txt', 'requirements-dev.txt',
                          'requirements-telemetry.txt', 'requirements-evidence.txt'])
            paths.extend(REQUIRED)
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
                self.assertEqual(set(metadata.get_all('Provides-Extra', [])),
                                 {'embedded', 'full', 'dev', 'telemetry', 'evidence'})
                evidence = [item for item in dependencies if 'extra == "evidence"' in item]
                self.assertEqual(evidence, ['pillow==12.2.0; extra == "evidence"'])
                full_pillow = next(item for item in dependencies
                                   if item.lower().startswith('pillow==') and 'extra == "full"' in item)
                self.assertEqual(full_pillow.split(';', 1)[0], evidence[0].split(';', 1)[0])
                for name in ('opentelemetry-api', 'opentelemetry-sdk', 'opentelemetry-exporter-otlp-proto-common'):
                    self.assertTrue(any(name + '==1.40.0' in item and 'telemetry' in item for item in dependencies))
                self.assertTrue(any('ladybug==0.21.2' in item and 'embedded' in item for item in dependencies))
                self.assertTrue(any('6fcead43fc13b0049481ea5b5c491e02eab4ac68' in item
                                    and 'embedded' in item for item in dependencies))
            sdist = next((source / 'dist').glob('*.tar.gz'))
            with tarfile.open(sdist) as archive:
                names = {name.split('/', 1)[-1] for name in archive.getnames()}
                self.assertTrue(REQUIRED <= names, REQUIRED - names)
                self.assertIn('requirements-telemetry.txt', names)
                self.assertIn('requirements-evidence.txt', names)
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
import memory.evidence_workspace as evidence_workspace
from graphrag_core.repository_learning.adapter import RepoAnalyzerAdapter
from graphrag_core.repository_learning.service import RepositoryLearningGateway
from graphrag_core.repository_learning.pattern_store import RepositoryPatternStore
from graphrag_core.repository_learning.github_configuration import GithubConfigurationAdapter
import graphrag_core.telemetry as telemetry
import graphrag_core.repository_learning.worker as learning_worker
import graphrag_core.repository_learning.github_worker as github_worker
root = pathlib.Path(sys.argv[1])
import os
runtime = (root.parent / 'runtime').resolve()
os.environ['LM_PROXY_RUNTIME_DIR'] = str(runtime)
import _jobs
assert _jobs._ensure_jobs_runtime_dir() == runtime / 'jobs'
assert _jobs._ensure_project_locks_dir() == runtime / 'project_locks'
assert pathlib.Path(memory.fire_store.__file__).is_relative_to(root)
assert pathlib.Path(memory.context_bundle.__file__).is_relative_to(root)
assert pathlib.Path(evidence_workspace.__file__).is_relative_to(root)
assert callable(evidence_workspace.EvidenceWorkspaceStore)
assert 'PIL' not in sys.modules
assert pathlib.Path(learning_worker.__file__).is_relative_to(root)
assert pathlib.Path(github_worker.__file__).is_relative_to(root)
assert RepoAnalyzerAdapter().availability() == {'status': 'disabled'}
assert GithubConfigurationAdapter().availability() == {'status': 'disabled'}
assert pathlib.Path(telemetry.__file__).is_relative_to(root)
assert telemetry.status()['exporter'] == 'none'
with telemetry.operation('parser', 'repository.parse', backend='ts_pack'):
    pass
assert 'opentelemetry.sdk' not in sys.modules
learning_directory = (root.parent / 'learning-memory').resolve()
assert RepositoryPatternStore(str(learning_directory)).read('project')['status'] == 'not-learned'
assert not learning_directory.exists()
assert callable(RepositoryLearningGateway.profile)
for name in json.loads(sys.argv[2]):
    path = root / name
    assert path.is_file(), name
    if path.suffix == '.py':
        compile(path.read_text(), str(path), 'exec')
print('isolated artifact imports and worker compilation passed')
"""
            result = subprocess.run(
                [sys.executable, '-I', '-S', '-c', code, str(installed), json.dumps(sorted(REQUIRED))],
                cwd=base, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])


if __name__ == '__main__':
    unittest.main()

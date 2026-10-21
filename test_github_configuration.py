"""Offline component-worker protocol tests; no acquisition, engines or inference.

Most cases use an explicitly labelled disposable source fixture to check isolation
and failure contracts. Set LM_PROXY_GITHUB_ANALYZER_TEST_ROOT to a genuine checkout
for the optional actual-component case; no installation or backend writes occur.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from graphrag_core.repository_learning.github_configuration import COMPONENT_FILES, GithubConfigurationAdapter

MODEL_SOURCE = '''from dataclasses import dataclass, field
from GithubAnalyzer.models.core.base_model import BaseModel
@dataclass
class RequirementsResult(BaseModel):
    is_valid: bool = True
    errors: list = field(default_factory=list)
    raw_data: object = None
    requirements: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
@dataclass
class GitignoreResult(BaseModel):
    is_valid: bool = True
    errors: list = field(default_factory=list)
    raw_data: object = None
    patterns: list = field(default_factory=list)
'''
BASE_MODEL_SOURCE = '''from dataclasses import dataclass
from GithubAnalyzer.utils.logging import get_logger
logger = get_logger(__name__)
@dataclass
class BaseModel:
    pass
'''
LOGGING_SOURCE = '''import logging
from pathlib import Path
root = Path(__file__).parent.parent.parent.parent.parent
logs = root / 'logs'
logs.mkdir(exist_ok=True)
def get_logger(name):
    logger = logging.getLogger(name)
    logger.addHandler(logging.FileHandler(logs / (name + '.log')))
    return logger
'''
COMPONENT_SOURCE = '''from pathlib import Path
from GithubAnalyzer.models.core.parsers import RequirementsResult, GitignoreResult
from GithubAnalyzer.services.core.base_service import BaseService
from GithubAnalyzer.utils.logging import get_logger
logger = get_logger(__name__)
def guard_cases(text):
    if text == '__network__':
        import socket
        try:
            socket.create_connection(('127.0.0.1', 9))
        except Exception:
            pass
    if text == '__write__':
        try:
            (Path.cwd() / 'outside-component-scratch').write_text('must be denied')
        except Exception:
            pass
    if text == '__sleep__':
        import time
        time.sleep(2)
class RequirementsParser(BaseService):
    def parse(self, text):
        guard_cases(text)
        return RequirementsResult(requirements=[{'name': line, 'version': 'fixture',
            'is_editable': False, 'extras': []} for line in text.splitlines()])
class GitignoreParser(BaseService):
    def parse(self, text):
        guard_cases(text)
        return GitignoreResult(patterns=[{'pattern': line, 'negated': False,
            'directory_only': False} for line in text.splitlines()])
'''


class ConfigurationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='github-component-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        sources = (COMPONENT_SOURCE, MODEL_SOURCE, BASE_MODEL_SOURCE, 'class BaseService: pass\n', LOGGING_SOURCE)
        for relative, source in zip(COMPONENT_FILES, sources):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        (self.root / 'src/GithubAnalyzer/__init__.py').write_text(
            "raise RuntimeError('broad package initializer must never run')\n")
        self.adapter = GithubConfigurationAdapter(self.root, python=sys.executable)
        self.before = {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in self.root.rglob('*') if path.is_file()}

    async def analyze(self, text='fixture==1', parser='requirements'):
        return await self.adapter.analyze_configuration('project', 'provided-config', text, parser)

    def assert_original_unchanged(self):
        after = {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(after, self.before)

    async def test_staged_component_fixture_calls_preserve_scope_and_source_identity(self):
        before_modules = {name for name in sys.modules if name.startswith('GithubAnalyzer')}
        for parser in ('requirements', 'gitignore'):
            response = await self.analyze('first\nsecond', parser)
            self.assertTrue(response['ok'])
            result = response['result']
            self.assertEqual(result['source_id'], 'provided-config')
            self.assertEqual(result['source_sha256'], hashlib.sha256(b'first\nsecond').hexdigest())
            self.assertEqual(result['parser'], parser)
            self.assertEqual(result['metadata'], {'project_id': 'project', 'source_kind': 'provided-text'})
            self.assertEqual(response['backend']['name'], 'GithubAnalyzer')
            self.assertEqual(response['backend']['source_hashes'],
                             {path: self.before[path] for path in COMPONENT_FILES})
            self.assertEqual(response['backend']['loaded_source_files'], 5)
            self.assertEqual(response['backend']['package_startup'], 'skipped')
            self.assertEqual(response['backend']['network_access'], 'denied')
            self.assertEqual(response['backend']['external_writes'], 'denied')
            self.assertFalse(response['backend']['runtime_initialized'])
        self.assertEqual({name for name in sys.modules if name.startswith('GithubAnalyzer')}, before_modules)
        self.assert_original_unchanged()

    async def test_disabled_invalid_and_unicode_byte_bounds(self):
        self.assertEqual(await GithubConfigurationAdapter().analyze_configuration('p', 's', 'x', 'gitignore'),
                         {'ok': False, 'error': 'disabled'})
        for project, source, text, parser in [('p', 's', 'x', 'yaml'), ('p', 's', 'x', []),
                ('', 's', 'x', 'gitignore'), ('p', 's' * 257, 'x', 'gitignore'),
                ('p', 's', '', 'gitignore'), ('p', 's', '\0', 'gitignore'),
                ('p', 's', '\ud800', 'gitignore')]:
            response = await self.adapter.analyze_configuration(project, source, text, parser)
            self.assertEqual(response, {'ok': False, 'error': 'invalid_request'})
        self.assertEqual(await self.analyze('😀' * 4097), {'ok': False, 'error': 'input_too_large'})
        response = await self.analyze('😀' * 4096)
        self.assertTrue(response['ok'])
        self.assert_original_unchanged()

    async def test_network_and_external_write_attempts_are_not_hidden_by_backend(self):
        self.assertEqual(await self.analyze('__network__'), {'ok': False, 'error': 'network_access_refused'})
        self.assertEqual(await self.analyze('__write__'), {'ok': False, 'error': 'filesystem_write_refused'})
        self.assert_original_unchanged()

    async def test_output_node_count_is_bounded(self):
        for parser in ('requirements', 'gitignore'):
            self.assertEqual(await self.analyze('x\n' * 257, parser),
                             {'ok': False, 'error': 'output_too_large'})

    async def test_timeout_and_cancellation_drain_owned_worker(self):
        self.adapter.timeout_seconds = 0.1
        self.assertEqual(await self.analyze('__sleep__'), {'ok': False, 'error': 'timeout'})
        self.adapter.timeout_seconds = 5
        original = asyncio.create_subprocess_exec
        started = asyncio.Event()
        processes = []

        async def spawn(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            started.set()
            return process

        with mock.patch('asyncio.create_subprocess_exec', side_effect=spawn):
            task = asyncio.create_task(self.analyze('__sleep__'))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)
        self.assert_original_unchanged()

    async def test_symlinked_component_source_is_refused(self):
        path = self.root / COMPONENT_FILES[0]
        alternate = self.root / 'alternate.py'
        path.rename(alternate)
        path.symlink_to(alternate)
        response = await self.analyze()
        self.assertFalse(response['ok'])
        self.assertNotIn(str(self.root), str(response))

    @unittest.skipUnless(os.getenv('LM_PROXY_GITHUB_ANALYZER_TEST_ROOT'), 'explicit actual backend test root is unset')
    async def test_actual_component_source(self):
        root = Path(os.environ['LM_PROXY_GITHUB_ANALYZER_TEST_ROOT']).resolve()
        adapter = GithubConfigurationAdapter(root, python=sys.executable)
        hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in COMPONENT_FILES}
        result = await adapter.analyze_configuration('actual-fixture', 'provided-requirements',
                'requests==2.32.0\n-e example>=1\n-c constraints.txt\n', 'requirements')
        self.assertTrue(result['ok'])
        self.assertEqual(result['result']['observations']['requirements'][0],
                         {'name': 'requests', 'version': '==2.32.0', 'is_editable': False, 'extras': []})
        self.assertTrue(result['result']['observations']['requirements'][1]['is_editable'])
        self.assertEqual(result['result']['observations']['constraints'][0]['name'], '-c constraints.txt')
        result = await adapter.analyze_configuration('actual-fixture', 'provided-ignore',
                                                     'build/\n!keep.txt\n*.pyc\n', 'gitignore')
        self.assertTrue(result['ok'])
        self.assertEqual(result['result']['observations']['patterns'][0],
                         {'pattern': 'build/', 'negated': False, 'directory_only': True})
        self.assertTrue(result['result']['observations']['patterns'][1]['negated'])
        self.assertEqual(result['backend']['source_hashes'], hashes)
        self.assertEqual({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                          for name in COMPONENT_FILES}, hashes)


if __name__ == '__main__':
    unittest.main()

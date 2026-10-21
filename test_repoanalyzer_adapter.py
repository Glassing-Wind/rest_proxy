"""Offline subprocess-boundary checks; no production services or model calls.

Real checkout acceptance is optional: set LM_PROXY_REPOANALYZER_TEST_ROOT and,
when needed, LM_PROXY_REPOANALYZER_TEST_PYTHON. The real worker receives only the
disposable typed fixture below and runs with network/runtime initialization denied.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from graphrag_core.repository_learning.adapter import (
    MAX_PLAINTEXT_BYTES, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, RepoAnalyzerAdapter,
)


def fixture_payload(name: str = 'reference') -> dict:
    return {'repo_name': name, 'root_path': f'/disposable/{name}', 'files': [
        {'path': 'src/storage.py', 'language': 'python', 'symbols': [
            {'name': 'Storage', 'kind': 'Class'}, {'name': 'read', 'kind': 'Method'},
        ], 'metadata': {'role': 'infrastructure', 'source_sha256': 'a' * 64}},
        {'path': 'tests/test_storage.py', 'language': 'python', 'symbols': [
            {'name': 'test_read', 'kind': 'Function'},
        ], 'metadata': {'role': 'testing', 'source_sha256': 'b' * 64}},
        {'path': 'README.md', 'language': 'markdown', 'symbols': [],
         'metadata': {'role': 'documentation', 'source_sha256': 'c' * 64}},
    ], 'architecture': {'top_level_directories': [
        {'path': 'src', 'file_count': 1}, {'path': 'tests', 'file_count': 1},
    ]}, 'conventions': {'module_roles': {'infrastructure': 1, 'testing': 1, 'documentation': 1}},
        'metadata': {'project_id': name, 'run_id': 'disposable-run',
                     'manifest_sha256': 'd' * 64, 'snapshot_sha256': 'e' * 64,
                     'coverage': {'selected_files': 3, 'total_files': 3}}}


def plaintext_payload(text: str, source_id: str = 'fixture.txt') -> dict:
    return {'text': text, 'source_id': source_id,
            'source_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'metadata': {'project_id': 'disposable', 'source_kind': 'provided-text'}}


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='learning-adapter-tests-')
        self.root = Path(self.directory.name)
        for name in ('learning_core/service.py', 'learning_core/models.py',
                     'context_adapters/graphrag_brain.py', 'context_adapters/models.py'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('')

    def tearDown(self):
        self.directory.cleanup()

    def executable(self, body: str) -> Path:
        path = self.root / 'fixture-python'
        path.write_text(f'#!{sys.executable}\n{body}\n')
        path.chmod(0o700)
        return path

    async def test_disabled_and_environment_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            adapter = RepoAnalyzerAdapter.from_environment()
            self.assertEqual(adapter.availability(), {'status': 'disabled'})
            self.assertEqual(await adapter.analyze('profile', payload=fixture_payload()),
                             {'ok': False, 'error': 'disabled'})
        with patch.dict(os.environ, {'LM_PROXY_REPOANALYZER_ROOT': str(self.root)}, clear=True):
            self.assertEqual(RepoAnalyzerAdapter.from_environment().availability(), {'status': 'configured'})

    async def test_missing_root_and_invalid_configuration(self):
        self.assertEqual((await RepoAnalyzerAdapter(self.root / 'missing').analyze(
            'profile', payload=fixture_payload()))['error'], 'backend_unavailable')
        self.assertEqual((await RepoAnalyzerAdapter('relative').analyze(
            'profile', payload=fixture_payload()))['error'], 'invalid_configuration')
        self.assertEqual((await RepoAnalyzerAdapter(self.root, timeout_seconds=float('inf')).analyze(
            'profile', payload=fixture_payload()))['error'], 'invalid_configuration')

    async def test_invalid_operation_nonfinite_and_oversized_input(self):
        adapter = RepoAnalyzerAdapter(self.root)
        self.assertEqual((await adapter.analyze('write', payload=fixture_payload()))['error'], 'invalid_request')
        self.assertEqual((await adapter.analyze('profile', payload={'bad': float('nan')}))['error'], 'invalid_request')
        self.assertEqual((await adapter.analyze('profile', payload={'data': 'x' * MAX_REQUEST_BYTES}))['error'],
                         'input_too_large')

    async def test_plaintext_utf8_input_bound(self):
        adapter = RepoAnalyzerAdapter(self.root)
        value = plaintext_payload('é' * (MAX_PLAINTEXT_BYTES // 2 + 1))
        self.assertEqual(await adapter.analyze('plaintext_ast', payload=value),
                         {'ok': False, 'error': 'input_too_large'})
        self.assertEqual(await adapter.analyze('plaintext_ast', payload={'text': None}),
                         {'ok': False, 'error': 'invalid_request'})

    async def test_result_budget_counts_utf8_bytes_without_ascii_escape_inflation(self):
        value = {'protocol_version': 1, 'ok': True, 'result': {'text': '😀' * 4096},
                 'backend': {'name': 'RepoAnalyzer'}}
        self.assertTrue(RepoAnalyzerAdapter._validate_response(json.dumps(value, ensure_ascii=False).encode())['ok'])
        value['result']['text'] *= 2
        self.assertEqual(RepoAnalyzerAdapter._validate_response(json.dumps(value, ensure_ascii=False).encode()),
                         {'ok': False, 'error': 'output_too_large'})

    async def test_worker_protocol_and_source_hash_validation(self):
        from graphrag_core.repository_learning import worker

        value = plaintext_payload('= Guide =')
        self.assertTrue(worker._validate_plaintext_payload(value))
        value['source_sha256'] = '0' * 64
        self.assertFalse(worker._validate_plaintext_payload(value))
        value = plaintext_payload('= Guide =')
        value['metadata']['source_kind'] = 'indexed-file'
        self.assertFalse(worker._validate_plaintext_payload(value))
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-I', '-B', str(Path(worker.__file__).absolute()), str(self.root),
            cwd=self.root, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await process.communicate(json.dumps({
            'protocol_version': 2, 'operation': 'plaintext_ast', 'payload': value,
        }).encode())
        self.assertEqual(json.loads(stdout), {'ok': False, 'error': 'invalid_request', 'protocol_version': 1})

    async def test_worker_refuses_swallowed_external_file_and_directory_writes(self):
        for name in ('learning_core/framework_profiles.py', 'ingestion/local_repository.py'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('')
        with tempfile.TemporaryDirectory(prefix='learning-external-write-fixture-') as outside:
            file = Path(outside) / 'forbidden.txt'
            directory = Path(outside) / 'forbidden-directory'
            for statement, target in [(f'Path({str(file)!r}).write_text("forbidden")', file),
                                      (f'Path({str(directory)!r}).mkdir()', directory)]:
                (self.root / 'learning_core/service.py').write_text(
                    'from dataclasses import dataclass\nfrom pathlib import Path\n'
                    '@dataclass\nclass Result:\n    metadata:dict\n'
                    'class RepositoryLearningService:\n'
                    '    def __init__(self):\n        self.runtime=None\n        self.reference_learning=None\n'
                    '    async def build_repository_profile_from_indexed_payload(self,payload):\n'
                    f'        try:\n            {statement}\n'
                    '        except Exception:\n            pass\n'
                    '        return Result(payload.get("metadata",{}))\n')
                result = await RepoAnalyzerAdapter(self.root).analyze('profile', payload=fixture_payload())
                self.assertEqual(result, {'ok': False, 'error': 'filesystem_write_refused'})
                self.assertFalse(target.exists())

    async def test_shared_runner_uses_explicit_backend_and_worker_hooks(self):
        class DistinctBackend(RepoAnalyzerAdapter):
            _backend_name = 'FixtureBackend'
            _backend_markers = ('fixture-marker.py',)

            def _worker_path(self) -> Path:
                return Path(self.root) / 'fixture-worker.py'

        (self.root / 'fixture-marker.py').write_text('')
        (self.root / 'fixture-worker.py').write_text(
            'import json,sys\nrequest=json.load(sys.stdin)\n'
            'print(json.dumps({"protocol_version":1,"ok":True,"result":'
            '{"operation":request["operation"]},"backend":{"name":"FixtureBackend"}}))\n')
        result = await DistinctBackend(self.root)._execute_request(
            {'protocol_version': 1, 'operation': 'configuration', 'payload': {}})
        self.assertTrue(result['ok'])
        self.assertEqual(result['result']['operation'], 'configuration')
        self.assertEqual(result['backend']['name'], 'FixtureBackend')

    async def test_invalid_json_and_private_stderr(self):
        executable = self.executable("import sys\nsys.stderr.write('SECRET fixture diagnostics')\nprint('not-json')")
        result = await RepoAnalyzerAdapter(self.root, executable).analyze('profile', payload=fixture_payload())
        self.assertEqual(result, {'ok': False, 'error': 'invalid_response'})
        self.assertNotIn('SECRET', json.dumps(result))

    async def test_stdout_limit_while_stdin_is_not_consumed(self):
        executable = self.executable(f"import sys,time\nsys.stdout.write('x'*{MAX_RESPONSE_BYTES + 1})\n"
                                     "sys.stdout.flush()\ntime.sleep(10)")
        # The writer may block on this input while the child fills stdout. Reading
        # both output pipes concurrently must still terminate at the output limit.
        payload = fixture_payload()
        payload['metadata']['padding'] = 'x' * 200000
        result = await RepoAnalyzerAdapter(self.root, executable, timeout_seconds=2).analyze(
            'profile', payload=payload)
        self.assertEqual(result, {'ok': False, 'error': 'output_too_large'})

    def sleeping_executable(self) -> tuple[Path, Path]:
        pid_file = self.root / 'fixture.pid'
        executable = self.executable(
            f"import os,time\nfrom pathlib import Path\nPath({str(pid_file)!r}).write_text(str(os.getpid()))\n"
            "time.sleep(30)")
        return executable, pid_file

    async def wait_for_pid(self, path: Path) -> int:
        for _ in range(100):
            if path.exists() and path.read_text().strip():
                return int(path.read_text())
            await asyncio.sleep(0.01)
        self.fail('fixture process did not start')

    @staticmethod
    def assert_process_stopped(pid: int) -> None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        raise AssertionError('fixture subprocess survived cleanup')

    @unittest.skipUnless(os.name == 'posix', 'POSIX process cleanup check')
    async def test_timeout_kills_process(self):
        executable, pid_file = self.sleeping_executable()
        result = await RepoAnalyzerAdapter(self.root, executable, timeout_seconds=0.3).analyze(
            'profile', payload=fixture_payload())
        self.assertEqual(result, {'ok': False, 'error': 'timeout'})
        self.assert_process_stopped(int(pid_file.read_text()))

    @unittest.skipUnless(os.name == 'posix', 'POSIX process cleanup check')
    async def test_cancellation_kills_process(self):
        executable, pid_file = self.sleeping_executable()
        task = asyncio.create_task(RepoAnalyzerAdapter(self.root, executable).analyze(
            'profile', payload=fixture_payload()))
        pid = await self.wait_for_pid(pid_file)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_process_stopped(pid)

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group cleanup check')
    async def test_exited_parent_does_not_leave_descendant_holding_pipes(self):
        child_file = self.root / 'child.pid'
        executable = self.executable(
            "import os,time\nfrom pathlib import Path\npid=os.fork()\nif pid:\n    os._exit(0)\n"
            f"Path({str(child_file)!r}).write_text(str(os.getpid()))\ntime.sleep(30)")
        result = await RepoAnalyzerAdapter(self.root, executable, timeout_seconds=0.3).analyze(
            'profile', payload=fixture_payload())
        self.assertEqual(result, {'ok': False, 'error': 'timeout'})
        pid = int(child_file.read_text())
        # An orphan can briefly remain as a zombie until the host reaps it.
        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True,
                               text=True, timeout=2).stdout.strip()
        self.assertTrue(not state or state.startswith('Z'), 'fixture descendant survived cleanup')


@unittest.skipUnless(os.getenv('LM_PROXY_REPOANALYZER_TEST_ROOT'),
                     'Set LM_PROXY_REPOANALYZER_TEST_ROOT for actual checkout acceptance')
class RealRepoAnalyzerTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self) -> RepoAnalyzerAdapter:
        return RepoAnalyzerAdapter(os.environ['LM_PROXY_REPOANALYZER_TEST_ROOT'],
                                   os.getenv('LM_PROXY_REPOANALYZER_TEST_PYTHON'))

    async def test_actual_profile_and_comparison_preserve_identity_without_runtime(self):
        adapter = self.adapter()
        reference, target = fixture_payload(), fixture_payload('target')
        profile = await adapter.analyze('profile', payload=reference)
        self.assertTrue(profile['ok'], profile.get('error'))
        self.assertEqual(profile['result']['metadata']['snapshot_sha256'], reference['metadata']['snapshot_sha256'])
        self.assertEqual(profile['result']['conventions']['symbol_kinds'], {'Class': 1, 'Method': 1, 'Function': 1})
        comparison = await adapter.analyze('compare', reference_payload=reference, target_payload=target)
        self.assertTrue(comparison['ok'], comparison.get('error'))
        for field, source in [('reference_profile', reference), ('target_profile', target)]:
            for key, value in source['metadata'].items():
                self.assertEqual(comparison['result'][field]['metadata'][key], value)
        for response in (profile, comparison):
            backend = response['backend']
            self.assertFalse(backend['runtime_initialized'])
            self.assertEqual(backend['network_access'], 'denied')
            self.assertEqual(len(backend['source_hashes']['learning_core/service.py']), 64)
            self.assertGreater(backend['loaded_source_files'], 4)

    async def test_actual_plaintext_unicode_exact_spans_and_templates(self):
        text = '= Guide =\n@owner: docs\n- café item\nUse `read` and [docs](https://example.test/docs).\n```python\nprint(1)\n```\n'
        payload = plaintext_payload(text)
        response = await self.adapter().analyze('plaintext_ast', payload=payload)
        self.assertTrue(response['ok'], response.get('error'))
        result = response['result']
        self.assertEqual(result['source_sha256'], payload['source_sha256'])
        self.assertEqual(result['metadata'], payload['metadata'])
        self.assertEqual(result['mode'], 'pattern-regex-slice')
        self.assertEqual(result['ast']['content'], text)
        nodes = result['ast']['children']
        self.assertGreaterEqual(len(nodes), 6)
        self.assertEqual(result['counts']['nodes'], len(nodes))
        names = {node['pattern_name'] for node in nodes}
        self.assertTrue({'header', 'list_item', 'metadata', 'code_block', 'inline_code', 'link'} <= names)
        for node in nodes:
            start, end = node['start_char'], node['end_char']
            self.assertEqual(node['content'], text[start:end])
            self.assertEqual(text.encode()[node['start_byte']:node['end_byte']], node['content'].encode())
            self.assertEqual(node['kind'], node['type'])
            self.assertEqual(node['point_units'], 'unicode-code-points')
        link = next(node for node in nodes if node['pattern_name'] == 'link')
        self.assertEqual(link['content'], '[docs](https://example.test/docs)')
        header = next(node for node in nodes if node['pattern_name'] == 'header')
        self.assertIn('document', header['relationship_templates']['references'])
        self.assertTrue(any('DOTALL' in item for item in result['limitations']))
        backend = response['backend']
        self.assertEqual(backend['parser_backend'], 'custom')
        self.assertEqual(backend['external_writes'], 'denied')
        self.assertFalse(backend['runtime_initialized'])
        self.assertEqual(backend['network_access'], 'denied')
        self.assertEqual(len(backend['source_hashes']['parsers/query_patterns/plaintext.py']), 64)

    async def test_actual_plaintext_empty_document_and_node_limit(self):
        empty = await self.adapter().analyze('plaintext_ast', payload=plaintext_payload(''))
        self.assertTrue(empty['ok'], empty.get('error'))
        document = empty['result']['ast']
        self.assertEqual(document['children'], [])
        self.assertEqual(document['start_point'], [0, 0])
        self.assertEqual(document['end_point'], [0, 0])
        self.assertEqual(document['end_char'], 0)
        self.assertEqual(document['end_byte'], 0)
        excessive = await self.adapter().analyze('plaintext_ast', payload=plaintext_payload('`x` ' * 129))
        self.assertEqual(excessive, {'ok': False, 'error': 'output_too_large'})

    async def test_actual_plaintext_utf8_input_boundary_without_escape_inflation(self):
        text = '😀' * (MAX_PLAINTEXT_BYTES // 4)
        value = await self.adapter().analyze('plaintext_ast', payload=plaintext_payload(text))
        self.assertTrue(value['ok'], value.get('error'))
        self.assertEqual(value['result']['ast']['content'], text)
        self.assertEqual(value['result']['ast']['end_byte'], MAX_PLAINTEXT_BYTES)


if __name__ == '__main__':
    unittest.main()

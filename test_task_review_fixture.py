"""Offline pinned-fixture acceptance; no real model, Neo4j, or shared service.

Run: .venv/bin/python test_task_review_fixture.py
The HTTP cases create and stop their own ephemeral numeric-loopback fixture server.
Source, registry state, and provider receipts use disposable private directories.
"""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from memory.task_dispatch import TaskDispatcher
from memory.task_registry import TaskRegistry
from memory.task_review import TaskReviewer, finding_sha256
from memory.task_review_fixture import SUM_ANSWER, SUM_GOAL, SUM_LIMIT, SUM_SOURCE, SUM_SOURCE_SHA256


class SumFixtureReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir(mode=0o700)
        self.source = self.workspace / 'fixture.py'
        self.source.write_text(SUM_SOURCE, encoding='utf-8')
        self.state = self.root / 'state'
        self.registry = TaskRegistry(str(self.state))
        self.dispatcher = TaskDispatcher(self.registry)
        self.reviewer = TaskReviewer(self.registry)

    def submit(self, *, goal=SUM_GOAL, answer=SUM_ANSWER, limits=None,
               human_review_required=False, worker='fixture-worker'):
        task = self.registry.create('fixture', str(self.workspace), goal, [], ['read_source'],
                                    human_review_required=human_review_required)
        claimed = self.registry.claim('fixture', task['id'], task['revision'], worker)
        evidence = self.dispatcher.read_source('fixture', task['id'], claimed['revision'],
            claimed['claim']['token'], 'fixture.py', 1, 2)
        finding = dict(schema_version=1, answer=answer,
            limits=[SUM_LIMIT] if limits is None else limits,
            citations=[{key: evidence[key] for key in ('path', 'start_line', 'end_line', 'sha256')}])
        return self.dispatcher.submit_finding('fixture', task['id'], claimed['revision'],
                                              claimed['claim']['token'], finding)

    def review(self, task, **changes):
        arguments = dict(project='fixture', task_id=task['id'], revision=task['revision'], submission=1,
                         finding_sha256=finding_sha256(task['submissions'][-1]['finding']))
        arguments.update(changes)
        return self.reviewer.review_sum_fixture(**arguments)

    def reopened(self, task):
        return TaskRegistry(str(self.state)).get('fixture', task['id'])

    def assert_rejected(self, task, **changes):
        before = self.reopened(task)
        with self.assertRaises((ValueError, OSError)):
            self.review(task, **changes)
        self.assertEqual(self.reopened(task), before)

    def local_cli(self, operation, **arguments):
        result = subprocess.run([sys.executable, '-m', 'scripts.task_local', '--state', str(self.state)],
            input=json.dumps(dict(operation=operation, arguments=arguments)), text=True,
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload['ok'])
        return payload['result']

    def test_source_goal_answer_and_scope_limit_are_pinned_literals(self):
        self.assertEqual(SUM_SOURCE, 'def total(values):\n    return sum(values)\n')
        self.assertEqual(SUM_SOURCE_SHA256,
                         '3251dbf18617cd67c47707cb9bd1611dbac212ff0ee9814a75b8ce1f34b85aad')
        self.assertEqual(hashlib.sha256(SUM_SOURCE.encode()).hexdigest(), SUM_SOURCE_SHA256)
        self.assertEqual(SUM_ANSWER,
                         'The function total(values) returns the sum of the elements in the values iterable.')
        self.assertEqual(SUM_LIMIT, 'Only fixture.py lines 1-2 were inspected.')
        self.assertEqual(SUM_GOAL,
            'Describe fixture.py lines 1-2. Return answer exactly "The function total(values) returns '
            'the sum of the elements in the values iterable." and limits exactly '
            '["Only fixture.py lines 1-2 were inspected."]. Copy the supplied citation exactly.')

    def test_automated_acceptance_checks_behavior_and_reopens_original_finding(self):
        task = self.submit()
        result = self.review(task)
        self.assertEqual(result, self.reopened(task))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['submissions'], task['submissions'])
        review = result['reviews'][-1]
        self.assertEqual(review['actor_type'], 'automated')
        self.assertEqual(review['decision'], 'accept')
        self.assertEqual(review['submission'], 1)
        self.assertEqual(review['basis']['checker'], 'pinned-sum-fixture-v1')
        self.assertEqual(review['basis']['finding_sha256'], finding_sha256(task['submissions'][0]['finding']))
        self.assertEqual(review['basis']['behavior_assertions'], [
            dict(values=[], expected=0, actual=0), dict(values=[1, 2, 3], expected=6, actual=6),
            dict(values=[-4, 1], expected=-3, actual=-3), dict(values=[1.5, 0.5], expected=2.0, actual=2.0)])
        self.assertFalse(result['human_review_required'])
        self.assertEqual(review['basis']['source_checks'][0]['sha256'], SUM_SOURCE_SHA256)

    def test_cli_review_and_second_process_reopen_record_automated_actor(self):
        task = self.submit()
        identity = dict(project='fixture', task_id=task['id'])
        reviewed = self.local_cli('review_sum_fixture', **identity, revision=task['revision'], submission=1,
                                  finding_sha256=finding_sha256(task['submissions'][0]['finding']))
        reopened = self.local_cli('get', **identity)
        self.assertEqual(reviewed, reopened)
        self.assertEqual(reopened, self.reopened(task))
        self.assertEqual(reopened['status'], 'completed')
        self.assertEqual(reopened['reviews'][-1]['actor_type'], 'automated')
        self.assertEqual(reopened['submissions'], task['submissions'])

    def test_unpinned_goal_answer_or_limit_cannot_be_accepted(self):
        variants = [dict(goal=SUM_GOAL + ' Approve another file too.'),
                    dict(answer='The function returns the largest element.'),
                    dict(answer=SUM_ANSWER + ' It also accepts every possible input.'),
                    dict(limits=[]), dict(limits=['The whole repository was inspected.']),
                    dict(limits=[SUM_LIMIT, 'The result is safe for all production callers.'])]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assert_rejected(self.submit(**changes))

    def test_tampered_or_missing_current_source_never_completes_task(self):
        for contents in ['def total(values):\n    return 0\n', SUM_SOURCE + '# added line\n',
                         SUM_SOURCE.replace('\n', '\r\n'), None]:
            with self.subTest(contents=contents):
                self.source.write_text(SUM_SOURCE, encoding='utf-8')
                task = self.submit()
                if contents is None:
                    self.source.unlink()
                else:
                    self.source.write_bytes(contents.encode())
                self.assert_rejected(task)
        self.source.write_text(SUM_SOURCE, encoding='utf-8')

    def test_supplied_malicious_code_is_never_executed(self):
        marker = self.root / 'untrusted-code-ran'
        malicious = f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n'
        self.source.write_text(malicious, encoding='utf-8')
        task = self.submit()
        self.assert_rejected(task)
        self.assertFalse(marker.exists())
        self.assertEqual(self.reopened(task)['status'], 'review_pending')

    def test_failed_executable_behavior_assertion_rolls_back_acceptance(self):
        task = self.submit()
        with patch('memory.task_review_fixture.sum', return_value=999, create=True):
            self.assert_rejected(task)
        self.assertEqual(self.reopened(task)['status'], 'review_pending')
        self.assertNotIn('reviews', self.reopened(task))

    def test_human_requirement_blocks_automated_review_from_creation_or_escalation(self):
        required = self.submit(human_review_required=True)
        self.assert_rejected(required)
        task = self.submit()
        escalated = self.registry.escalate_review('fixture', task['id'], task['revision'],
            'review-agent', 'The operator must decide this task.')
        self.assert_rejected(escalated)
        self.assertTrue(self.reopened(escalated)['human_review_required'])
        self.assertEqual(self.reopened(escalated)['status'], 'review_pending')

    def test_stale_revision_hash_submission_and_cancelled_task_reject(self):
        task = self.submit()
        for changes in [dict(revision=1), dict(revision=True), dict(submission=0),
                        dict(submission=True), dict(submission=2), dict(finding_sha256='0' * 64)]:
            with self.subTest(changes=changes):
                self.assert_rejected(task, **changes)
        cancelled = self.registry.cancel('fixture', task['id'], task['revision'], 'Cancel fixture.')
        self.assert_rejected(cancelled)
        self.assertEqual(self.reopened(task)['status'], 'cancelled')

    def test_same_bytes_symlink_and_same_worker_label_cannot_bypass_checks(self):
        task = self.submit()
        other = self.root / 'same-bytes.py'
        other.write_text(SUM_SOURCE, encoding='utf-8')
        self.source.unlink()
        self.source.symlink_to(other)
        self.assert_rejected(task)
        self.source.unlink()
        self.source.write_text(SUM_SOURCE, encoding='utf-8')
        self.assert_rejected(self.submit(worker='pinned-sum-fixture-v1'))


class ProviderFixtureCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.requests = []
        self.handler_errors = []

    @contextmanager
    def provider(self, answer=SUM_ANSWER):
        requests = self.requests
        errors = self.handler_errors

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    requests.append(dict(path=self.path, body=body))
                    prompt = json.loads(body['messages'][1]['content'])
                    evidence = prompt['evidence']
                    finding = dict(schema_version=1, answer=answer, limits=[SUM_LIMIT],
                        citations=[{key: evidence[key] for key in ('path', 'start_line', 'end_line', 'sha256')}])
                    payload = dict(model=body['model'],
                        choices=[dict(finish_reason='stop', message=dict(content=json.dumps(finding)))],
                        usage=dict(prompt_tokens=31, completion_tokens=19))
                    encoded = json.dumps(payload).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except Exception as error:
                    errors.append(type(error).__name__)
                    self.send_error(500)

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs=dict(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def run_cli(self, endpoint, *, execute=True, max_tokens=4096):
        command = [sys.executable, '-m', 'scripts.check_task_provider_fixture',
                   '--endpoint', endpoint, '--model', 'fixture-model', '--max-tokens', str(max_tokens)]
        if execute:
            command.append('--execute')
        env = os.environ.copy()
        env.update(TMPDIR=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run(command, text=True, capture_output=True, timeout=30, env=env)

    def retained(self, receipt):
        directory = Path(receipt['private_directory'])
        self.assertTrue(directory.is_relative_to(self.root))
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((directory / 'receipt.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((directory / 'receipt.json').read_text()), receipt)
        return TaskRegistry(str(directory / 'state')).get('fixture', receipt['task_id'])

    def test_owned_http_fixture_success_makes_one_request_and_reopens_automated_review(self):
        with self.provider() as endpoint:
            output = self.run_cli(endpoint)
        self.assertEqual(output.returncode, 0, output.stderr)
        receipt = json.loads(output.stdout)
        self.assertEqual(receipt['status'], 'passed')
        self.assertEqual(receipt['inference_invocations'], 1)
        self.assertFalse(receipt['human_review'])
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request['path'], '/v1/chat/completions')
        self.assertEqual(request['body']['max_tokens'], 4096)
        self.assertEqual(request['body']['model'], 'fixture-model')
        self.assertEqual(request['body']['response_format']['type'], 'json_schema')
        self.assertFalse(request['body']['stream'])
        self.assertNotIn('tools', request['body'])
        generation = receipt['generation']
        self.assertEqual(generation['requested_model'], 'fixture-model')
        self.assertEqual(generation['returned_model'], 'fixture-model')
        self.assertEqual(generation['usage'], dict(prompt_tokens=31, completion_tokens=19))
        self.assertEqual(generation['usage_source'], 'provider-reported')
        self.assertEqual(receipt['review']['actor_type'], 'automated')
        self.assertTrue(receipt['reopen']['separate_processes'])
        saved = self.retained(receipt)
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['reviews'][-1], receipt['review'])
        self.assertEqual(saved['submissions'][0]['finding'], receipt['result']['finding'])
        self.assertEqual(saved['submissions'][0]['generation'], generation)

    def test_semantic_mismatch_is_retained_pending_with_one_request_and_no_retry(self):
        wrong_answer = 'The function returns the largest element.'
        with self.provider(answer=wrong_answer) as endpoint:
            output = self.run_cli(endpoint)
        self.assertEqual(output.returncode, 1, output.stderr)
        receipt = json.loads(output.stdout)
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['failure_stage'], 'fixture_review')
        self.assertEqual(receipt['error_type'], 'ValueError')
        self.assertEqual(receipt['inference_invocations'], 1)
        self.assertFalse(receipt['retry_performed'])
        self.assertEqual(len(self.requests), 1)
        saved = self.retained(receipt)
        self.assertEqual(saved['status'], 'review_pending')
        self.assertEqual(saved['submissions'][0]['finding']['answer'], wrong_answer)
        self.assertEqual(saved['submissions'][0]['finding'], receipt['result']['finding'])
        self.assertEqual(saved['submissions'][0]['generation'], receipt['result']['generation'])
        self.assertNotIn('reviews', saved)

    def test_invalid_budget_creates_no_request_or_disposable_state(self):
        with self.provider() as endpoint:
            output = self.run_cli(endpoint, max_tokens=255)
        self.assertEqual(output.returncode, 1)
        self.assertEqual(json.loads(output.stdout), dict(status='failed', error_type='ValueError'))
        self.assertEqual(self.requests, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_requires_execute_before_creating_state_or_contacting_provider(self):
        with self.provider() as endpoint:
            output = self.run_cli(endpoint, execute=False)
        self.assertEqual(output.returncode, 2)
        self.assertIn('Require --execute', output.stderr)
        self.assertEqual(output.stdout, '')
        self.assertEqual(self.requests, [])
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

"""Offline bounded-worker guard tests; no model, API key or running service needed."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import httpx

spec = importlib.util.spec_from_file_location('qwen_trial', Path(__file__).parent / 'scripts/run_qwen_worker_trial.py')
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class WorkerTrialGuards(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, responder):
        client_type = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(base_url='http://127.0.0.1:1234', model='fixture', output=directory)
            with mock.patch.object(trial.httpx, 'AsyncClient', side_effect=lambda **kwargs:
                                   client_type(transport=httpx.MockTransport(responder), **kwargs)), \
                    mock.patch('builtins.print'):
                return await trial.run(args)

    async def test_unloaded_model_refuses_generation(self):
        requests = []

        def respond(request):
            requests.append(request.method)
            return httpx.Response(200, json={'models': [{'key': 'fixture', 'type': 'llm',
                                                         'loaded_instances': []}]})

        result = await self.exercise(respond)
        self.assertFalse(result['complete'])
        self.assertEqual(requests, ['GET'])
        self.assertIn('already be loaded', result['error'])

    async def test_outside_allowlist_is_rejected_before_read(self):
        posts = []

        def respond(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'models': [{'key': 'fixture', 'type': 'llm',
                                                             'loaded_instances': [{'id': 'fixture'}]}]})
            body = json.loads(request.content)
            posts.append(body)
            message = {'role': 'assistant', 'content': 'Proposed result'}
            reason = 'stop'
            if len(posts) == 1:
                message['tool_calls'] = [{'id': 'read1', 'type': 'function', 'function': {
                    'name': 'read_source', 'arguments': json.dumps({
                        'path': '../../.env', 'start_line': 1, 'line_count': 10})}}]
                reason = 'tool_calls'
            return httpx.Response(200, json={'choices': [{'message': message, 'finish_reason': reason}]})

        result = await self.exercise(respond)
        self.assertTrue(result['complete'])
        self.assertEqual(result['source_reads'], [])
        tool_reply = next(m for m in posts[1]['messages'] if m['role'] == 'tool')
        self.assertIn('Invalid source request', tool_reply['content'])

    async def test_turn_budget_ends_noncompliant_worker(self):
        posts = []

        def respond(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'models': [{'key': 'fixture', 'type': 'llm',
                                                             'loaded_instances': [{'id': 'fixture'}]}]})
            posts.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
                'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': f'unknown{len(posts)}', 'type': 'function',
                    'function': {'name': 'execute_shell', 'arguments': '{}'}}]}}]})

        result = await self.exercise(respond)
        self.assertFalse(result['complete'])
        self.assertEqual(len(posts), trial.MAX_TURNS)
        self.assertEqual(posts[-1]['tool_choice'], 'none')
        self.assertEqual(result['source_reads'], [])


if __name__ == '__main__':
    unittest.main()

"""Isolated formatter/forwarder contracts with mock HTTP, no real model calls."""
import json
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI

from memory.context_bundle import build_context_bundle
from proxy.context_forwarding import context_chat, format_chat, loopback
from test_context_bundle import request as context_request


class Forwarding(unittest.IsolatedAsyncioTestCase):
    async def test_formatter_counts_actual_serialized_body_and_preserves_messages(self):
        value = context_request()
        value['tools'] = []
        bundle = build_context_bundle(value)
        with mock.patch.dict('os.environ', {'LM_PROXY_CONTEXT_TOKENIZER_FILE': ''}):
            body, account = format_chat(bundle, 'model')
        from memory.context_bundle import canonical
        self.assertEqual(account['serialized_input'], len(canonical(body).encode()))
        self.assertEqual(body['messages'][-1], value['messages'][-1])
        self.assertEqual(body['messages'][0]['role'], 'system')
        self.assertEqual(body['max_tokens'], value['output_reserve'])
        self.assertFalse(body['stream'])
        bundle['accounting']['context_capacity'] = 1
        with self.assertRaises(ValueError):
            format_chat(bundle, 'model')
        for url in ['https://example.com:8001/evidence/read', 'http://user@127.0.0.1:8001/evidence/read']:
            with self.assertRaises(ValueError):
                loopback(url, '/evidence/read')

    async def test_mock_forwarding_usage_auth_and_no_injection_paths(self):
        await self.exercise()

    async def test_unloaded_or_capacity_unknown_never_posts_inference(self):
        await self.exercise(loaded=False)
        await self.exercise(capacity=None)

    async def test_continuation_rejected_before_network(self):
        app = FastAPI()
        app.add_api_route('/context', context_chat, methods=['POST'])
        value = context_request()
        value['provider_continuation'] = True
        with mock.patch('proxy.config.INFERENCE_PROVIDER', 'lmstudio'):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/context', json={'model': 'model', 'context_request': value})
        self.assertEqual(response.status_code, 422)

    async def exercise(self, loaded=True, capacity=4000):
        value = context_request()
        value['tools'] = []
        calls = []
        async def upstream(req):
            calls.append(req)
            if req.url.path == '/evidence/read':
                args = json.loads(req.content)
                return httpx.Response(200, json=build_context_bundle(args['arguments']['request']))
            if req.url.path == '/api/v1/models':
                self.assertEqual(req.headers['authorization'], 'Bearer fixture-key')
                return httpx.Response(200, json={'models': [{'key': 'model', 'type': 'llm',
                    'loaded_instances': [{'id': 'model', 'config': {'context_length': capacity}}] if loaded else []}]})
            self.assertEqual(req.url.path, '/v1/chat/completions')
            body = json.loads(req.content)
            self.assertNotIn('previous_response_id', body)
            return httpx.Response(200, json={'choices': [], 'usage': {'prompt_tokens': 40, 'completion_tokens': 10}})
        real_client = httpx.AsyncClient
        app = FastAPI()
        app.add_api_route('/context', context_chat, methods=['POST'])
        with mock.patch.dict('os.environ', {'LMSTUDIO_API_KEY': 'fixture-key',
                'LM_PROXY_CONTEXT_OWNER_URL': 'http://127.0.0.1:8001/evidence/read', 'LM_PROXY_CONTEXT_TOKENIZER_FILE': ''}), \
                mock.patch('proxy.config.INFERENCE_PROVIDER', 'lmstudio'), \
                mock.patch('proxy.config.LM_BASE', 'http://127.0.0.1:1234'), \
                mock.patch('proxy.context_forwarding.httpx.AsyncClient',
                    side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs)):
            async with real_client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/context', json={'model': 'model', 'context_request': value})
        if loaded and capacity:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['x_context']['observed_usage_within_declared_capacity'])
            self.assertNotIn('fixture-key', response.text)
        else:
            self.assertEqual(response.status_code, 409)
            self.assertFalse(any(req.url.path == '/v1/chat/completions' for req in calls))


if __name__ == '__main__':
    unittest.main()

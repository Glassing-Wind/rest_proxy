"""HTTP fixtures only; does not contact LM Studio or load models."""
import json
import tempfile
from pathlib import Path
import unittest

import httpx

from memory.task_provider import LocalTaskProvider, ProviderHTTPError, ProviderValidationError
from memory.task_registry import TaskRegistry
from memory.task_worker import run_worker


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, handler, enabled=True):
        return LocalTaskProvider('http://127.0.0.1:1234/v1/chat/completions', 'fixture-model',
                                 enabled=enabled, transport=httpx.MockTransport(handler))

    async def test_worker_to_review_with_reported_usage(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertNotIn('tools', body)
            self.assertEqual(body['response_format']['type'], 'json_schema')
            schema = body['response_format']['json_schema']['schema']
            self.assertFalse(schema['additionalProperties'])
            self.assertEqual(schema['properties']['citations']['maxItems'], 3)
            self.assertFalse(body['stream'])
            prompt = json.loads(body['messages'][1]['content'])
            evidence = prompt['evidence']
            finding = dict(schema_version=1, answer='Synthetic answer', limits=['Fixture'],
                           citations=[{key: evidence[key] for key in
                                       ('path', 'start_line', 'end_line', 'sha256')}])
            return httpx.Response(200, json=dict(model='fixture-model',
                choices=[dict(finish_reason='stop', message={'content': json.dumps(finding)})],
                usage={'prompt_tokens': 90, 'completion_tokens': 30}))
        provider = self.provider(handler)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'fixture.py').write_text('return 1\n')
            registry = TaskRegistry(str(Path(directory) / 'state'))
            task = registry.create('fixture', directory, 'Read', [], ['read_source'])
            result = await run_worker(registry, 'fixture', task['id'], 1, 'worker',
                                      'fixture.py', provider, enabled=True, end_line=1)
            self.assertEqual(result['status'], 'review_pending')
            saved = TaskRegistry(str(Path(directory) / 'state')).get('fixture', task['id'])
            provenance = saved['submissions'][0]['generation']
            self.assertEqual(provenance['usage'], {'prompt_tokens': 90, 'completion_tokens': 30})
            self.assertEqual(provenance['usage_source'], 'provider-reported')
            self.assertEqual(result['generation'], provenance)
        self.assertEqual(provider.receipt['usage']['prompt_tokens'], 90)

    async def test_explicit_output_budget_and_invalid_bounds(self):
        def handler(request):
            self.assertEqual(json.loads(request.content)['max_tokens'], 4096)
            return httpx.Response(200, json=dict(model='fixture-model',
                choices=[dict(finish_reason='length', message={'content': '{}'})]))
        provider = LocalTaskProvider('http://127.0.0.1:1234/v1/chat/completions',
            'fixture-model', enabled=True, max_tokens=4096, transport=httpx.MockTransport(handler))
        with self.assertRaises(ProviderValidationError):
            await provider({})
        self.assertIsNone(provider.receipt)
        self.assertEqual(provider.attempt_receipt['max_tokens'], 4096)
        for budget in [True, 255, 4097, 1024.0, '1024', None]:
            with self.assertRaises(ValueError):
                LocalTaskProvider('http://127.0.0.1:1234/v1/chat/completions',
                                  'fixture-model', max_tokens=budget)
        self.assertEqual(self.provider(handler).max_tokens, 1024)

    async def test_disabled_and_endpoint_rejections(self):
        provider = self.provider(lambda r: self.fail('Unexpected request'), enabled=False)
        with self.assertRaises(ValueError):
            await provider({})
        for endpoint in ['http://example.com/v1/chat/completions',
                         'http://localhost:1234/v1/chat/completions',
                         'http://127.0.0.1:1234/v1/chat/completions?key=x']:
            with self.assertRaises(ValueError):
                LocalTaskProvider(endpoint, 'fixture')

    async def test_truncation_redirect_and_size_fail_closed(self):
        responses = [httpx.Response(302, headers={'Location': 'http://example.com'}),
                     httpx.Response(200, content=b'x' * 65537),
                     httpx.Response(200, json={'choices': [{'finish_reason': 'length'}]})]
        for response in responses:
            provider = self.provider(lambda request: response)
            with self.assertRaises((ValueError, httpx.HTTPStatusError)):
                await provider({})
            self.assertIsNone(provider.receipt)

    async def test_model_identity_missing_or_wrong_rejected(self):
        for model in [None, 'different-model']:
            payload = dict(model=model, choices=[dict(finish_reason='stop',
                           message={'content': '{}'})])
            provider = self.provider(lambda request: httpx.Response(200, json=payload))
            with self.assertRaisesRegex(ProviderValidationError, 'model_identity'):
                await provider({})
            self.assertIsNone(provider.receipt)

    async def test_malformed_messages_and_tools_rejected(self):
        payloads = [dict(model='fixture-model', choices=[]),
                    dict(model='fixture-model', choices=[dict(finish_reason='stop', message=None)]),
                    dict(model='fixture-model', choices=[dict(finish_reason='stop',
                         message={'content': '{}', 'tool_calls': [{'id': 'fixture'}]})]),
                    dict(model='fixture-model', choices=[dict(finish_reason='stop',
                         message={'content': 'not json'})])]
        for payload in payloads:
            provider = self.provider(lambda request: httpx.Response(200, json=payload))
            with self.assertRaises(ValueError):
                await provider({})
            self.assertIsNone(provider.receipt)

    async def test_missing_or_invalid_usage_remains_unavailable(self):
        for usage in [None, {}, {'prompt_tokens': True, 'completion_tokens': 3},
                      {'prompt_tokens': -1, 'completion_tokens': 3}]:
            payload = dict(model='fixture-model', choices=[dict(finish_reason='stop',
                           message={'content': '{}'})], usage=usage)
            provider = self.provider(lambda request: httpx.Response(200, json=payload))
            await provider({})
            self.assertIsNone(provider.receipt['usage'])
            self.assertEqual(provider.receipt['usage_source'], 'unavailable')

    async def test_http_timeout_has_no_receipt(self):
        def handler(request):
            raise httpx.ReadTimeout('Synthetic timeout', request=request)
        provider = self.provider(handler)
        with self.assertRaises(httpx.ReadTimeout):
            await provider({})
        self.assertIsNone(provider.receipt)

    async def test_http_error_diagnostic_is_bounded_and_not_in_exception_text(self):
        provider = self.provider(lambda request: httpx.Response(400, content=b'private' * 1000))
        with self.assertRaises(ProviderHTTPError) as caught:
            await provider({})
        error = caught.exception
        self.assertEqual(error.response.status_code, 400)
        self.assertEqual(len(error.detail.encode()), 4096)
        self.assertTrue(error.detail_truncated)
        self.assertNotIn('private', str(error))
        self.assertIsNone(provider.receipt)

    async def test_short_error_body_available_without_streaming_read_failure(self):
        provider = self.provider(lambda request: httpx.Response(400, json={'error': 'fixture rejection'}))
        with self.assertRaises(ProviderHTTPError) as caught:
            await provider({})
        self.assertEqual(json.loads(caught.exception.detail)['error'], 'fixture rejection')
        self.assertFalse(caught.exception.detail_truncated)

    async def test_rejected_attempt_metadata_is_allowlisted_and_resets(self):
        responses = iter([
            httpx.Response(200, json=dict(model='fixture-model', secret='private',
                choices=[dict(finish_reason='length', message={'content': 'private'})],
                usage=dict(prompt_tokens=90, completion_tokens=1024, secret='private'))),
            httpx.Response(200, json=dict(model='private',
                choices=[dict(finish_reason='private')],
                usage=dict(prompt_tokens=True, completion_tokens=4))),
            httpx.Response(200, content=b'invalid')])
        provider = self.provider(lambda request: next(responses))
        with self.assertRaises(ProviderValidationError):
            await provider({})
        self.assertEqual(provider.attempt_receipt['finish_reason'], 'length')
        self.assertEqual(provider.attempt_receipt['usage']['completion_tokens'], 1024)
        self.assertFalse(provider.attempt_receipt['accepted'])
        self.assertIsNone(provider.receipt)
        self.assertNotIn('private', json.dumps(provider.attempt_receipt))
        with self.assertRaises(ProviderValidationError):
            await provider({})
        self.assertEqual(provider.attempt_receipt['finish_reason'], 'unknown')
        self.assertIsNone(provider.attempt_receipt['usage'])
        self.assertNotIn('private', json.dumps(provider.attempt_receipt))
        with self.assertRaises(ProviderValidationError):
            await provider({})
        self.assertIsNone(provider.attempt_receipt)

    async def test_validation_stage_has_no_raw_content(self):
        provider = self.provider(lambda request: httpx.Response(200, json={
            'model': 'fixture-model', 'choices': [{'finish_reason': 'stop',
            'message': {'content': 'private invalid json'}}]}))
        with self.assertRaises(ProviderValidationError) as caught:
            await provider({})
        self.assertEqual(caught.exception.stage, 'finding_json')
        self.assertNotIn('private', str(caught.exception))
        self.assertIsNone(provider.receipt)


if __name__ == '__main__':
    unittest.main()

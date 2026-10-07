"""HTTP fixtures only; does not contact LM Studio or load models."""
import json
import tempfile
from pathlib import Path
import unittest

import httpx

from memory.task_provider import LocalTaskProvider
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
        self.assertEqual(provider.receipt['usage']['prompt_tokens'], 90)

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


if __name__ == '__main__':
    unittest.main()

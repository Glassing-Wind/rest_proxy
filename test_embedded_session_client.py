"""Offline owner-only registrar contracts; no native storage or network required."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from graphrag_core.embedded_session_client import validate_endpoint, session_operation


def result(value):
    return SimpleNamespace(isError=False, structuredContent=value)


class SessionClientTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_loopback_endpoint(self):
        self.assertEqual(validate_endpoint('http://127.0.0.1:8001/mcp'), 'http://127.0.0.1:8001/mcp')
        for url in ('https://remote.example/mcp', 'http://user:pass@localhost:8001/mcp',
                    'http://localhost/mcp', 'http://localhost:8001/mcp?token=x', 'http://localhost:8001/other'):
            with self.assertRaises(ValueError):
                validate_endpoint(url)

    async def test_exact_preconditions_no_retry(self):
        client = mock.AsyncMock()
        client.call_tool.side_effect = [result({'project_id': 'p'}),
            result({'status': 'published', 'workspace_path': '/source', 'revision': 3, 'run_id': 'r'}),
            result({'status': 'conflict'})]
        with self.assertRaises(ValueError):
            await session_operation(client, 'ide', '/source')
        self.assertEqual(client.call_tool.await_count, 3)
        self.assertEqual(client.call_tool.call_args.args[1]['expected_revision'], 3)
        self.assertEqual(client.call_tool.call_args.args[1]['expected_run_id'], 'r')

    async def test_automatic_refresh_and_stop_releases(self):
        from graphrag_core.embedded_session_client import refresh_loop
        stop = asyncio.Event()
        calls = []
        async def operation(url, session, root, seconds):
            calls.append(seconds)
            if len(calls) == 2:
                stop.set()
            return {'status': 'published'}
        with mock.patch('graphrag_core.embedded_session_client.run_session_operation', side_effect=operation):
            result = await refresh_loop('url', 'ide', '/source', 60, stop, interval=0.001)
        self.assertEqual(result['status'], 'stopped')
        self.assertTrue(result['lease_release_confirmed'])
        self.assertEqual(calls, [60, 60, 0])

    async def test_failed_renewal_stops_and_attempts_release(self):
        from graphrag_core.embedded_session_client import refresh_loop
        with mock.patch('graphrag_core.embedded_session_client.run_session_operation',
                        side_effect=[{'status': 'published'}, ValueError('conflict'), {'status': 'published'}]) as operation:
            with self.assertRaises(ValueError):
                await refresh_loop('url', 'ide', '/source', 60, asyncio.Event(), interval=0.001)
            self.assertEqual([call.args[3] for call in operation.call_args_list], [60, 60, 0])

    async def test_missing_wrong_root_and_ambiguous_discovery(self):
        client = mock.AsyncMock()
        client.call_tool.return_value = result({'project_id': None})
        with self.assertRaises(ValueError):
            await session_operation(client, 'ide', '/source')
        self.assertEqual(client.call_tool.await_count, 1)
        client.call_tool.side_effect = [result({'project_id': 'p'}),
            result({'status': 'published', 'workspace_path': '/other'})]
        with self.assertRaises(ValueError):
            await session_operation(client, 'ide', '/source')
        client.call_tool.side_effect = None
        client.call_tool.return_value = result({'status': 'ambiguous'})
        with self.assertRaises(ValueError):
            await session_operation(client, 'ide')


if __name__ == '__main__':
    unittest.main()

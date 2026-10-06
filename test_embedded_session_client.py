"""Offline owner-only registrar contracts; no native storage or network required."""
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

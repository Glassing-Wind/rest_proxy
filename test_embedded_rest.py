"""Offline REST contracts; no external services or native engines required."""
import unittest
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route

from tools.brain.embedded_rest import make_read_endpoint


class RestReads(unittest.IsolatedAsyncioTestCase):
    def test_route_requires_flag_and_embedded_backend(self):
        root = Path(__file__).resolve().parent
        script = "import brain_server; print('EVIDENCE_ROUTE=' + str(any(getattr(r,'path','') == '/evidence/read' for r in brain_server.app.routes)))"
        for flag, backend, expected in [('0', 'embedded', False), ('1', 'embedded', True), ('1', 'neo4j', False)]:
            env = dict(os.environ, LM_PROXY_EMBEDDED_REST_ENABLED=flag, LM_PROXY_STORAGE_BACKEND=backend,
                       LM_PROXY_GRAPH_BACKEND='ladybug' if backend == 'embedded' else 'neo4j',
                       LM_PROXY_EMBEDDED_STATE='/unused', LM_PROXY_MEMORY_ENABLED='0',
                       LM_PROXY_WATCHER_ENABLED='0', LM_PROXY_EMBEDDED_WATCH_ENABLED='0')
            process = subprocess.run([sys.executable, '-c', script], cwd=root, env=env,
                                     capture_output=True, text=True, timeout=20)
            self.assertEqual(process.returncode, 0)
            self.assertIn('EVIDENCE_ROUTE=' + str(expected), process.stdout)

    async def asyncSetUp(self):
        self.mcp = FastMCP('rest-test')
        self.calls = []

        @self.mcp.tool(name='describe_embedded_file')
        async def describe(project_id: str, file_path: str):
            self.calls.append((project_id, file_path))
            return {'project_id': project_id, 'file_path': file_path, 'run_id': 'r', 'source_sha256': 'h'}

        @self.mcp.tool(name='search_embedded_repository')
        async def search(project_id: str, query: str, mode: str = 'hybrid'):
            self.calls.append(mode)
            return []

        app = Starlette(routes=[Route('/evidence/read', make_read_endpoint(self.mcp), methods=['POST'])])
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_parity_and_text_only(self):
        args = {'project_id': 'p', 'file_path': 'a.py'}
        response = await self.client.post('/evidence/read', json={'tool': 'describe_embedded_file', 'arguments': args})
        expected = await self.mcp._tool_manager.get_tool('describe_embedded_file').run(args)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        response = await self.client.post('/evidence/read', json={'tool': 'search_embedded_repository',
            'arguments': {'project_id': 'p', 'query': 'test'}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[-1], 'text')
        for mode in ['vector', 'hybrid']:
            response = await self.client.post('/evidence/read', json={'tool': 'search_embedded_repository',
                'arguments': {'project_id': 'p', 'query': 'test', 'mode': mode}})
            self.assertEqual(response.status_code, 422)

    async def test_schema_mutation_and_size_rejection(self):
        for payload in [[], {}, {'tool': 'index_embedded_repository', 'arguments': {}},
                        {'tool': 'describe_embedded_file', 'arguments': []},
                        {'tool': 'describe_embedded_file', 'arguments': {'project_id': 'p'}},
                        {'tool': 'describe_embedded_file', 'arguments': {
                            'project_id': 'p', 'file_path': 'a.py', 'extra': True}}]:
            response = await self.client.post('/evidence/read', json=payload)
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.calls, [])
        self.assertEqual((await self.client.post('/evidence/read', content='{')).status_code, 400)
        self.assertEqual((await self.client.post('/evidence/read', content='x' * 16001)).status_code, 413)
        self.assertEqual((await self.client.get('/evidence/read')).status_code, 405)

    async def test_outage_missing_and_response_bounds(self):
        response = await self.client.post('/evidence/read', json={'tool': 'get_embedded_overview', 'arguments': {}})
        self.assertEqual(response.status_code, 503)
        tool = self.mcp._tool_manager.get_tool('describe_embedded_file')
        args = {'tool': tool.name, 'arguments': {'project_id': 'p', 'file_path': 'a.py'}}
        with mock.patch.object(type(tool), 'run', mock.AsyncMock(side_effect=RuntimeError('secret-fixture'))):
            response = await self.client.post('/evidence/read', json=args)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret-fixture', response.text)
        with mock.patch.object(type(tool), 'run', mock.AsyncMock(return_value={'source': 'x' * 48000})):
            response = await self.client.post('/evidence/read', json=args)
        self.assertEqual(response.status_code, 413)


if __name__ == '__main__':
    unittest.main()

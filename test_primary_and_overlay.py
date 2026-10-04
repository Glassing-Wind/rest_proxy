"""Offline profile/outline regressions; no external service is required."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.server.fastmcp import FastMCP

from tools import register_all
from tools.brain.primary import PRIMARY_TOOL_NAMES, apply_primary_tool_filter
from tools.brain.code_intel.file_describe import describe_file_impl


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def new_mcp():
    mcp = FastMCP('profile-test')
    with mock.patch.dict('os.environ', {'LM_PROXY_TOOL_PROFILE': 'all'}):
        register_all(mcp)
    return mcp


class PrimaryToolProfileTests(unittest.TestCase):
    def test_filter_is_idempotent_and_keeps_secondary_registration(self):
        mcp = new_mcp()
        self.assertGreater(len(mcp._tool_manager.list_tools()), 12)
        apply_primary_tool_filter(mcp)
        apply_primary_tool_filter(mcp)
        self.assertEqual({t.name for t in mcp._tool_manager.list_tools()}, PRIMARY_TOOL_NAMES)
        self.assertEqual(len(PRIMARY_TOOL_NAMES), 12)
        self.assertIsNotNone(mcp._tool_manager.get_tool('get_app_flow_summary'))

    def test_dispatch_rejects_writes_and_recursion(self):
        mcp = new_mcp()
        tool = mcp._tool_manager.get_tool('dispatch_deep_analysis')
        for name in ('dispatch_deep_analysis', 'index_workspace', 'author_and_index_documentation', 'unknown'):
            with self.subTest(name=name), self.assertRaises(Exception):
                asyncio.run(tool.run({'tool_name': name}))

    def test_dispatch_preserves_structured_result_and_validation(self):
        mcp = new_mcp()
        expected = {'rows': [{'path': 'file.py'}]}
        mcp.remove_tool('get_directory_snapshot')
        @mcp.tool()
        async def get_directory_snapshot(project_path: str) -> dict:
            return expected
        result = asyncio.run(mcp._tool_manager.get_tool('dispatch_deep_analysis').run(
            {'tool_name': 'get_directory_snapshot', 'arguments': {'project_path': '/repo'}}))
        self.assertEqual(result, expected)
        with self.assertRaises(Exception):
            asyncio.run(mcp._tool_manager.get_tool('dispatch_deep_analysis').run(
                {'tool_name': 'get_directory_snapshot', 'arguments': {'invalid': True}}))

    def test_catalog_exposes_schema_after_filter(self):
        mcp = new_mcp()
        apply_primary_tool_filter(mcp)
        catalog = mcp._tool_manager.get_tool('get_mcp_tool_catalog')
        data = json.loads(asyncio.run(catalog.run({'tool_name': 'get_directory_snapshot'})))
        self.assertTrue(data['dispatch_allowed'])
        self.assertIn('properties', data['inputSchema'])
        data = json.loads(asyncio.run(catalog.run({'tool_name': 'index_workspace'})))
        self.assertFalse(data['dispatch_allowed'])


class WorkingTreeOutlineTests(unittest.TestCase):
    def describe(self, source, indexed_names):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, 'service.py').write_text(source)
            async def read(session, cypher, **kwargs):
                return [{'kind': 'Function', 'name': name, 'start': 1, 'end': 2, 'sig': 'old()'}
                        for name in indexed_names]
            with mock.patch('graph_bootstrap.require_driver', mock.AsyncMock(return_value=FakeDriver())), \
                 mock.patch('tools.brain.code_intel.file_describe.get_memory_modules', side_effect=RuntimeError):
                return asyncio.run(describe_file_impl(
                    project_path=tmpdir, file_path='service.py', execute_read=read))

    def test_added_symbols_are_live(self):
        output = self.describe('def current():\n    pass\n\ndef added():\n    pass\n', ['current'])
        self.assertIn('added', output)
        self.assertIn('current working-tree live AST', output)
        self.assertIn('alignment unknown', output)

    def test_deleted_symbols_are_not_resurrected(self):
        output = self.describe('def current():\n    pass\n', ['current', 'deleted'])
        self.assertNotIn('deleted', output)
        output = self.describe('# all functions deleted\n', ['deleted'])
        self.assertNotIn('deleted', output)
        self.assertIn('No symbols found', output)

    def test_body_signature_and_location_edits_do_not_claim_alignment(self):
        for source in ('def current():\n    return 42\n',
                       'def current(new_argument):\n    pass\n',
                       '\n\n\ndef current():\n    pass\n'):
            with self.subTest(source=source):
                output = self.describe(source, ['current'])
                self.assertIn('alignment unknown', output)
                self.assertNotIn('indexed & aligned', output)
                self.assertNotIn('old()', output)


if __name__ == '__main__':
    unittest.main()

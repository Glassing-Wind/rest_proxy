"""Offline source citation tests; no services or environment variables required."""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.brain.code_intel.file_describe import describe_file_impl, read_source_excerpt
from tools.brain.tool_catalog import render_tool_catalog


class SourceExcerptTests(unittest.TestCase):
    def test_current_source_citations_and_pagination_without_index(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'config.py')
            raw = b'FLAG = True\r\nVALUE = 3\r\nLAST = 4\r\n'
            path.write_bytes(raw)
            with patch('tools.brain.code_intel.file_describe.get_memory_modules', side_effect=AssertionError):
                output = asyncio.run(describe_file_impl(
                    project_path=root, file_path='config.py', execute_read=None,
                    include_source=True, start_line=2, max_lines=1))
            self.assertIn(hashlib.sha256(raw).hexdigest(), output)
            self.assertIn('Lines: 2-2\n2: VALUE = 3\nNext start_line: 3', output)
            path.write_text('CHANGED = True\n')
            changed = read_source_excerpt(str(path), 1, 80, 12000)
            self.assertNotIn(hashlib.sha256(raw).hexdigest(), changed)
            self.assertIn('1: CHANGED = True', changed)

    def test_invalid_missing_binary_and_long_line(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'file')
            self.assertIn('Invalid', read_source_excerpt(str(path), 0, 80, 12000))
            self.assertIn('FileNotFoundError', read_source_excerpt(str(path), 1, 80, 12000))
            path.write_bytes(b'a\x00b')
            self.assertIn('binary', read_source_excerpt(str(path), 1, 80, 12000))
            path.write_text('a' * 400 + '\n')
            self.assertIn('exceeds max_chars', read_source_excerpt(str(path), 1, 80, 256))
            self.assertIn('No lines', read_source_excerpt(str(path), 2, 80, 256))

    def test_known_file_routing(self):
        for question in ['What are settings in proxy/config.py?', 'Read `requirements.txt`',
                         'Explain module constants in _jobs.py', 'Read /tmp/project/config.py']:
            output = render_tool_catalog(intent=question)
            self.assertIn('`describe_file`', output)
            self.assertNotIn('`search_codebase`', output)
        self.assertNotIn('`describe_file`', render_tool_catalog(intent='https://example.org/docs.md'))


if __name__ == '__main__':
    unittest.main()

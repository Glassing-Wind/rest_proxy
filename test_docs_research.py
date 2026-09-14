"""Offline documentation discovery tests; no credentials or network required."""
import asyncio
import threading
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tools.brain.docs import research


class Registry:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class ResearchTests(unittest.TestCase):
    def test_normalizes_deduplicates_and_runs_off_event_loop(self):
        main_thread = threading.get_ident()
        client = MagicMock()
        def search(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), main_thread)
            return [{'href': 'https://example.org/docs', 'title': 'Docs', 'body': 'Guide'},
                    {'href': 'https://example.org/docs'}, {'href': 'javascript:alert(1)'}]
        client.text.side_effect = search
        factory = MagicMock()
        factory.return_value.__enter__.return_value = client
        with patch.dict('sys.modules', {'ddgs': types.SimpleNamespace(DDGS=factory)}):
            hits = asyncio.run(research._web_search('documentation', 10))
        self.assertEqual(hits, [{'url': 'https://example.org/docs', 'title': 'Docs', 'content': 'Guide'}])
        factory.assert_called_once_with(timeout=10)

    def test_empty_and_unavailable_search_do_not_start_indexing(self):
        registry = Registry()
        research.register(registry)
        for name in ('research_documentation', 'research_and_index'):
            for backend in (AsyncMock(return_value=[]), AsyncMock(side_effect=ImportError('missing'))):
                with self.subTest(tool=name), patch.object(research, '_web_search', backend), \
                     patch.object(research.subprocess, 'Popen') as start:
                    output = asyncio.run(registry.tools[name]('example', 'docs'))
                    self.assertIn('download_documentation()', output)
                    self.assertNotIn('API_KEY', output)
                    start.assert_not_called()

    def test_research_fallback_and_output(self):
        registry = Registry()
        research.register(registry)
        hits = [{'url': 'https://example.org/docs', 'title': 'Example docs', 'content': 'Guide'}]
        with patch.object(research, '_web_search', AsyncMock(side_effect=[[], hits])) as search, \
             patch('httpx.AsyncClient') as http:
            http.return_value.__aenter__.return_value.get = AsyncMock(side_effect=OSError())
            output = asyncio.run(registry.tools['research_documentation']('example', 'setup'))
        self.assertEqual(search.await_count, 2)
        self.assertIn('https://example.org/docs', output)
        self.assertNotIn('[0.00]', output)


if __name__ == '__main__':
    unittest.main()

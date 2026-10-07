"""Offline conservative binding tests; no native engines or external services."""
import hashlib
import json
import unittest

from graphrag_core.indexing.embedded_relationships import build_relationships


def file(path, source, symbols=()):
    return {'path': path, 'id': path, 'language': 'python', 'content': source,
            'sha256': hashlib.sha256(source.encode()).hexdigest(), 'symbols': list(symbols),
            'facts_json': json.dumps({'imports': [], 'calls': [], 'native': {}})}


class SymbolImports(unittest.TestCase):
    def bindings(self, source, target='def helper():\n    return 1\n', extras=()):
        definition = {'id': 'helper-id', 'kind': 'Function', 'name': 'helper', 'start': 1}
        files = [file('main.py', source), file('util.py', target, [definition]), *extras]
        return [json.loads(link['payload_json']) for link in build_relationships(files)
                if link['kind'] == 'symbol_imports']

    def test_alias_and_relative_function_bindings(self):
        links = self.bindings('from util import helper as h\n')
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['callee_id'], 'helper-id')
        self.assertEqual(links[0]['local_name'], 'h')
        self.assertEqual(links[0]['imported_name'], 'helper')
        self.assertEqual(links[0]['line'], 1)
        self.assertEqual(links[0]['expression'], 'from util import helper as h')
        long = self.bindings('from util import (\n#' + 'x' * 2100 + '\nhelper as h\n)\n')[0]
        self.assertEqual(len(long['expression']), 2000)
        self.assertTrue(long['expression_truncated'])
        definition = {'id': 'helper-id', 'kind': 'Function', 'name': 'helper', 'start': 1}
        links = build_relationships([
            file('pkg/main.py', 'from .util import helper\n'),
            file('pkg/util.py', 'def helper():\n return 1\n', [definition])])
        self.assertEqual(sum(link['kind'] == 'symbol_imports' for link in links), 1)

    def test_unsafe_or_unsupported_bindings_remain_unresolved(self):
        sources = ['from util import helper\nhelper = 2\n',
                   'from util import helper\nfrom other import *\n',
                   'from util import helper\nexec("helper=2")\n',
                   'from util import helper\nhelper.attr=2\n',
                   'from util import helper\nmatch value:\n case helper:\n  pass\n',
                   'from util import helper\nmatch value:\n case [*helper]:\n  pass\n',
                   'from util import helper\nmatch value:\n case {**helper}:\n  pass\n',
                   'import util\n', 'from util import missing\n',
                   'def nested():\n from util import helper\n',
                   'from os import helper\n']
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(self.bindings(source), [])
        for target in ['@decorator\ndef helper():\n return 1\n',
                       'from other import helper\n', 'class helper:\n pass\n',
                       'def helper():\n return 1\nhelper=2\n']:
            with self.subTest(target=target):
                self.assertEqual(self.bindings('from util import helper\n', target), [])
        self.assertEqual(self.bindings('from util import helper\n', extras=[
            file('writer.py', 'import util\nutil.helper=2\n')]), [])
        self.assertEqual(self.bindings('from util import helper\n', extras=[
            file('util/__init__.py', 'def helper():\n return 1\n')]), [])


if __name__ == '__main__':
    unittest.main()

"""Native parser fact-contract tests; no storage or embedding service required."""
import unittest

import tree_sitter_language_pack as ts_pack
from graphrag_core.indexing.embedded_facts import build_file_facts


class FactsTests(unittest.TestCase):
    def test_python_call_bytes_unicode_nested_owner_and_import(self):
        source = '# café\nfrom util import helper as alias\ndef caller():\n    def nested():\n        return alias()\n    return nested()\nalias()\n'
        result = ts_pack.process(source, ts_pack.ProcessConfig('python'))
        # Owner ranges are the same native byte coordinates used for stable symbol IDs.
        outer = result['structure'][0]
        inner = outer['children'][0]
        symbols = [dict(id=name, kind='Function', name=name, **item['span'])
                   for name, item in [('caller', outer), ('nested', inner)]]
        facts = build_file_facts(ts_pack, source, 'python', 'a.py', result, symbols)
        self.assertEqual(facts['imports'][0]['source'], 'util')
        self.assertEqual([(call['target'], call['owner_name']) for call in facts['calls']],
                         [('alias', 'nested'), ('nested', 'caller'), ('alias', None)])
        for call in facts['calls']:
            self.assertIn(call['target'], source.encode()[call['start_byte']:call['end_byte']].decode())
        self.assertEqual(facts['calls'][0]['start_line'], 5)

    def test_http_methods_routes_and_imports_use_native_file_path(self):
        source = ('import {helper} from "./util";\n'
                  'export function GET(){ return helper(); }\n'
                  'fetch("/api/leases", {headers: {Authorization: token}});\n'
                  'fetch("/api/units", {method:"POST"});\n')
        for language in ('javascript', 'typescript', 'tsx'):
            with self.subTest(language=language):
                ts_pack.get_parser(language)
                result = ts_pack.process(source, ts_pack.ProcessConfig(language))
                facts = build_file_facts(ts_pack, source, language, 'app/api/items/route.ts', result, [])
                self.assertEqual(facts['imports'][0]['source'], './util')
                self.assertEqual({call['path']: call['method'] for call in facts['native']['http_calls']},
                                 {'/api/leases': 'GET', '/api/units': 'POST'})
                self.assertTrue(facts['native'].get('route_defs'))
                self.assertEqual([call['target'] for call in facts['calls']], ['helper', 'fetch', 'fetch'])

    def test_call_count_refuses_silent_truncation(self):
        source = 'helper();\n' * 10001
        with self.assertRaises(ValueError):
            build_file_facts(ts_pack, source, 'python', 'a.py', {}, [])


if __name__ == '__main__':
    unittest.main()

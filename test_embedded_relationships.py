"""Native parser static-resolution fixtures; no graph/model services required."""
from pathlib import Path
import tempfile
import unittest

from graphrag_core.indexing.embedded_outlines import build_outline_snapshot


def snapshot(sources):
    with tempfile.TemporaryDirectory() as directory:
        for name, content in sources.items():
            path = Path(directory) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return build_outline_snapshot(directory, 'fixture', list(sources))


class RelationshipTests(unittest.TestCase):
    def test_alias_relative_import_and_unique_calls(self):
        result = snapshot({'pkg/__init__.py': '', 'pkg/util.py': 'def helper():\n    return 1\n',
                           'pkg/main.py': 'from .util import helper as h\nimport pkg.util as u\ndef caller():\n    return h() + u.helper()\n'})
        import json
        links = [json.loads(link['payload_json']) for link in result['relationships']]
        calls = [link for link in links if link['kind'] == 'calls']
        self.assertEqual({call['expression'] for call in calls}, {'h', 'u.helper'})
        self.assertTrue(all(call['target_file'] == 'pkg/util.py' for call in calls))
        self.assertTrue(all(call['caller_id'] and call['callee_id'] for call in calls))
        self.assertEqual(len([link for link in links if link['kind'] == 'imports']), 2)

    def test_shadowed_rebound_dynamic_and_ambiguous_targets_stay_unresolved(self):
        result = snapshot({'util.py': 'def helper():\n    return 1\n', 'util/__init__.py': 'def helper():\n    return 2\n',
                           'main.py': 'from util import helper\ndef helper():\n    return 3\ndef caller(helper):\n    helper()\n    getattr(obj, "run")()\n'})
        self.assertEqual(result['relationships'], [])
        result = snapshot({'main.py': 'def helper():\n    return 1\ndef caller():\n    helper = replacement\n    return helper()\n'})
        self.assertEqual(result['relationships'], [])

    def test_known_module_mutation_and_wildcard_do_not_resolve_calls(self):
        import json
        result = snapshot({'util.py': 'def helper():\n    return 1\n',
                           'caller.py': 'from util import helper\ndef caller():\n    return helper()\n',
                           'mutator.py': 'import util as u\nu.helper = replacement\n'})
        self.assertFalse(any(json.loads(link['payload_json'])['kind'] == 'calls'
                             for link in result['relationships']))
        result = snapshot({'main.py': 'def helper():\n    return 1\nif flag:\n    from util import *\ndef caller():\n    return helper()\n'})
        self.assertEqual(result['relationships'], [])

    def test_local_recursion_and_forward_module_execution(self):
        result = snapshot({'main.py': 'helper()\ndef helper():\n    return helper()\nhelper()\n'})
        self.assertEqual(len(result['relationships']), 2)

    def test_unsupported_ast_does_not_hide_competing_module_candidates(self):
        import ast
        from unittest import mock
        original = ast.parse
        unsupported = 'def helper():\n    return 1\n'
        def parse(source, *args, **kwargs):
            if source == unsupported:
                raise SyntaxError('Unsupported interpreter grammar')
            return original(source, *args, **kwargs)
        with mock.patch('graphrag_core.indexing.embedded_relationships.ast.parse', side_effect=parse):
            result = snapshot({'util.py': unsupported, 'util/__init__.py': 'def helper():\n    return 2\n',
                               'main.py': 'from util import helper\ndef caller():\n    return helper()\n'})
        self.assertEqual(result['relationships'], [])

    def test_generic_type_parameters_shadow_module_functions(self):
        result = snapshot({'main.py': 'def T():\n    return 1\ndef caller[T]():\n    return T()\n'})
        self.assertEqual(result['relationships'], [])

    def test_js_import_ambiguity_and_exact_route_methods(self):
        import json
        result = snapshot({'client.ts': 'import {helper} from "./util";\nfetch("/api/items");\nfetch("/api/items", {method:"POST"});\n',
                           'util.ts': 'export function helper(){ return 1; }\n',
                           'app/api/items/route.ts': 'export function GET(){ return 1; }\n'})
        links = [json.loads(link['payload_json']) for link in result['relationships']]
        self.assertEqual(len([link for link in links if link['kind'] == 'imports']), 1)
        routes = [link for link in links if link['kind'] == 'http_routes']
        self.assertEqual([link['expression'] for link in routes], ['GET /api/items'])
        ambiguous = snapshot({'client.ts': 'import {helper} from "./util";\n', 'util.ts': 'export const helper=1;\n',
                              'util.js': 'export const helper=2;\n'})
        self.assertEqual(ambiguous['relationships'], [])


if __name__ == '__main__':
    unittest.main()

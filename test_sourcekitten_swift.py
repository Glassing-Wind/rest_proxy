import importlib.util
import sys
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/graphrag_core/indexing/sourcekitten_swift.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sourcekitten_swift_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SourceKittenSwiftTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_base_name_strips_signature(self):
        self.assertEqual(self.module._base_name("moveSelectedReferenceUp()"), "moveSelectedReferenceUp")
        self.assertEqual(self.module._base_name("SidebarView"), "SidebarView")

    def test_match_symbol_record_prefers_line_overlap(self):
        record = {
            "filepath": "Views/Foo.swift",
            "name": "body",
            "base_name": "body",
            "kind": "source.lang.swift.decl.var.instance",
            "start_line": 10,
            "end_line": 12,
            "usr": None,
            "doc_comment": None,
            "inherited_types": [],
        }
        match = self.module._match_symbol_record(
            record,
            [
                {"sid": "a", "name": "body", "start_line": 3, "end_line": 4},
                {"sid": "b", "name": "body", "start_line": 10, "end_line": 12},
            ],
        )
        self.assertEqual(match["sid"], "b")

    def test_enrich_swift_graph_fail_opens_when_parser_extractor_returns_empty(self):
        fake_ts_pack = mock.Mock()
        fake_ts_pack.extract_swift_semantic_facts.return_value = {}
        with mock.patch.object(self.module, "_load_ts_pack", return_value=fake_ts_pack):
            result = self.module.enrich_swift_graph(
                project_path="/tmp/project",
                project_id="pid",
                indexed_files=[],
                neo4j_uri="bolt://localhost:7687",
                neo4j_user="neo4j",
                neo4j_pass="password",
            )
        self.assertEqual(result["enabled"], self.module._enabled())
        if self.module._enabled():
            self.assertFalse(result["available"])


if __name__ == "__main__":
    unittest.main()

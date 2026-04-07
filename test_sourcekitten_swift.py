import importlib.util
import sys
import unittest


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

    def test_extract_symbol_records_from_structure_data(self):
        raw = b"struct Foo: View {\n    /// Body docs\n    var body: some View { Text(\"x\") }\n}\n"
        structure = {
            "key.substructure": [
                {
                    "key.kind": "source.lang.swift.decl.struct",
                    "key.name": "Foo",
                    "key.offset": 0,
                    "key.length": len(raw),
                    "key.inheritedtypes": [{"key.name": "SwiftUI.View"}],
                    "key.substructure": [
                        {
                            "key.kind": "source.lang.swift.decl.var.instance",
                            "key.name": "body",
                            "key.offset": raw.index(b"var body"),
                            "key.length": len(b"var body: some View { Text(\"x\") }"),
                            "key.doc.comment": "Body docs",
                        }
                    ],
                }
            ]
        }
        records = self.module._extract_symbol_records_from_structure_data(
            structure, "Views/Foo.swift", raw
        )
        self.assertEqual([r.name for r in records], ["Foo", "body"])
        self.assertEqual(records[0].inherited_types, ["View"])
        self.assertEqual(records[1].doc_comment, "Body docs")

    def test_match_symbol_record_prefers_line_overlap(self):
        record = self.module.SwiftSymbolRecord(
            filepath="Views/Foo.swift",
            name="body",
            base_name="body",
            kind="source.lang.swift.decl.var.instance",
            start_line=10,
            end_line=12,
            usr=None,
            doc_comment=None,
            inherited_types=[],
        )
        match = self.module._match_symbol_record(
            record,
            [
                {"sid": "a", "name": "body", "start_line": 3, "end_line": 4},
                {"sid": "b", "name": "body", "start_line": 10, "end_line": 12},
            ],
        )
        self.assertEqual(match["sid"], "b")

    def test_clean_inherited_type_name(self):
        self.assertEqual(self.module._clean_inherited_type_name("SwiftUI.View"), "View")
        self.assertEqual(self.module._clean_inherited_type_name("Foo<Bar>"), "Foo")
        self.assertEqual(self.module._clean_inherited_type_name("ProtocolA & ProtocolB"), "ProtocolA")


if __name__ == "__main__":
    unittest.main()

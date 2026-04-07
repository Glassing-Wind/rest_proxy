import importlib.util
import sys
import types
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

    def test_enrich_swift_graph_delegates_to_ts_pack_binding(self):
        fake_ts_pack = types.SimpleNamespace(
            enrich_swift_graph=mock.Mock(return_value={"enabled": True, "files": 2, "symbols": 7})
        )
        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            result = self.module.enrich_swift_graph(
                project_path="/tmp/project",
                project_id="pid",
                indexed_files=["/tmp/project/Foo.swift"],
                neo4j_uri="bolt://localhost:7687",
                neo4j_user="neo4j",
                neo4j_pass="password",
                neo4j_db="proxy",
            )
        self.assertEqual(result["symbols"], 7)
        fake_ts_pack.enrich_swift_graph.assert_called_once()


if __name__ == "__main__":
    unittest.main()

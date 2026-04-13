import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


FILE_DESCRIBE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/code_intel/file_describe.py"
REFERENCES_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/code_intel/references.py"
SYMBOL_GRAPH_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/code_intel/symbol_graph.py"


def load_file_describe_module():
    spec = importlib.util.spec_from_file_location("file_describe_under_test", FILE_DESCRIBE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (None, None, None, None, None)
    ts_diag = types.ModuleType("ts_diagnostics")
    ts_diag.normalize_ts_pack_result = lambda code, lang, result: result

    with mock.patch.dict(sys.modules, {"_helpers": helpers_mod, "ts_diagnostics": ts_diag}):
        spec.loader.exec_module(module)
    return module


def load_references_module():
    spec = importlib.util.spec_from_file_location("references_under_test", REFERENCES_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: workspace_id.rstrip("/").split("/")[-1]
    helpers_mod.get_memory_modules = lambda: (None, None, None, None, None)
    neo4j_mod = types.ModuleType("neo4j")

    def unit_of_work(timeout=None, metadata=None):
        def decorator(fn):
            return fn
        return decorator

    neo4j_mod.unit_of_work = unit_of_work

    with mock.patch.dict(sys.modules, {"_helpers": helpers_mod, "neo4j": neo4j_mod}):
        spec.loader.exec_module(module)
    return module


def load_symbol_graph_module():
    spec = importlib.util.spec_from_file_location("symbol_graph_under_test", SYMBOL_GRAPH_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CodeIntelHelperTests(unittest.TestCase):
    def test_resolve_describe_paths(self):
        module = load_file_describe_module()
        abs_path, display = module.resolve_describe_paths("", "/tmp/file.swift")
        self.assertEqual(abs_path, "/tmp/file.swift")
        self.assertEqual(display, "/tmp/file.swift")

    def test_format_ts_pack_symbols(self):
        module = load_file_describe_module()
        symbols, label = module.format_ts_pack_symbols(
            {
                "_language": "swift",
                "metrics": {"error_count": 1},
                "structure": [
                    {
                        "name": "AppView",
                        "kind": "struct",
                        "span": {"start_line": 0, "end_line": 9},
                        "children": [],
                    }
                ],
            }
        )
        self.assertIn("[swift]", label)
        self.assertTrue(symbols)

    def test_find_references_formats_graph_and_semantic_sections(self):
        module = load_references_module()

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            async def data(self):
                return self.rows

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute_read(self, fn):
                return await fn(self)

            async def run(self, cypher, **params):
                if "CALLS_EXTERNAL_SYMBOL" in cypher:
                    return FakeResult(
                        [
                            {
                                "fp": "src/ext.py",
                                "sl": 30,
                                "cn": "use_external",
                                "qn": "json.Unmarshal",
                                "language": "go",
                                "tpid": "repo",
                            }
                        ]
                    )
                return FakeResult([{"fp": "src/a.py", "sl": 12, "cn": "caller", "tpid": "repo"}])

        class FakeDriver:
            def session(self, database=None):
                return FakeSession()

        class FakeCursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, query, params):
                return None

            def __aiter__(self):
                async def gen():
                    yield ("src/b.py", "21", "repo", "symbol_name()")
                return gen()

        class FakeConnection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return FakeCursor()

        class FakePool:
            def connection(self):
                return FakeConnection()

        class FakeMemoryStore:
            _pg_pool = FakePool()

            @staticmethod
            async def open_pool():
                return None

        graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        graph_bootstrap_mod.require_driver = _require_driver
        graph_bootstrap_mod._NEO4J_DB = "neo4j"

        with mock.patch.object(module, "get_memory_modules", return_value=(FakeMemoryStore, None, None, None, None)):
            with mock.patch.dict(sys.modules, {"graph_bootstrap": graph_bootstrap_mod}):
                output = asyncio.run(module.find_references_impl(["/tmp/repo"], "symbol_name"))

        self.assertIn("Functional References (Graph)", output)
        self.assertIn("External Symbol Callers (Graph)", output)
        self.assertIn("Mentions & Type Usages (Semantic)", output)

    def test_format_symbol_context_includes_external_calls(self):
        module = load_symbol_graph_module()
        output = module.format_symbol_context(
            {
                "kind": "Function",
                "filepath": "src/lib.rs",
                "start_line": 10,
                "end_line": 30,
                "signature": "fn do_work()",
                "callers": [],
                "callees": [{"name": "helper", "file": "src/helper.rs"}],
                "external_callees": [
                    {
                        "name": "Unmarshal",
                        "qualified_name": "json.Unmarshal",
                        "language": "go",
                    }
                ],
            },
            "do_work",
        )

        rendered = "\n".join(output)
        self.assertIn("**External Calls** (1):", rendered)
        self.assertIn("`json.Unmarshal` [go]", rendered)


if __name__ == "__main__":
    unittest.main()

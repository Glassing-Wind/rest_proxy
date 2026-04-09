import importlib
import os
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock


SEARCH_MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/docs/search.py"


def load_search_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("docs_search_under_test", SEARCH_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    mcp_mod = types.ModuleType("mcp.server.fastmcp")

    class FakeMCP:
        def tool(self):
            def decorator(fn):
                return fn

            return decorator

    mcp_mod.FastMCP = FakeMCP
    with mock.patch.dict(sys.modules, {"mcp.server.fastmcp": mcp_mod}):
        spec.loader.exec_module(module)
    return module


class RuntimeResolutionTests(unittest.TestCase):
    def test_resolve_python_runtime_prefers_explicit_env_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_python = os.path.join(tmpdir, "python")
            with open(fake_python, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(fake_python, stat.S_IRWXU)

            with mock.patch.dict(
                os.environ,
                {"LM_PROXY_INDEX_PYTHON": fake_python},
                clear=False,
            ):
                runtime_mod = importlib.import_module("_runtime")
                runtime_mod.resolve_python_runtime.cache_clear()
                info = runtime_mod.resolve_python_runtime()

            self.assertEqual(info["cmd"], [fake_python])
            self.assertEqual(info["python"], fake_python)
            self.assertEqual(info["source"], "env")

    def test_resolve_python_runtime_prefers_configured_conda_env(self):
        runtime_mod = importlib.import_module("_runtime")
        with mock.patch.dict(
            os.environ,
            {"LM_PROXY_CONDA_ENV": "lmproxy", "LM_PROXY_INDEX_PYTHON": "", "LM_PROXY_PYTHON": ""},
            clear=False,
        ):
            with mock.patch.object(runtime_mod, "_conda_env_python", return_value="/tmp/lmproxy/bin/python"):
                runtime_mod.resolve_python_runtime.cache_clear()
                info = runtime_mod.resolve_python_runtime()

        self.assertEqual(info["cmd"], ["/tmp/lmproxy/bin/python"])
        self.assertEqual(info["python"], "/tmp/lmproxy/bin/python")
        self.assertEqual(info["source"], "conda_env_path")
        self.assertEqual(info["conda_env"], "lmproxy")


class DocsSearchHelperTests(unittest.TestCase):
    def setUp(self):
        self.module = load_search_module()

    def test_expand_query_adds_neo4j_operational_synonyms(self):
        expanded = self.module._expand_query("neo4j", "deadlock retry guidance")
        self.assertIn("DeadlockDetected", expanded)
        self.assertIn("lock contention", expanded)
        self.assertIn("How to diagnose locking issues", expanded)

    def test_extract_exact_terms_recognizes_deadlock_code(self):
        exact_terms = self.module._extract_exact_terms("neo4j", "How do I handle DeadlockDetected?")
        self.assertIn("DeadlockDetected", exact_terms)
        self.assertIn("Neo.TransientError.Transaction.DeadlockDetected", exact_terms)

    def test_is_operational_query_ignores_generic_transaction_language(self):
        self.assertFalse(
            self.module._is_operational_query(
                "neo4j",
                "python driver transaction functions retryable transaction",
            )
        )
        self.assertTrue(
            self.module._is_operational_query(
                "neo4j",
                "deadlock retry guidance",
            )
        )

    def test_doc_type_from_result_prefers_kb_and_operations(self):
        self.assertEqual(
            self.module._doc_type_from_result(
                "https://neo4j.com/developer/kb/diagnose-locking-issues/",
                "How to diagnose locking issues - Knowledge Base",
                {},
            ),
            "knowledge-base",
        )
        self.assertEqual(
            self.module._doc_type_from_result(
                "https://neo4j.com/docs/operations-manual/current/database-internals/",
                "Database internals",
                {},
            ),
            "operations-manual",
        )

    def test_topic_filter_sql_expands_curated_family_topics(self):
        sql, params = self.module._topic_filter_sql("neo4j")
        self.assertIn("source = %(topic_0)s", sql)
        self.assertIn("source ILIKE %(topic_1)s", sql)
        self.assertEqual(params["topic_0"], "neo4j")
        self.assertEqual(params["topic_1"], "neo4j-%")

    def test_topic_filter_sql_uses_exact_match_for_non_family_topic(self):
        sql, params = self.module._topic_filter_sql("pgvector")
        self.assertEqual(sql, "AND source = %(topic)s")
        self.assertEqual(params["topic"], "pgvector")


if __name__ == "__main__":
    unittest.main()

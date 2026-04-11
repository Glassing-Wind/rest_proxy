import asyncio
import importlib
import os
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock


SEARCH_MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/docs/search.py"
POLICY_MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/docs/policy.py"
CONFIG_MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/docs/config.py"


def load_config_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("tools.brain.docs.config", CONFIG_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_search_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("docs_search_under_test", SEARCH_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    docs_pkg = types.ModuleType("tools.brain.docs")
    docs_pkg.__path__ = []
    search_pkg = types.ModuleType("tools.brain.search")
    search_pkg.__path__ = []
    config_mod = load_config_module()
    policy_mod = load_policy_module()

    class FakeMCP:
        def tool(self):
            def decorator(fn):
                return fn

            return decorator

    mcp_mod.FastMCP = FakeMCP
    with mock.patch.dict(
        sys.modules,
        {
            "mcp.server.fastmcp": mcp_mod,
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.docs": docs_pkg,
            "tools.brain.search": search_pkg,
            "tools.brain.docs.config": config_mod,
            "tools.brain.docs.policy": policy_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


def load_policy_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("tools.brain.docs.policy", POLICY_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    docs_pkg = types.ModuleType("tools.brain.docs")
    docs_pkg.__path__ = []
    config_mod = load_config_module()
    with mock.patch.dict(
        sys.modules,
        {
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.docs": docs_pkg,
            "tools.brain.docs.config": config_mod,
        },
    ):
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
        self.search_module = load_search_module()
        self.module = load_policy_module()

    def test_expand_query_adds_neo4j_operational_synonyms(self):
        expanded = self.module.expand_query("neo4j", "deadlock retry guidance")
        self.assertIn("DeadlockDetected", expanded)
        self.assertIn("lock contention", expanded)
        self.assertIn("How to diagnose locking issues", expanded)

    def test_extract_exact_terms_recognizes_deadlock_code(self):
        exact_terms = self.module.extract_exact_terms("neo4j", "How do I handle DeadlockDetected?")
        self.assertIn("DeadlockDetected", exact_terms)
        self.assertIn("Neo.TransientError.Transaction.DeadlockDetected", exact_terms)

    def test_is_operational_query_ignores_generic_transaction_language(self):
        self.assertFalse(
            self.module.is_operational_query(
                "neo4j",
                "python driver transaction functions retryable transaction",
            )
        )
        self.assertTrue(
            self.module.is_operational_query(
                "neo4j",
                "deadlock retry guidance",
            )
        )

    def test_doc_type_from_result_prefers_kb_and_operations(self):
        self.assertEqual(
            self.module.doc_type_from_result(
                "https://neo4j.com/developer/kb/diagnose-locking-issues/",
                "How to diagnose locking issues - Knowledge Base",
                {},
            ),
            "knowledge-base",
        )
        self.assertEqual(
            self.module.doc_type_from_result(
                "https://neo4j.com/docs/operations-manual/current/database-internals/",
                "Database internals",
                {},
            ),
            "operations-manual",
        )

    def test_topic_filter_sql_expands_curated_family_topics(self):
        sql, params = self.module.topic_filter_sql("neo4j")
        self.assertIn("source = %(topic_0)s", sql)
        self.assertIn("source ILIKE %(topic_1)s", sql)
        self.assertEqual(params["topic_0"], "neo4j")
        self.assertEqual(params["topic_1"], "neo4j-%")

    def test_topic_filter_sql_uses_exact_match_for_non_family_topic(self):
        sql, params = self.module.topic_filter_sql("pgvector")
        self.assertEqual(sql, "AND source = %(topic)s")
        self.assertEqual(params["topic"], "pgvector")

    def test_apply_diverse_docs_selection_prefers_shared_rerank_contract(self):
        rows = [
            {"source_url": "https://neo4j.com/docs/python-manual/current/transactions/", "content": "canonical", "rrf": 1.0},
            {"source_url": "https://mirror.example/transactions/", "content": "mirror", "rrf": 0.99},
            {"source_url": "https://neo4j.com/docs/operations-manual/current/database-internals/concurrent-data-access/", "content": "ops", "rrf": 0.8},
        ]
        helper_mod = types.ModuleType("tools.brain.search.semantic_helpers")
        helper_mod.duplicate_experiment_flags_from_env = (
            lambda mode="code": {"canonical_docs_mirror_suppression": mode == "docs"}
        )
        helper_mod.rerank_retrieval_results_contract = lambda results, query, mode, experiments, include_debug: {
            "results": [dict(rows[0]), dict(rows[2])],
            "selection": {"keep_indices": [0, 2]},
            "telemetry": {"experimental_suppressions": 1},
        }
        with mock.patch.dict(sys.modules, {"tools.brain.search.semantic_helpers": helper_mod}):
            selected, trace = self.search_module._apply_diverse_docs_selection(rows, query="neo4j transactions", k=2)

        self.assertEqual([row["source_url"] for row in selected], [rows[0]["source_url"], rows[2]["source_url"]])
        self.assertEqual(trace["telemetry"]["experimental_suppressions"], 1)

    def test_apply_diverse_docs_selection_falls_back_to_url_dedupe(self):
        rows = [
            {"source_url": "https://neo4j.com/docs/python-manual/current/transactions/", "content": "canonical", "rrf": 1.0},
            {"source_url": "https://neo4j.com/docs/python-manual/current/transactions/", "content": "same-url", "rrf": 0.95},
            {"source_url": "https://neo4j.com/developer/kb/diagnose-locking-issues/", "content": "kb", "rrf": 0.8},
        ]
        with mock.patch.dict(sys.modules, {}, clear=False):
            sys.modules.pop("tools.brain.search.semantic_helpers", None)
            selected, trace = self.search_module._apply_diverse_docs_selection(rows, query="neo4j transactions", k=2)

        self.assertEqual(
            [row["source_url"] for row in selected],
            [rows[0]["source_url"], rows[2]["source_url"]],
        )
        self.assertIsNone(trace)

    def test_search_documentation_reranks_docs_by_default(self):
        class FakeCursor:
            def __init__(self, rows):
                self.rows = rows

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, sql, params=None):
                self.sql = sql
                self.params = params or {}

            def __aiter__(self):
                self._iter = iter(self.rows)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        class FakeConn:
            def __init__(self, rows):
                self.rows = rows

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, sql):
                self.sql = sql

            def cursor(self):
                return FakeCursor(self.rows)

        class FakePool:
            def __init__(self, rows):
                self.rows = rows

            def connection(self):
                return FakeConn(self.rows)

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self):
                def decorator(fn):
                    self.tools[fn.__name__] = fn
                    return fn

                return decorator

        rows = [
            (
                "https://mirror.example.com/docs/python-manual/5.26/transactions/",
                "Transactions guide mirror",
                0,
                "Mirror transactions guide",
                0.91,
                ["Transactions"],
                {"domain": "mirror.example.com", "source_type": "mirror"},
            ),
            (
                "https://neo4j.com/docs/python-manual/5.26/transactions/",
                "Transactions guide canonical",
                0,
                "Canonical transactions guide",
                0.89,
                ["Transactions"],
                {"domain": "neo4j.com", "source_type": "driver-manual"},
            ),
            (
                "https://neo4j.com/docs/python-manual/4.4/transactions/",
                "Transactions guide 4.4",
                0,
                "Version 4.4 transactions guide",
                0.85,
                ["Transactions"],
                {"domain": "neo4j.com", "source_type": "driver-manual"},
            ),
        ]

        memory_mod = types.ModuleType("memory.store")
        memory_mod._pg_pool = FakePool(rows)
        memory_mod.open_pool = mock.AsyncMock()
        memory_mod._pg_pool_available = lambda: True

        embed_mod = types.ModuleType("embedding_service")

        class FakeEmbeddingService:
            async def embed_batch_async(self, texts):
                return [[0.1, 0.2, 0.3] for _ in texts]

        embed_mod.get_embedding_service = lambda: FakeEmbeddingService()

        helper_mod = types.ModuleType("tools.brain.search.semantic_helpers")
        helper_mod.duplicate_experiment_flags_from_env = (
            lambda mode="code": {"canonical_docs_mirror_suppression": mode == "docs"}
        )
        helper_mod.rerank_retrieval_results_contract = lambda results, query, mode, experiments, include_debug: {
            "results": [dict(results[1]), dict(results[2])],
            "selection": {"keep_indices": [1, 2], "suppressed_indices": [0]},
            "telemetry": {"experimental_suppressions": 1},
            "suppression_policy": "experimental_non_exact",
            "experiments": experiments,
        }
        helper_mod.append_duplicate_telemetry_event = lambda *args, **kwargs: None

        with mock.patch.dict(
            sys.modules,
            {
                "memory.store": memory_mod,
                "embedding_service": embed_mod,
                "tools.brain.search.semantic_helpers": helper_mod,
            },
        ):
            mcp = FakeMCP()
            self.search_module.register(mcp)
            output = asyncio.run(
                mcp.tools["search_documentation"](
                    "neo4j 5.26 transactions",
                    topic="neo4j",
                    k=2,
                )
            )

        self.assertIn("Transactions guide canonical", output)
        self.assertIn("Transactions guide 4.4", output)
        self.assertNotIn("Transactions guide mirror", output)


if __name__ == "__main__":
    unittest.main()

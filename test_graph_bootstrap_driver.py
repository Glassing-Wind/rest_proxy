import asyncio
import contextlib
import io
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "graph_bootstrap.py"


class _FakeResult:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def consume(self):
        return None

    async def single(self):
        return self.rows[0] if self.rows else None

    async def data(self):
        return list(self.rows)


class _FakeTx:
    def __init__(self, constraint_rows=None, index_rows=None):
        self.constraint_rows = constraint_rows or []
        self.index_rows = index_rows or []
        self.queries = []

    async def run(self, query, *args, **kwargs):
        self.queries.append(query)
        if query.startswith("SHOW CONSTRAINTS"):
            return _FakeResult(self.constraint_rows)
        if query.startswith("SHOW INDEXES"):
            return _FakeResult(self.index_rows)
        return _FakeResult()


class _FakeSession:
    def __init__(self, tx):
        self.tx = tx

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute_write(self, fn):
        return await fn(self.tx)

    async def execute_read(self, fn):
        return await fn(self.tx)


class _FakeDriver:
    def __init__(self, tx=None):
        self.session_calls = []
        self.closed = False
        self.tx = tx or _FakeTx()

    async def verify_connectivity(self):
        return None

    def session(self, **kwargs):
        self.session_calls.append(kwargs)
        return _FakeSession(self.tx)

    async def close(self):
        self.closed = True
        return None


def _load_module(*, fail_verify: bool = False, tx=None):
    spec = importlib.util.spec_from_file_location(
        "graph_bootstrap_under_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    driver_calls = []
    fake_driver = _FakeDriver(tx)
    if fail_verify:

        async def _fail_verify():
            raise RuntimeError("auth failed")

        fake_driver.verify_connectivity = _fail_verify

    neo4j_mod = types.ModuleType("neo4j")

    class _AsyncGraphDatabase:
        @staticmethod
        def driver(uri, auth=None, **kwargs):
            driver_calls.append({"uri": uri, "auth": auth, "kwargs": kwargs})
            return fake_driver

    neo4j_mod.AsyncGraphDatabase = _AsyncGraphDatabase
    neo4j_mod.unit_of_work = lambda *args, **kwargs: lambda fn: fn

    with mock.patch.dict(sys.modules, {"neo4j": neo4j_mod}):
        spec.loader.exec_module(module)

    module._driver = None
    return module, driver_calls, fake_driver


class GraphBootstrapDriverTests(unittest.TestCase):
    def test_init_graph_db_passes_explicit_driver_config(self):
        env = {
            "LM_PROXY_GRAPH_ENABLED": "1",
            "LM_PROXY_NEO4J_MAX_CONNECTION_POOL_SIZE": "77",
            "LM_PROXY_NEO4J_CONNECTION_ACQUISITION_TIMEOUT": "12.5",
            "LM_PROXY_NEO4J_MAX_TRANSACTION_RETRY_TIME": "45",
            "LM_PROXY_NEO4J_LIVENESS_CHECK_TIMEOUT": "9.5",
            "LM_PROXY_NEO4J_KEEP_ALIVE": "0",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            module, driver_calls, fake_driver = _load_module()

        asyncio.run(module.init_graph_db())

        self.assertEqual(len(driver_calls), 1)
        call = driver_calls[0]
        self.assertEqual(call["kwargs"]["max_connection_pool_size"], 77)
        self.assertEqual(call["kwargs"]["connection_acquisition_timeout"], 12.5)
        self.assertEqual(call["kwargs"]["max_transaction_retry_time"], 45.0)
        self.assertEqual(call["kwargs"]["liveness_check_timeout"], 9.5)
        self.assertFalse(call["kwargs"]["keep_alive"])
        self.assertEqual(fake_driver.session_calls[0]["database"], module._NEO4J_DB)

    def test_current_named_schema_skips_all_ddl(self):
        with mock.patch.dict(
            "os.environ", {"LM_PROXY_GRAPH_ENABLED": "1"}, clear=False
        ):
            module, driver_calls, fake_driver = _load_module()
        fake_driver.tx.constraint_rows = [
            {
                "name": name,
                "type": signature[0],
                "labelsOrTypes": list(signature[1]),
                "properties": list(signature[2]),
            }
            for name, signature in module._EXPECTED_CONSTRAINT_SIGNATURES.items()
        ]
        fake_driver.tx.index_rows = [
            {
                "name": name,
                "type": signature[0],
                "entityType": signature[1],
                "labelsOrTypes": list(signature[2]),
                "properties": list(signature[3]),
            }
            for name, signature in module._EXPECTED_INDEX_SIGNATURES.items()
        ]

        asyncio.run(module.init_graph_db())

        ddl = [
            query
            for query in fake_driver.tx.queries
            if query.lstrip().startswith("CREATE")
        ]
        self.assertEqual(ddl, [])

    def test_missing_named_schema_only_creates_missing_objects(self):
        with mock.patch.dict(
            "os.environ", {"LM_PROXY_GRAPH_ENABLED": "1"}, clear=False
        ):
            module, driver_calls, fake_driver = _load_module()
        fake_driver.tx.constraint_rows = [
            {
                "name": name,
                "type": signature[0],
                "labelsOrTypes": list(signature[1]),
                "properties": list(signature[2]),
            }
            for name, signature in module._EXPECTED_CONSTRAINT_SIGNATURES.items()
            if name != "chunk_id_unique"
        ]
        fake_driver.tx.index_rows = [
            {
                "name": name,
                "type": signature[0],
                "entityType": signature[1],
                "labelsOrTypes": list(signature[2]),
                "properties": list(signature[3]),
            }
            for name, signature in module._EXPECTED_INDEX_SIGNATURES.items()
            if name != "contains_idx"
        ]

        asyncio.run(module.init_graph_db())

        ddl = [
            query
            for query in fake_driver.tx.queries
            if query.lstrip().startswith("CREATE")
        ]
        self.assertEqual(len(ddl), 2)
        self.assertTrue(any("chunk_id_unique" in query for query in ddl))
        self.assertTrue(any("contains_idx" in query for query in ddl))

    def test_equivalent_legacy_constraint_name_satisfies_signature(self):
        with mock.patch.dict(
            "os.environ", {"LM_PROXY_GRAPH_ENABLED": "1"}, clear=False
        ):
            module, driver_calls, fake_driver = _load_module()
        fake_driver.tx.constraint_rows = [
            {
                "name": "node_id",
                "type": "NODE_PROPERTY_UNIQUENESS",
                "labelsOrTypes": ["Node"],
                "properties": ["id"],
            }
        ]
        expected = module._EXPECTED_CONSTRAINT_SIGNATURES["node_id_unique"]
        signatures = {
            module._constraint_signature(row) for row in fake_driver.tx.constraint_rows
        }
        self.assertIn(expected, signatures)

    def test_failed_verify_closes_driver_and_caches_error(self):
        with mock.patch.dict(
            "os.environ", {"LM_PROXY_GRAPH_ENABLED": "1"}, clear=False
        ):
            module, driver_calls, fake_driver = _load_module(fail_verify=True)

        with contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(module.init_graph_db())

        self.assertEqual(len(driver_calls), 1)
        self.assertTrue(fake_driver.closed)
        self.assertIsNone(module._driver)
        self.assertEqual(module._last_init_error, "auth failed")

    def test_require_driver_respects_failure_cooldown(self):
        with mock.patch.dict(
            "os.environ",
            {
                "LM_PROXY_GRAPH_ENABLED": "1",
                "LM_PROXY_NEO4J_INIT_FAILURE_COOLDOWN_SECONDS": "60",
            },
            clear=False,
        ):
            module, driver_calls, fake_driver = _load_module(fail_verify=True)

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "Last error: auth failed"):
                asyncio.run(module.require_driver())
        self.assertTrue(fake_driver.closed)
        self.assertEqual(len(driver_calls), 1)

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "Last error: auth failed"):
                asyncio.run(module.require_driver())
        self.assertEqual(
            len(driver_calls),
            1,
            "driver() should not be called again during failure cooldown",
        )

    def test_concurrent_init_graph_db_creates_single_driver(self):
        with mock.patch.dict(
            "os.environ", {"LM_PROXY_GRAPH_ENABLED": "1"}, clear=False
        ):
            module, driver_calls, fake_driver = _load_module()

        async def _slow_verify():
            await asyncio.sleep(0.01)
            return None

        fake_driver.verify_connectivity = _slow_verify

        async def _run():
            await asyncio.gather(*(module.init_graph_db() for _ in range(5)))

        asyncio.run(_run())

        self.assertEqual(len(driver_calls), 1)
        self.assertIs(module.get_driver(), fake_driver)


if __name__ == "__main__":
    unittest.main()

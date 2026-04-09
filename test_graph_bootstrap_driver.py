import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/graph_bootstrap.py"


class _FakeResult:
    async def consume(self):
        return None


class _FakeTx:
    async def run(self, *args, **kwargs):
        return _FakeResult()


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute_write(self, fn):
        return await fn(_FakeTx())


class _FakeDriver:
    def __init__(self):
        self.session_calls = []

    async def verify_connectivity(self):
        return None

    def session(self, **kwargs):
        self.session_calls.append(kwargs)
        return _FakeSession()

    async def close(self):
        return None


def _load_module():
    spec = importlib.util.spec_from_file_location("graph_bootstrap_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    driver_calls = []
    fake_driver = _FakeDriver()

    neo4j_mod = types.ModuleType("neo4j")

    class _AsyncGraphDatabase:
        @staticmethod
        def driver(uri, auth=None, **kwargs):
            driver_calls.append({"uri": uri, "auth": auth, "kwargs": kwargs})
            return fake_driver

    neo4j_mod.AsyncGraphDatabase = _AsyncGraphDatabase
    neo4j_mod.unit_of_work = lambda *args, **kwargs: (lambda fn: fn)

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


if __name__ == "__main__":
    unittest.main()

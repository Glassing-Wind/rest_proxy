import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "docs" / "admin.py"
CONFIG_MODULE_PATH = REPO_ROOT / "tools" / "brain" / "docs" / "config.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeCursor:
    def __init__(self):
        self._rows = []
        self._index = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params=None):
        self._index = 0
        query = " ".join(query.split())
        if "SELECT count(*) AS chunks, count(DISTINCT url) AS urls" in query:
            self._rows = [(3723, 1112)]
        elif "SELECT split_part(url, '/', 3) AS domain, count(*) AS chunks" in query:
            self._rows = [("neo4j.com", 3723)]
        elif "SELECT source, count(*) AS chunks, count(DISTINCT url) AS urls" in query:
            self._rows = [
                ("neo4j-apoc", 990, 564),
                ("neo4j-gds", 979, 183),
                ("neo4j", 826, 195),
                ("neo4j-cypher", 812, 149),
                ("neo4j-python", 84, 13),
            ]
        else:
            raise AssertionError(f"Unexpected query: {query}")

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._index]
        self._index += 1
        return row


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


def load_module():
    spec = importlib.util.spec_from_file_location("docs_admin_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP
    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    docs_pkg = types.ModuleType("tools.brain.docs")
    docs_pkg.__path__ = []

    config_spec = importlib.util.spec_from_file_location("tools.brain.docs.config", CONFIG_MODULE_PATH)
    config_mod = importlib.util.module_from_spec(config_spec)
    assert config_spec.loader is not None
    config_spec.loader.exec_module(config_mod)

    memory_pkg = types.ModuleType("memory")
    memory_store_mod = types.ModuleType("memory.store")

    async def _open_pool():
        return None

    memory_store_mod.open_pool = _open_pool
    memory_store_mod._pg_pool_available = lambda: True
    memory_store_mod._pg_pool = FakePool()

    stub_modules = {
        "mcp.server.fastmcp": mcp_mod,
        "tools": tools_pkg,
        "tools.brain": brain_pkg,
        "tools.brain.docs": docs_pkg,
        "tools.brain.docs.config": config_mod,
        "memory": memory_pkg,
        "memory.store": memory_store_mod,
    }
    with mock.patch.dict(sys.modules, stub_modules):
        spec.loader.exec_module(module)
    return module, stub_modules


class DocsAdminFamilyTests(unittest.TestCase):
    def test_list_documentation_sources_expands_family_topic(self):
        module, stub_modules = load_module()
        mcp = FakeMCP()
        module.register(mcp)

        with mock.patch.dict(sys.modules, stub_modules):
            output = asyncio.run(mcp.tools["list_documentation_sources"](topic="neo4j", limit=20))

        self.assertIn("Chunks: 3723", output)
        self.assertIn("Topic members:", output)
        self.assertIn("neo4j-python", output)
        self.assertIn("neo4j-apoc", output)
        self.assertIn("Domains:", output)
        self.assertIn("neo4j.com", output)


if __name__ == "__main__":
    unittest.main()

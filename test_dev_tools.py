import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/hands/dev.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        self.executed.append((query, params))

    async def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self.rows)


class FakePool:
    def __init__(self, rows):
        self.rows = rows

    def connection(self):
        return FakeConnection(self.rows)


class FakeMemoryStore:
    def __init__(self, rows):
        self._pg_pool = FakePool(rows)

    async def open_pool(self):
        return None


def load_module(memory_store):
    spec = importlib.util.spec_from_file_location("dev_tools_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (memory_store, None, None, None, None)
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.normalize_neo4j_path = lambda path: path.replace("\\", "/")
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/repo"

    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP

    neo4j_mod = types.ModuleType("neo4j")
    neo4j_mod.unit_of_work = lambda *args, **kwargs: (lambda fn: fn)

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "mcp.server.fastmcp": mcp_mod,
            "neo4j": neo4j_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


class DevToolsTests(unittest.TestCase):
    def test_get_test_coverage_for_uses_semantic_test_chunk_fallback(self):
        memory_store = FakeMemoryStore(
            [("tests/test_workspace_registry.py", 3), ("spec/workspace_registry_spec.py", 1)]
        )
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)

        def fake_subprocess_run(cmd, **kwargs):
            return types.SimpleNamespace(stdout="")

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run):
            output = asyncio.run(
                mcp.tools["get_test_coverage_for"]("/tmp/repo", "src/workspace_registry.py")
            )

        self.assertIn("tests/test_workspace_registry.py", output)
        self.assertIn("semantic test-chunk match (3)", output)


if __name__ == "__main__":
    unittest.main()

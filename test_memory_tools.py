import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "memory.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeMemoryStore:
    _ENABLE_PERSISTENCE = True

    def __init__(self):
        self.add_kwargs = None
        self.list_kwargs = None

    async def open_pool(self):
        return None

    async def add_durable_memory(self, workspace_id, text, **kwargs):
        self.add_kwargs = {"workspace_id": workspace_id, "text": text, **kwargs}
        return True

    async def list_durable_memories(self, workspace_id, **kwargs):
        self.list_kwargs = {"workspace_id": workspace_id, **kwargs}
        return [
            {
                "text": "Keep graph writes transactional.",
                "is_global": False,
                "created_at": None,
                "tags": ["neo4j", "reliability"],
                "category": "architecture",
                "importance": 5,
            }
        ]


def load_module(memory_store):
    spec = importlib.util.spec_from_file_location("memory_tools_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    store_durable = types.ModuleType("memory.store_durable")

    def normalize(tags=None, category=None, importance=3):
        normalized_tags = list(
            dict.fromkeys(str(tag).strip().lower() for tag in tags or [])
        )
        normalized_category = category.strip().lower() if category else None
        normalized_importance = int(importance)
        if not 1 <= normalized_importance <= 5:
            raise ValueError("memory importance must be between 1 and 5")
        return normalized_tags, normalized_category, normalized_importance

    store_durable.normalize_memory_metadata = normalize
    store_durable.durable_memory_backend_status = mock.AsyncMock(
        return_value=(True, None)
    )
    memory_pkg = types.ModuleType("memory")
    memory_pkg.__path__ = []
    memory_pkg.store_durable = store_durable
    helpers = types.ModuleType("_helpers")
    helpers.get_memory_modules = lambda: (memory_store, None, None, None, None)
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    fastmcp.FastMCP = FakeMCP

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers,
            "memory": memory_pkg,
            "memory.store_durable": store_durable,
            "mcp.server.fastmcp": fastmcp,
        },
    ):
        spec.loader.exec_module(module)
    return module


class MemoryToolTests(unittest.TestCase):
    def test_add_memory_normalizes_and_forwards_metadata(self):
        store = FakeMemoryStore()
        module = load_module(store)
        mcp = FakeMCP()
        module.register(mcp)

        output = asyncio.run(
            mcp.tools["add_memory"](
                "repo",
                "Keep graph writes transactional.",
                tags=["Neo4j", "Reliability", "neo4j"],
                category="Architecture",
                importance=5,
            )
        )

        self.assertEqual(store.add_kwargs["tags"], ["neo4j", "reliability"])
        self.assertEqual(store.add_kwargs["category"], "architecture")
        self.assertEqual(store.add_kwargs["importance"], 5)
        self.assertIn("#neo4j", output)

    def test_list_memories_forwards_filters_and_renders_metadata(self):
        store = FakeMemoryStore()
        module = load_module(store)
        mcp = FakeMCP()
        module.register(mcp)

        output = asyncio.run(
            mcp.tools["list_memories"](
                "repo",
                tags=["Neo4j"],
                category="Architecture",
                min_importance=4,
            )
        )

        self.assertEqual(store.list_kwargs["tags"], ["neo4j"])
        self.assertEqual(store.list_kwargs["category"], "architecture")
        self.assertEqual(store.list_kwargs["min_importance"], 4)
        self.assertIn("[P5 architecture #neo4j #reliability]", output)

    def test_add_memory_rejects_invalid_importance(self):
        store = FakeMemoryStore()
        module = load_module(store)
        mcp = FakeMCP()
        module.register(mcp)

        output = asyncio.run(
            mcp.tools["add_memory"]("repo", "invalid", importance=9)
        )

        self.assertIn("between 1 and 5", output)
        self.assertIsNone(store.add_kwargs)


if __name__ == "__main__":
    unittest.main()

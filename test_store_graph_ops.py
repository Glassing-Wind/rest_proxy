import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "memory" / "store_graph_ops.py"


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeDriver:
    def session(self, **kwargs):
        return _FakeSession()


def _load_module(calls):
    spec = importlib.util.spec_from_file_location("memory.store_graph_ops_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")
    graph_bootstrap_mod._NEO4J_DB = "proxy"
    graph_bootstrap_mod.get_driver = lambda: _FakeDriver()

    store_core_mod = types.ModuleType("memory.store_core")
    store_core_mod._pool_available = lambda: True
    store_core_mod._debug = lambda *args, **kwargs: None

    async def _neo4j_write(session, cypher, op, **params):
        calls.append({"session": session, "cypher": cypher, "op": op, "params": params})

    store_core_mod._neo4j_write = _neo4j_write
    memory_pkg = types.ModuleType("memory")
    memory_pkg.store_core = store_core_mod

    with mock.patch.dict(
        sys.modules,
        {
            "graph_bootstrap": graph_bootstrap_mod,
            "memory": memory_pkg,
            "memory.store_core": store_core_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


class StoreGraphOpsTests(unittest.TestCase):
    def test_insert_turns_batch_uses_unwind_write(self):
        calls = []
        module = _load_module(calls)

        row_ids = asyncio.run(
            module.insert_turns_batch(
                [
                    {
                        "session_id": "sess1",
                        "turn_index": 3,
                        "role": "user",
                        "content": "hello",
                        "compact_content": "hello",
                        "model": "gpt",
                    },
                    {
                        "session_id": "sess1",
                        "turn_index": 4,
                        "role": "assistant",
                        "content": "world",
                        "compact_content": "world",
                        "model": "gpt",
                    },
                ]
            )
        )

        self.assertEqual(len(row_ids), 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["op"], "insert_turns_batch")
        self.assertIn("UNWIND $rows AS row", calls[0]["cypher"])
        self.assertEqual(len(calls[0]["params"]["rows"]), 2)

    def test_insert_turn_uses_shared_neo4j_write_helper(self):
        calls = []
        module = _load_module(calls)

        row_id = asyncio.run(
            module.insert_turn(
                session_id="sess1",
                turn_index=3,
                role="user",
                content="hello",
                compact_content="hello",
                metadata={"k": "v"},
                project_path="/tmp/project",
            )
        )

        self.assertTrue(row_id)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["op"], "insert_turns_batch")
        self.assertEqual(calls[0]["params"]["rows"][0]["session_id"], "sess1")
        self.assertEqual(calls[0]["params"]["rows"][0]["turn_index"], 3)

    def test_insert_checkpoint_uses_shared_neo4j_write_helper(self):
        calls = []
        module = _load_module(calls)

        row_id = asyncio.run(
            module.insert_checkpoint(
                session_id="sess1",
                working_memory={"foo": "bar"},
                rolling_summary="sum",
                metadata={"kind": "checkpoint"},
            )
        )

        self.assertTrue(row_id)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["op"], "insert_checkpoints_batch")
        self.assertEqual(calls[0]["params"]["rows"][0]["rolling_summary"], "sum")

    def test_insert_summaries_batch_uses_unwind_write(self):
        calls = []
        module = _load_module(calls)

        row_ids = asyncio.run(
            module.insert_summaries_batch(
                [
                    {
                        "session_id": "sess1",
                        "summary_text": "summary",
                        "summary_type": "rolling",
                        "metadata": {"kind": "summary"},
                    }
                ]
            )
        )

        self.assertEqual(len(row_ids), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["op"], "insert_summaries_batch")
        self.assertIn("UNWIND $rows AS row", calls[0]["cypher"])
        self.assertEqual(calls[0]["params"]["rows"][0]["summary_text"], "summary")

    def test_insert_tool_outputs_batch_uses_unwind_write(self):
        calls = []
        module = _load_module(calls)

        row_ids = asyncio.run(
            module.insert_tool_outputs_batch(
                [
                    {
                        "session_id": "sess1",
                        "tool_name": "search",
                        "tool_call_id": "call1",
                        "raw_output": "raw",
                        "compact_output": "compact",
                        "metadata": {"kind": "tool"},
                    }
                ]
            )
        )

        self.assertEqual(len(row_ids), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["op"], "insert_tool_outputs_batch")
        self.assertIn("UNWIND $rows AS row", calls[0]["cypher"])
        self.assertEqual(calls[0]["params"]["rows"][0]["tool_name"], "search")


if __name__ == "__main__":
    unittest.main()

import asyncio
import unittest
from unittest import mock

from memory import store_durable


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


class DurableMemoryStoreTests(unittest.TestCase):
    def test_normalize_memory_metadata_deduplicates_and_validates(self):
        self.assertEqual(
            store_durable.normalize_memory_metadata(
                [" Neo4j ", "Reliability", "neo4j"],
                " Architecture ",
                5,
            ),
            (["neo4j", "reliability"], "architecture", 5),
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 5"):
            store_durable.normalize_memory_metadata(importance=0)

    def test_preference_reads_prioritize_importance_then_recency(self):
        read = mock.AsyncMock(return_value=[])
        with mock.patch.object(
            store_durable.graph_bootstrap, "_NEO4J_ENABLED", True
        ), mock.patch.object(
            store_durable.graph_bootstrap, "get_driver", return_value=FakeDriver()
        ), mock.patch.object(store_durable.store_core, "_neo4j_read", read):
            asyncio.run(store_durable.get_project_preferences("repo"))

        query = read.await_args.args[1]
        self.assertIn("coalesce(properties(pref).importance, 3) DESC", query)
        self.assertNotIn("coalesce(pref.importance", query)

    def test_add_durable_memory_writes_metadata_properties(self):
        write = mock.AsyncMock(return_value=[])
        with mock.patch.object(
            store_durable.graph_bootstrap, "_NEO4J_ENABLED", True
        ), mock.patch.object(
            store_durable.graph_bootstrap, "get_driver", return_value=FakeDriver()
        ), mock.patch.object(store_durable.store_core, "_neo4j_write", write):
            success = asyncio.run(
                store_durable.add_durable_memory(
                    "repo:session",
                    "Use explicit transactions.",
                    tags=["Neo4j"],
                    category="Architecture",
                    importance=5,
                )
            )

        self.assertTrue(success)
        kwargs = write.await_args.kwargs
        self.assertEqual(kwargs["pid"], "repo")
        self.assertEqual(kwargs["tags"], ["neo4j"])
        self.assertEqual(kwargs["category"], "architecture")
        self.assertEqual(kwargs["importance"], 5)

    def test_list_durable_memories_applies_metadata_filters(self):
        read = mock.AsyncMock(
            return_value=[
                {
                    "text": "critical",
                    "created_at": 20,
                    "tags": ["neo4j", "reliability"],
                    "category": "architecture",
                    "importance": 5,
                },
                {
                    "text": "routine",
                    "created_at": 10,
                    "tags": ["neo4j"],
                    "category": "workflow",
                    "importance": 2,
                },
            ]
        )
        with mock.patch.object(
            store_durable.graph_bootstrap, "_NEO4J_ENABLED", True
        ), mock.patch.object(
            store_durable.graph_bootstrap, "get_driver", return_value=FakeDriver()
        ), mock.patch.object(store_durable.store_core, "_neo4j_read", read):
            memories = asyncio.run(
                store_durable.list_durable_memories(
                    "repo",
                    tags=["Reliability"],
                    category="Architecture",
                    min_importance=4,
                )
            )

        self.assertEqual([memory["text"] for memory in memories], ["critical"])
        pref_query = read.await_args_list[0].args[1]
        self.assertIn("coalesce(properties(pref).tags, []) AS tags", pref_query)
        self.assertIn("properties(pref).category AS category", pref_query)
        self.assertIn("coalesce(properties(pref).importance, 3) AS importance", pref_query)
        self.assertNotIn("coalesce(pref.tags", pref_query)
        self.assertNotIn("pref.category AS category", pref_query)


if __name__ == "__main__":
    unittest.main()

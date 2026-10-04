"""FIRE provenance/assembly regression checks; no external services required."""
import unittest
from unittest.mock import AsyncMock, patch

from memory import retrieval
from memory.types import EvidenceReference, TaskCheckpoint


class FireContextTests(unittest.IsolatedAsyncioTestCase):
    def test_checkpoint_scope_requires_project_session_and_task(self):
        checkpoint = TaskCheckpoint("project", "session", "task", "investigate")
        self.assertTrue(checkpoint.matches_scope("project", "session", "task"))
        for scope in [("other", "session", "task"), ("project", "other", "task"),
                      ("project", "session", "other")]:
            self.assertFalse(checkpoint.matches_scope(*scope))
        reference = EvidenceReference("project", "session", "turn", "turn-1")
        self.assertEqual(reference.original_availability, "unknown")
        self.assertEqual(reference.freshness, "unknown")
        self.assertFalse(TaskCheckpoint("", "", "", "").matches_scope("", "", ""))

    async def test_assembly_preserves_ranked_provenance_and_distinct_shared_prefixes(self):
        prefix = "same opening " * 12
        hits = [
            {"compact_text": prefix + "first", "ref_id": "a", "ref_type": "turn",
             "session_id": "p:s", "project_id": "p", "rrf_score": 1},
            {"compact_text": prefix + "second", "ref_id": "b", "ref_type": "summary",
             "session_id": "q:s", "project_id": "q", "rrf_score": 2},
        ]
        with patch.object(retrieval, "_ENABLE_RETRIEVAL", True), \
             patch.object(retrieval, "_ENABLE_EMBEDDINGS", True), \
             patch.object(retrieval, "get_embedding", AsyncMock(return_value=[1.0])), \
             patch("memory.store.get_rolling_summary", AsyncMock(return_value="")), \
             patch("memory.store.get_session_state", AsyncMock(return_value={})), \
             patch("memory.store.get_recent_turns", AsyncMock(return_value=[])), \
             patch("memory.store.get_project_preferences", AsyncMock(return_value=[])), \
             patch("memory.store.get_global_instructions", AsyncMock(return_value=[])), \
             patch("memory.store.search_similar_memory", AsyncMock(return_value=hits)):
            result = await retrieval.assemble_memory("p:s", "question", global_search=True)
        self.assertEqual(result.retrieved_snippets, [hits[1]["compact_text"], hits[0]["compact_text"]])
        self.assertEqual([e.reference.source_id for e in result.retrieved_evidence], ["b", "a"])
        self.assertEqual(result.retrieved_evidence[0].reference.project_id, "q")
        self.assertEqual(result.retrieved_evidence[0].reference.original_availability, "unknown")
        self.assertIn("second", result.assembled_text)

    def test_dedup_cap_and_unknown_scope(self):
        hits = [{"compact_text": str(i), "rrf_score": i} for i in range(5)]
        selected = retrieval._select_retrieved_evidence(hits + [hits[-1]])
        self.assertEqual([e.compact_text for e in selected], ["4", "3", "2"])
        self.assertEqual(selected[0].reference.session_id, "")
        self.assertEqual(retrieval._rerank_by_recency(hits)[0], "4")

    async def test_storage_failure_returns_usable_empty_assembly(self):
        with patch.object(retrieval, "_ENABLE_RETRIEVAL", True), \
             patch.object(retrieval, "_ENABLE_EMBEDDINGS", True), \
             patch.object(retrieval, "get_embedding", AsyncMock(return_value=[1.0])), \
             patch("memory.store.get_rolling_summary", AsyncMock(side_effect=RuntimeError("offline"))), \
             patch("memory.store.get_session_state", AsyncMock(return_value={})), \
             patch("memory.store.get_recent_turns", AsyncMock(return_value=[])), \
             patch("memory.store.get_project_preferences", AsyncMock(return_value=[])), \
             patch("memory.store.get_global_instructions", AsyncMock(return_value=[])), \
             patch("memory.store.search_similar_memory", AsyncMock(side_effect=RuntimeError("offline"))):
            result = await retrieval.assemble_memory("p:s", "question")
        self.assertTrue(result.is_empty())
        self.assertEqual(result.retrieved_evidence, [])


if __name__ == "__main__":
    unittest.main()

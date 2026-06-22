import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "semantic.py"
GOLDENS_PATH = REPO_ROOT / "benchmarks" / "retrieval_duplicate_goldens.json"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def load_case(case_id: str) -> dict:
    with open(GOLDENS_PATH, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    for case in payload.get("cases") or []:
        if case.get("id") == case_id:
            return case
    raise AssertionError(f"missing benchmark case: {case_id}")


def load_semantic_module():
    spec = importlib.util.spec_from_file_location("semantic_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (None, None, None, None, None)
    helpers_mod.get_project_id = lambda workspace_id: "proj123"

    logging_mod = types.ModuleType("proxy.logging")
    logging_mod.debug_log = lambda *args, **kwargs: None

    search_core_mod = types.ModuleType("tools.brain.search.core")
    sem_helpers_mod = types.ModuleType("tools.brain.search.semantic_helpers")
    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "proxy.logging": logging_mod,
            "tools.brain.search.core": search_core_mod,
            "tools.brain.search.semantic_helpers": sem_helpers_mod,
            "mcp.server.fastmcp": mcp_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module, sem_helpers_mod


class SearchRerankToolTests(unittest.TestCase):
    def test_rerank_retrieval_results_tool_returns_json_contract(self):
        module, sem_helpers_mod = load_semantic_module()
        case = load_case("docs_canonical_mirror_preferred")
        sem_helpers_mod.rerank_retrieval_results_contract = mock.Mock(
            return_value={
                "results": [
                    {"original_index": 0, "source_url": case["results"][0]["file_path"], "content": case["results"][0]["content"]},
                    {"original_index": 2, "source_url": case["results"][2]["file_path"], "content": case["results"][2]["content"]},
                ],
                "keep_indices": [0, 2],
                "suppressed_indices": [1],
                "groups": [{"group_id": 0, "members": [0, 1], "canonical_candidates": [0]}],
                "pairs": [{"left": 0, "right": 1, "duplicate": True, "score": 0.95}],
                "selection": {"keep_indices": [0, 2], "suppressed_indices": [1]},
                "telemetry": {"canonical_doc_preference_success": True},
                "suppression_policy": "exact_only",
                "experiments": case.get("experiments"),
                "trace": [{"idx": 1, "beaten_by": 0, "decision_reason": "canonical_doc"}],
            }
        )
        mcp = FakeMCP()
        module.register(mcp)
        output = asyncio.run(
            mcp.tools["rerank_retrieval_results"](
                case["query"],
                case["results"],
                mode=case["mode"],
                experiments=case.get("experiments"),
                include_debug=True,
            )
        )
        payload = json.loads(output)
        self.assertEqual(payload["keep_indices"], [0, 2])
        self.assertEqual(payload["suppressed_indices"], [1])
        self.assertEqual(payload["results"][0]["original_index"], 0)
        self.assertEqual(payload["trace"][0]["decision_reason"], "canonical_doc")

    def test_analyze_duplicate_results_tool_returns_groups_and_pairs(self):
        module, sem_helpers_mod = load_semantic_module()
        case = load_case("code_exact_duplicate_helpers")
        sem_helpers_mod.analyze_duplicate_results_contract = mock.Mock(
            return_value={
                "keep_indices": [0, 2],
                "suppressed_indices": [1],
                "pairs": [{"left": 0, "right": 1, "duplicate": True, "score": 0.99}],
                "groups": [{"group_id": 0, "members": [0, 1], "canonical_candidates": [0]}],
                "mode": "code_retrieval",
            }
        )
        mcp = FakeMCP()
        module.register(mcp)
        output = asyncio.run(
            mcp.tools["analyze_duplicate_results"](
                case["query"],
                case["results"],
                mode=case["mode"],
                include_debug=True,
            )
        )
        payload = json.loads(output)
        self.assertEqual(payload["keep_indices"], [0, 2])
        self.assertEqual(payload["groups"][0]["members"], [0, 1])
        self.assertEqual(payload["pairs"][0]["right"], 1)

    def test_trace_code_ranking_tool_returns_structural_trace(self):
        module, sem_helpers_mod = load_semantic_module()
        sem_helpers_mod.build_implementation_ranking_trace = mock.Mock(
            return_value={
                "query_class": "usage_lookup",
                "rows": [
                    {
                        "file_path": "examples/python_smoke/main.py",
                        "rank_score": 0.91,
                        "role": "usage_callsite",
                        "node_types": ["call_expression"],
                        "components": {"member_usage_bonus": 0.06},
                    }
                ],
            }
        )
        mcp = FakeMCP()
        module.register(mcp)
        output = asyncio.run(
            mcp.tools["trace_code_ranking"](
                "where is parser.parse used in tree-sitter-language-pack",
                [{"file_path": "examples/python_smoke/main.py", "content": "tree = parser.parse(b'x')"}],
                include_debug=True,
            )
        )
        payload = json.loads(output)
        self.assertEqual(payload["query_class"], "usage_lookup")
        self.assertEqual(payload["rows"][0]["file_path"], "examples/python_smoke/main.py")
        self.assertEqual(payload["rows"][0]["role"], "usage_callsite")


if __name__ == "__main__":
    unittest.main()

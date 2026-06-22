import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _semantic_contract import SEMANTIC_CONTRACT_VERSION


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "semantic_helpers.py"
FALLBACKS_PATH = REPO_ROOT / "tools" / "brain" / "search" / "fallbacks.py"
GOLDENS_PATH = REPO_ROOT / "benchmarks" / "retrieval_duplicate_goldens.json"


spec = importlib.util.spec_from_file_location("semantic_helpers_under_test", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

fallbacks_spec = importlib.util.spec_from_file_location("semantic_fallbacks_under_test", FALLBACKS_PATH)
fallbacks_module = importlib.util.module_from_spec(fallbacks_spec)
assert fallbacks_spec.loader is not None
fallbacks_spec.loader.exec_module(fallbacks_module)


def load_benchmark_case(case_id: str) -> dict:
    with open(GOLDENS_PATH, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    for case in payload.get("cases") or []:
        if case.get("id") == case_id:
            return case
    raise AssertionError(f"missing benchmark case: {case_id}")


def current_contract_meta(meta: dict | None = None, *, file_roles: list[str] | None = None) -> dict:
    payload = dict(meta or {})
    payload["semantic_contract_version"] = SEMANTIC_CONTRACT_VERSION
    payload["file_roles"] = list(file_roles or [])
    return payload


class SemanticHelperTests(unittest.TestCase):
    def test_meta_helpers_and_filters(self):
        meta = {
            "language": "python",
            "file_imports": ["os"],
            "file_symbols": ["run"],
            "file_diagnostics": {"count": 1},
            "context_path": ["Service", "run"],
        }
        self.assertGreater(module.meta_score(meta), 0)
        self.assertTrue(
            module.passes_filters(
                meta,
                languages=["python"],
                min_imports=1,
                min_symbols=1,
                require_diagnostics=True,
                require_context=True,
            )
        )
        self.assertTrue(any("lang=python" in line for line in module.format_meta(meta)))

    def test_dedupe_and_caps_are_stable(self):
        rows = [
            {"file_path": "src/a.py", "project_id": "p", "rrf": 1.0, "content": "a"},
            {"file_path": "src/a.py", "project_id": "p", "rrf": 0.9, "content": "b"},
            {"file_path": "tests/x.py", "project_id": "p", "rrf": 0.8, "content": "c"},
        ]
        self.assertEqual(len(module.dedupe_files(rows)), 2)
        self.assertEqual(len(module.cap_per_file(rows, 1)), 2)
        self.assertEqual(len(module.cap_per_dir(rows, 1)), 2)

    def test_dedupe_files_prefers_best_implementation_chunk_per_file(self):
        query = "where is model inference selected"
        query_class = module.implementation_query_class(query)
        weaker = {
            "file_path": "pkg/models/__init__.py",
            "project_id": "p",
            "rrf": 0.40,
            "content": "// File: pkg/models/__init__.py\ndef helper():\n    return None\n",
            "metadata": current_contract_meta(
                {
                    "declared_symbols": ["helper"],
                    "node_types": ["function_definition"],
                },
                file_roles=["library_facade_surface"],
            ),
        }
        stronger = {
            "file_path": "pkg/models/__init__.py",
            "project_id": "p",
            "rrf": 0.20,
            "content": (
                "// File: pkg/models/__init__.py\n"
                "// Semantic role: canonical model inference selection dispatcher\n"
                "def infer_model(model_name: str):\n    return model_name\n"
            ),
            "metadata": current_contract_meta(
                {
                    "declared_symbols": ["infer_model"],
                    "declared_symbol_roles": {
                        "infer_model": ["canonical_dispatcher", "dispatcher", "model_selector"]
                    },
                    "chunk_role": "canonical_dispatcher_definition",
                    "node_types": ["function_definition"],
                },
                file_roles=["dispatcher_surface", "model_dispatcher_surface", "library_facade_surface"],
            ),
        }
        for row in (weaker, stronger):
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row["rrf"]),
            )
        deduped = module.dedupe_files([weaker, stronger])
        self.assertEqual(len(deduped), 1)
        self.assertIn("infer_model", deduped[0]["content"])

    def test_dispatcher_query_detection_requires_dispatcher_context(self):
        self.assertTrue(module.implementation_query_prefers_dispatchers("where is model inference selected"))
        self.assertTrue(module.implementation_query_prefers_dispatchers("how does OpenAI provider wiring work"))
        self.assertTrue(module.implementation_query_prefers_dispatchers("where is request dispatch implemented"))
        self.assertFalse(module.implementation_query_prefers_dispatchers("where is selection logic implemented"))
        self.assertFalse(module.implementation_query_prefers_dispatchers("how does inference caching work"))

    def test_request_routing_detection_avoids_ambiguous_non_routing_queries(self):
        self.assertTrue(
            module.implementation_query_prefers_request_routing(
                "where is owner request routing implemented in spring petclinic"
            )
        )
        self.assertTrue(
            module.implementation_query_prefers_request_routing(
                "where are incoming http requests handled in gin"
            )
        )
        self.assertTrue(
            module.implementation_query_prefers_request_routing(
                "how does gRPC server request routing work"
            )
        )
        self.assertFalse(
            module.implementation_query_prefers_request_routing(
                "how does json schema conversion work"
            )
        )
        self.assertFalse(
            module.implementation_query_prefers_request_routing(
                "where is project lock resolution executed in uv"
            )
        )
        self.assertFalse(
            module.implementation_query_prefers_request_routing(
                "how does inference caching work"
            )
        )
        self.assertFalse(
            module.implementation_query_prefers_request_routing(
                "where is selection logic implemented"
            )
        )

    def test_dedupe_files_prefers_contract_anchor_for_provider_dispatcher(self):
        query = "how does OpenAI provider wiring work"
        query_class = module.implementation_query_class(query)
        plain = {
            "file_path": "pkg/providers/__init__.py",
            "project_id": "p",
            "rrf": 0.50,
            "content": "def infer_provider_class(provider: str):\n    return provider\n",
            "metadata": current_contract_meta(
                {
                    "declared_symbols": ["infer_provider_class"],
                    "declared_symbol_roles": {
                        "infer_provider_class": [
                            "canonical_dispatcher",
                            "dispatcher",
                            "provider_selector",
                        ]
                    },
                    "chunk_role": "canonical_dispatcher_definition",
                    "node_types": ["function_definition"],
                },
                file_roles=["dispatcher_surface", "provider_dispatcher_surface"],
            ),
        }
        focused = {
            "file_path": "pkg/providers/__init__.py",
            "project_id": "p",
            "rrf": 0.48,
            "content": (
                "// Semantic role: canonical provider inference selection dispatcher\n"
                "// Query intent: where provider inference is selected; canonical provider wiring and selection entrypoint\n"
                "def infer_provider_class(provider: str):\n    return provider\n"
            ),
            "metadata": current_contract_meta(
                {
                    "declared_symbols": ["infer_provider_class"],
                    "file_symbols": ["infer_provider_class", "infer_provider"],
                    "declared_symbol_roles": {
                        "infer_provider_class": [
                            "canonical_dispatcher",
                            "dispatcher",
                            "provider_selector",
                        ]
                    },
                    "chunk_role": "canonical_dispatcher_definition",
                    "focused_dispatcher_anchor_contract_version": 1,
                    "semantic_contract_capabilities": ["focused_dispatcher_anchor_v1"],
                    "node_types": ["function_definition"],
                },
                file_roles=["dispatcher_surface", "provider_dispatcher_surface"],
            ),
        }
        for row in (plain, focused):
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row["rrf"]),
            )
        deduped = module.dedupe_files([plain, focused])
        self.assertEqual(len(deduped), 1)
        self.assertIn("provider wiring and selection entrypoint", deduped[0]["content"])

    def test_analyze_near_duplicate_results_uses_lower_level_contract(self):
        rows = [
            {"file_path": "src/a.py", "project_id": "p", "rrf": 1.0, "content": "same-a"},
            {"file_path": "src/b.py", "project_id": "p", "rrf": 0.9, "content": "same-b"},
            {"file_path": "src/c.py", "project_id": "p", "rrf": 0.8, "content": "different"},
        ]
        fake_ts_pack = mock.Mock()
        fake_ts_pack.analyze_duplicate_texts.return_value = {
            "mode": "code_retrieval",
            "keep_indices": [0, 2],
            "suppressed_indices": [1],
            "pairs": [{"left": 0, "right": 1, "duplicate": True, "score": 0.95}],
            "groups": [{"group_id": 0, "members": [0, 1], "canonical_candidates": [0, 1]}],
        }
        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            analysis = module.analyze_near_duplicate_results(rows, query="add user api", mode="code")
        self.assertEqual(analysis["suppressed_indices"], [1])
        self.assertEqual(analysis["keep_indices"], [0, 2])
        self.assertEqual(analysis["groups"][0]["members"], [0, 1])
        fake_ts_pack.analyze_duplicate_texts.assert_called_once()

    def test_collapse_near_duplicate_results_prefers_shared_rerank_contract(self):
        rows = [
            {"file_path": "src/a.py", "project_id": "p", "rrf": 1.0, "content": "same-a"},
            {"file_path": "src/b.py", "project_id": "p", "rrf": 0.9, "content": "same-b"},
            {"file_path": "src/c.py", "project_id": "p", "rrf": 0.8, "content": "different"},
        ]
        with mock.patch.object(
            module,
            "rerank_retrieval_results_contract",
            return_value={"keep_indices": [2, 0], "suppressed_indices": [1]},
        ):
            collapsed = module.collapse_near_duplicate_results(rows, query="delete user helper", mode="code")
        self.assertEqual([row["file_path"] for row in collapsed], ["src/c.py", "src/a.py"])

    def test_trace_diverse_results_returns_telemetry_contract(self):
        rows = [
            {"file_path": "src/a.py", "project_id": "p", "rrf": 1.0, "content": "same-a"},
            {"file_path": "src/b.py", "project_id": "p", "rrf": 0.9, "content": "same-b"},
        ]
        fake_ts_pack = mock.Mock()
        fake_ts_pack.trace_diverse_texts.return_value = {
            "selection": {
                "mode": "code_retrieval",
                "keep_indices": [0, 1],
                "suppressed_indices": [],
                "exact_suppressed_indices": [],
                "group_order": [0],
                "representative_indices": [0],
            },
            "candidates": [{"idx": 0, "kept": True}],
            "telemetry": {"query_class": "symbol_lookup", "topk_redundancy_before": 0.5, "topk_redundancy_after": 0.2},
            "suppression_policy": "exact_only",
            "experiments": {"helper_clone_suppression": False},
        }
        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            trace = module.trace_diverse_results(rows, query="lookup symbol", mode="code")
        self.assertEqual(trace["selection"]["keep_indices"], [0, 1])
        self.assertEqual(trace["telemetry"]["query_class"], "symbol_lookup")
        fake_ts_pack.trace_diverse_texts.assert_called_once()

    def test_rerank_contract_collapses_code_exact_duplicates(self):
        case = load_benchmark_case("code_exact_duplicate_helpers")
        fake_trace = {
            "selection": {
                "mode": "code_retrieval",
                "keep_indices": [0, 2],
                "suppressed_indices": [1],
                "exact_suppressed_indices": [1],
                "group_order": [0, 2],
                "representative_indices": [0, 2],
                "mmr_lambda": 0.78,
                "aspect_lambda": 0.18,
                "selected_aspects": [],
            },
            "candidates": [
                {"idx": 0, "group_id": 0, "kept": True, "decision_reason": "best_answer"},
                {"idx": 1, "group_id": 0, "kept": False, "beaten_by": 0, "decision_reason": "exact_duplicate"},
                {"idx": 2, "group_id": 2, "kept": True, "decision_reason": "distinct"},
            ],
            "telemetry": {"query_class": "symbol_lookup", "exact_suppressions": 1},
            "suppression_policy": "exact_only",
            "experiments": case.get("experiments"),
        }
        fake_analysis = {
            "mode": "code_retrieval",
            "keep_indices": [0, 2],
            "suppressed_indices": [1],
            "pairs": [{"left": 0, "right": 1, "duplicate": True, "score": 0.99}],
            "groups": [{"group_id": 0, "members": [0, 1], "canonical_candidates": [0]}],
        }
        with (
            mock.patch.object(module, "trace_diverse_results", return_value=fake_trace),
            mock.patch.object(module, "analyze_near_duplicate_results", return_value=fake_analysis),
        ):
            contract = module.rerank_retrieval_results_contract(
                case["results"],
                query=case["query"],
                mode=case["mode"],
                experiments=case.get("experiments"),
                include_debug=True,
            )
        self.assertEqual(contract["keep_indices"], [0, 2])
        self.assertEqual(contract["suppressed_indices"], [1])
        self.assertEqual([row["original_index"] for row in contract["results"]], [0, 2])
        self.assertEqual(contract["pairs"][0]["left"], 0)
        self.assertEqual(contract["groups"][0]["members"], [0, 1])
        self.assertEqual(contract["trace"][1]["decision_reason"], "exact_duplicate")

    def test_rerank_contract_keeps_docs_reference_and_tutorial(self):
        case = load_benchmark_case("docs_tutorial_and_reference_survive")
        fake_trace = {
            "selection": {
                "mode": "docs_retrieval",
                "keep_indices": [0, 1],
                "suppressed_indices": [2],
                "exact_suppressed_indices": [],
                "group_order": [0, 1],
                "representative_indices": [0, 1],
            },
            "candidates": [],
            "telemetry": {"canonical_doc_preference_success": True},
            "suppression_policy": "exact_only",
            "experiments": case.get("experiments"),
        }
        fake_analysis = {
            "mode": "docs_retrieval",
            "keep_indices": [0, 1],
            "suppressed_indices": [2],
            "pairs": [{"left": 1, "right": 2, "duplicate": True, "score": 0.93}],
            "groups": [{"group_id": 1, "members": [1, 2], "canonical_candidates": [1]}],
        }
        with (
            mock.patch.object(module, "trace_diverse_results", return_value=fake_trace),
            mock.patch.object(module, "analyze_near_duplicate_results", return_value=fake_analysis),
        ):
            contract = module.rerank_retrieval_results_contract(
                case["results"],
                query=case["query"],
                mode=case["mode"],
                experiments=case.get("experiments"),
            )
        self.assertEqual(contract["keep_indices"], [0, 1])
        self.assertEqual([row["original_index"] for row in contract["results"]], [0, 1])
        self.assertEqual(contract["suppressed_indices"], [2])
        self.assertEqual(contract["suppression_policy"], "exact_only")

    def test_rerank_contract_labels_non_exact_suppression_policy_honestly(self):
        case = load_benchmark_case("docs_canonical_mirror_preferred")
        fake_trace = {
            "selection": {
                "mode": "docs_retrieval",
                "keep_indices": [0, 2],
                "suppressed_indices": [1],
                "exact_suppressed_indices": [],
                "group_order": [0, 2],
                "representative_indices": [0, 2],
            },
            "candidates": [
                {
                    "idx": 1,
                    "group_id": 0,
                    "kept": False,
                    "beaten_by": 0,
                    "decision_reason": "experimental_non_exact_suppressed",
                    "duplicate_relations": ["canonical_docs_mirror"],
                }
            ],
            "telemetry": {"experimental_suppressions": 1},
            "suppression_policy": "exact_only",
            "experiments": case.get("experiments"),
        }
        fake_analysis = {
            "mode": "docs_retrieval",
            "keep_indices": [0, 2],
            "suppressed_indices": [1],
            "pairs": [{"left": 0, "right": 1, "duplicate": True, "score": 0.95}],
            "groups": [{"group_id": 0, "members": [0, 1], "canonical_candidates": [0]}],
        }
        with (
            mock.patch.object(module, "trace_diverse_results", return_value=fake_trace),
            mock.patch.object(module, "analyze_near_duplicate_results", return_value=fake_analysis),
        ):
            contract = module.rerank_retrieval_results_contract(
                case["results"],
                query=case["query"],
                mode=case["mode"],
                experiments=case.get("experiments"),
                include_debug=True,
            )
        self.assertEqual(contract["suppression_policy"], "experimental_non_exact")

    def test_rerank_contract_preserves_best_answer_for_query_aware_code_case(self):
        case = load_benchmark_case("code_renamed_helper_clones")
        fake_trace = {
            "selection": {
                "mode": "code_retrieval",
                "keep_indices": [2, 0],
                "suppressed_indices": [1],
                "exact_suppressed_indices": [],
                "group_order": [2, 0],
                "representative_indices": [2, 0],
            },
            "candidates": [],
            "telemetry": {"best_answer_loss_suspect": False},
            "suppression_policy": "query_aware",
            "experiments": case.get("experiments"),
        }
        fake_analysis = {
            "mode": "code_retrieval",
            "keep_indices": [2, 0],
            "suppressed_indices": [1],
            "pairs": [{"left": 0, "right": 1, "duplicate": True, "score": 0.9}],
            "groups": [{"group_id": 0, "members": [0, 1], "canonical_candidates": [0]}],
        }
        with (
            mock.patch.object(module, "trace_diverse_results", return_value=fake_trace),
            mock.patch.object(module, "analyze_near_duplicate_results", return_value=fake_analysis),
        ):
            contract = module.rerank_retrieval_results_contract(
                case["results"],
                query=case["query"],
                mode=case["mode"],
                experiments=case.get("experiments"),
            )
        self.assertEqual(contract["results"][0]["original_index"], 2)
        self.assertEqual(contract["keep_indices"], [2, 0])

    def test_analyze_duplicate_results_contract_reports_groups_without_reranking(self):
        case = load_benchmark_case("docs_canonical_mirror_preferred")
        fake_analysis = {
            "mode": "docs_retrieval",
            "keep_indices": [0, 2],
            "suppressed_indices": [1],
            "pairs": [{"left": 0, "right": 1, "duplicate": True, "score": 0.95}],
            "groups": [{"group_id": 0, "members": [0, 1], "canonical_candidates": [0]}],
        }
        with mock.patch.object(module, "analyze_near_duplicate_results", return_value=fake_analysis):
            contract = module.analyze_duplicate_results_contract(
                case["results"],
                query=case["query"],
                mode=case["mode"],
            )
        self.assertEqual(contract["keep_indices"], [0, 2])
        self.assertEqual(contract["suppressed_indices"], [1])
        self.assertEqual(contract["pairs"][0]["right"], 1)

    def test_compact_duplicate_analysis_reports_decisions_without_content(self):
        results = [
            {"file_path": "src/a.py", "content": "large source A"},
            {"file_path": "src/b.py", "content": "large source B"},
        ]
        compact = module.compact_duplicate_analysis(
            {
                "mode": "code_retrieval",
                "keep_indices": [0],
                "suppressed_indices": [1],
                "pairs": [{"left": 0, "right": 1, "duplicate": True, "score": 0.99}],
            },
            results,
        )
        self.assertEqual(compact["summary"]["suppressed_count"], 1)
        self.assertEqual(compact["duplicate_pairs"][0]["right_path"], "src/b.py")
        self.assertNotIn("content", json.dumps(compact))

    def test_compact_rerank_contract_reports_order_and_health(self):
        results = [
            {"file_path": "src/a.py", "content": "A"},
            {"file_path": "src/b.py", "content": "B"},
        ]
        compact = module.compact_rerank_contract(
            {
                "keep_indices": [1],
                "suppressed_indices": [0],
                "suppression_policy": "exact_only",
                "telemetry": {
                    "relation_counts": {"exact_duplicate": 1},
                    "topk_redundancy_before": 0.5,
                    "topk_redundancy_after": 0.0,
                    "regression_alerts": [],
                },
            },
            results,
        )
        self.assertEqual(compact["ordered_results"], [{"index": 1, "path": "src/b.py"}])
        self.assertEqual(compact["redundancy"], {"before": 0.5, "after": 0.0})

    def test_compact_ranking_trace_omits_zero_components_and_guides_empty_input(self):
        compact = module.compact_implementation_ranking_trace(
            {
                "query_class": "implementation_explanation",
                "rows": [
                    {
                        "file_path": "src/service.py",
                        "base_relevance": 0.8,
                        "rank_score": 0.9,
                        "role": "internal_implementation",
                        "node_types": ["function_definition"],
                        "components": {
                            "base_relevance": 0.8,
                            "definition_bonus": 0.1,
                            "doc_penalty": 0.0,
                            "role": "internal_implementation",
                        },
                    }
                ],
            }
        )
        self.assertEqual(compact["rows"][0]["contributions"], {"definition_bonus": 0.1})
        empty = module.compact_implementation_ranking_trace({"query_class": "general", "rows": []})
        self.assertIn("guidance", empty)

    def test_rerank_contract_is_deterministic_for_same_input(self):
        case = load_benchmark_case("docs_prose_near_duplicates_do_not_overcollapse")
        fake_trace = {
            "selection": {
                "mode": "docs_retrieval",
                "keep_indices": [0, 1],
                "suppressed_indices": [2],
                "exact_suppressed_indices": [],
                "group_order": [0, 1],
                "representative_indices": [0, 1],
            },
            "candidates": [],
            "telemetry": {"query_class": "docs_incident"},
            "suppression_policy": "exact_only",
            "experiments": case.get("experiments"),
        }
        fake_analysis = {
            "mode": "docs_retrieval",
            "keep_indices": [0, 1],
            "suppressed_indices": [2],
            "pairs": [{"left": 0, "right": 2, "duplicate": True, "score": 0.91}],
            "groups": [{"group_id": 0, "members": [0, 2], "canonical_candidates": [0]}],
        }
        with (
            mock.patch.object(module, "trace_diverse_results", return_value=fake_trace),
            mock.patch.object(module, "analyze_near_duplicate_results", return_value=fake_analysis),
        ):
            first = module.rerank_retrieval_results_contract(
                case["results"],
                query=case["query"],
                mode=case["mode"],
                experiments=case.get("experiments"),
            )
            second = module.rerank_retrieval_results_contract(
                case["results"],
                query=case["query"],
                mode=case["mode"],
                experiments=case.get("experiments"),
            )
        self.assertEqual(first, second)

    def test_duplicate_experiment_flags_respect_rollout_stage(self):
        with mock.patch.dict(os.environ, {"LM_PROXY_DUPLICATE_ROLLOUT_STAGE": "stage2"}, clear=False):
            flags = module.duplicate_experiment_flags_from_env("code")
        self.assertTrue(flags["boilerplate_variant_suppression"])
        self.assertTrue(flags["canonical_docs_mirror_suppression"])
        self.assertFalse(flags["helper_clone_suppression"])

    def test_duplicate_experiment_flags_default_to_stage2(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            flags = module.duplicate_experiment_flags_from_env("code")
        self.assertTrue(flags["boilerplate_variant_suppression"])
        self.assertTrue(flags["canonical_docs_mirror_suppression"])
        self.assertFalse(flags["helper_clone_suppression"])

    def test_duplicate_experiment_flags_allow_exact_only_disable(self):
        with mock.patch.dict(os.environ, {"LM_PROXY_DUPLICATE_ROLLOUT_STAGE": "off"}, clear=False):
            flags = module.duplicate_experiment_flags_from_env("code")
        self.assertFalse(flags["boilerplate_variant_suppression"])
        self.assertFalse(flags["canonical_docs_mirror_suppression"])
        self.assertFalse(flags["helper_clone_suppression"])

    def test_duplicate_experiment_flags_docs_mode_only_enables_docs_safe_flags(self):
        with mock.patch.dict(os.environ, {"LM_PROXY_DUPLICATE_ROLLOUT_STAGE": "stage2"}, clear=False):
            flags = module.duplicate_experiment_flags_from_env("docs")
        self.assertFalse(flags["boilerplate_variant_suppression"])
        self.assertTrue(flags["canonical_docs_mirror_suppression"])
        self.assertFalse(flags["helper_clone_suppression"])

    def test_duplicate_experiment_flags_with_query_class_adds_override(self):
        with mock.patch.dict(os.environ, {"LM_PROXY_DUPLICATE_ROLLOUT_STAGE": "stage2"}, clear=False):
            flags = module.duplicate_experiment_flags_with_query_class("code", "usage_lookup")
        self.assertEqual(flags["query_class_override"], "usage_lookup")
        self.assertTrue(flags["boilerplate_variant_suppression"])

    def test_append_duplicate_telemetry_event_writes_ndjson(self):
        trace = {
            "selection": {"keep_indices": [0]},
            "telemetry": {"query_class": "docs_incident", "experimental_suppressions": 1},
            "suppression_policy": "exact_only",
            "experiments": {"boilerplate_variant_suppression": True},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "dup.ndjson")
            with mock.patch.dict(
                os.environ,
                {
                    "LM_PROXY_DUPLICATE_TELEMETRY": "1",
                    "LM_PROXY_DUPLICATE_TELEMETRY_PATH": target,
                },
                clear=False,
            ):
                module.append_duplicate_telemetry_event(
                    trace,
                    query="deadlock retry",
                    tool="search_documentation",
                    mode="docs",
                    topic="neo4j",
                )
            with open(target, "r", encoding="utf-8") as fh:
                event = json.loads(fh.read().strip())
        self.assertEqual(event["tool"], "search_documentation")
        self.assertEqual(event["mode"], "docs")
        self.assertEqual(event["topic"], "neo4j")
        self.assertEqual(event["telemetry"]["experimental_suppressions"], 1)

    def test_append_duplicate_telemetry_event_trims_to_recent_max_events(self):
        trace = {
            "selection": {"keep_indices": [0]},
            "telemetry": {"query_class": "docs_incident", "experimental_suppressions": 1},
            "suppression_policy": "exact_only",
            "experiments": {"boilerplate_variant_suppression": True},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "dup.ndjson")
            with mock.patch.dict(
                os.environ,
                {
                    "LM_PROXY_DUPLICATE_TELEMETRY": "1",
                    "LM_PROXY_DUPLICATE_TELEMETRY_PATH": target,
                    "LM_PROXY_DUPLICATE_TELEMETRY_MAX_EVENTS": "2",
                },
                clear=False,
            ):
                module.append_duplicate_telemetry_event(
                    dict(trace),
                    query="q1",
                    tool="search_documentation",
                    mode="docs",
                )
                module.append_duplicate_telemetry_event(
                    dict(trace),
                    query="q2",
                    tool="search_documentation",
                    mode="docs",
                )
                module.append_duplicate_telemetry_event(
                    dict(trace),
                    query="q3",
                    tool="search_documentation",
                    mode="docs",
                )
            with open(target, "r", encoding="utf-8") as fh:
                events = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(events), 2)
        self.assertEqual([event["query"] for event in events], ["q2", "q3"])

    def test_dispatcher_contract_telemetry_distinguishes_missing_contract_from_missing_recall(self):
        semantic_candidates = [
            {
                "file_path": "pkg/models/__init__.py",
                "project_id": "p",
                "implementation_dispatcher_priority": 6,
                "metadata": current_contract_meta(
                    {
                        "declared_symbols": ["infer_model"],
                        "declared_symbol_roles": {
                            "infer_model": ["canonical_dispatcher", "dispatcher", "model_selector"]
                        },
                    },
                    file_roles=["dispatcher_surface", "model_dispatcher_surface"],
                ),
            }
        ]
        final_results = [
            {
                "file_path": "pkg/models/__init__.py",
                "project_id": "p",
                "implementation_dispatcher_priority": 6,
                "metadata": current_contract_meta(
                    {
                        "declared_symbols": ["infer_model"],
                        "declared_symbol_roles": {
                            "infer_model": ["canonical_dispatcher", "dispatcher", "model_selector"]
                        },
                        "focused_dispatcher_anchor_contract_version": 1,
                        "semantic_contract_capabilities": ["focused_dispatcher_anchor_v1"],
                    },
                    file_roles=["dispatcher_surface", "model_dispatcher_surface"],
                ),
            }
        ]
        telemetry = module.dispatcher_contract_telemetry(
            query="where is model inference selected",
            query_class="api_definition_lookup",
            semantic_candidates=semantic_candidates,
            ranked_candidates=semantic_candidates,
            final_results=final_results,
            rescue_applied=True,
        )
        self.assertEqual(telemetry["diagnosis"], "contract_missing_from_semantic_candidates_but_recovered")
        self.assertEqual(telemetry["semantic_contract_match_count"], 0)
        self.assertTrue(telemetry["final_top"]["contract_hit"])

    def test_dispatcher_contract_telemetry_reports_semantic_recall_missing_contract_candidate(self):
        non_dispatcher = {
            "file_path": "pkg/embeddings/__init__.py",
            "project_id": "p",
            "implementation_dispatcher_priority": 0,
            "metadata": current_contract_meta(
                {"declared_symbols": ["EmbeddingsModel"]},
                file_roles=["library_facade_surface"],
            ),
        }
        recovered = {
            "file_path": "pkg/models/__init__.py",
            "project_id": "p",
            "implementation_dispatcher_priority": 6,
            "metadata": current_contract_meta(
                {
                    "declared_symbols": ["infer_model"],
                    "declared_symbol_roles": {
                        "infer_model": ["canonical_dispatcher", "dispatcher", "model_selector"]
                    },
                    "focused_dispatcher_anchor_contract_version": 1,
                    "semantic_contract_capabilities": ["focused_dispatcher_anchor_v1"],
                },
                file_roles=["dispatcher_surface", "model_dispatcher_surface"],
            ),
        }
        telemetry = module.dispatcher_contract_telemetry(
            query="where is model inference selected",
            query_class="api_definition_lookup",
            semantic_candidates=[non_dispatcher],
            ranked_candidates=[non_dispatcher],
            final_results=[recovered],
            rescue_applied=True,
        )
        self.assertEqual(telemetry["diagnosis"], "semantic_recall_missing_contract_candidate")
        self.assertEqual(telemetry["semantic_exact_match_count"], 0)
        self.assertTrue(telemetry["final_top"]["contract_hit"])

    def test_append_dispatcher_telemetry_event_writes_ndjson(self):
        telemetry = {
            "query_class": "api_definition_lookup",
            "diagnosis": "semantic_recall_missing_contract_candidate",
            "dispatcher_anchor_contract_capability": "focused_dispatcher_anchor_v1",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "dispatcher.ndjson")
            with mock.patch.dict(
                os.environ,
                {
                    "LM_PROXY_DISPATCHER_TELEMETRY": "1",
                    "LM_PROXY_DISPATCHER_TELEMETRY_PATH": target,
                },
                clear=False,
            ):
                module.append_dispatcher_telemetry_event(
                    telemetry,
                    query="where is model inference selected",
                    tool="search_codebase",
                    topic="pydantic-ai",
                )
            with open(target, "r", encoding="utf-8") as fh:
                event = json.loads(fh.read().strip())
        self.assertEqual(event["tool"], "search_codebase")
        self.assertEqual(event["topic"], "pydantic-ai")
        self.assertEqual(event["telemetry"]["diagnosis"], "semantic_recall_missing_contract_candidate")

    def test_append_dispatcher_telemetry_event_trims_to_recent_max_events(self):
        telemetry = {
            "query_class": "api_definition_lookup",
            "diagnosis": "ranking_surfaces_contract",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "dispatcher.ndjson")
            with mock.patch.dict(
                os.environ,
                {
                    "LM_PROXY_DISPATCHER_TELEMETRY": "1",
                    "LM_PROXY_DISPATCHER_TELEMETRY_PATH": target,
                    "LM_PROXY_DISPATCHER_TELEMETRY_MAX_EVENTS": "2",
                },
                clear=False,
            ):
                module.append_dispatcher_telemetry_event(
                    dict(telemetry, ordinal=1),
                    query="q1",
                    tool="search_codebase",
                )
                module.append_dispatcher_telemetry_event(
                    dict(telemetry, ordinal=2),
                    query="q2",
                    tool="search_codebase",
                )
                module.append_dispatcher_telemetry_event(
                    dict(telemetry, ordinal=3),
                    query="q3",
                    tool="search_codebase",
                )
            with open(target, "r", encoding="utf-8") as fh:
                events = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(events), 2)
        self.assertEqual([event["query"] for event in events], ["q2", "q3"])

    def test_routing_signal_telemetry_reports_partitioned_controller_result(self):
        controller = {
            "file_path": "src/main/java/org/example/OwnerController.java",
            "project_id": "p",
            "implementation_routing_priority": 2,
            "implementation_request_handler_priority": 2,
            "implementation_controller_entity_hit": 2,
        }
        non_signal = {
            "file_path": "src/main/resources/templates/owners.html",
            "project_id": "p",
            "implementation_routing_priority": 0,
            "implementation_request_handler_priority": 0,
            "implementation_controller_entity_hit": 0,
        }
        telemetry = module.routing_signal_telemetry(
            query="where is owner request routing implemented in spring petclinic",
            query_class="api_definition_lookup",
            semantic_candidates=[controller, non_signal],
            ranked_candidates=[non_signal, controller],
            final_results=[controller, non_signal],
            partition_applied=True,
        )
        self.assertEqual(telemetry["diagnosis"], "partition_surfaces_routing_signal")
        self.assertEqual(telemetry["semantic_signal_match_count"], 1)
        self.assertTrue(telemetry["final_top"]["controller_entity_hit"])

    def test_append_routing_telemetry_event_writes_ndjson(self):
        telemetry = {
            "query_class": "implementation_explanation",
            "diagnosis": "ranking_surfaces_routing_signal",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "routing.ndjson")
            with mock.patch.dict(
                os.environ,
                {
                    "LM_PROXY_ROUTING_TELEMETRY": "1",
                    "LM_PROXY_ROUTING_TELEMETRY_PATH": target,
                },
                clear=False,
            ):
                module.append_routing_telemetry_event(
                    telemetry,
                    query="how does gRPC server request routing work",
                    tool="search_codebase",
                    topic="draw-things",
                )
            with open(target, "r", encoding="utf-8") as fh:
                event = json.loads(fh.read().strip())
        self.assertEqual(event["tool"], "search_codebase")
        self.assertEqual(event["topic"], "draw-things")
        self.assertEqual(event["telemetry"]["diagnosis"], "ranking_surfaces_routing_signal")

    def test_append_routing_telemetry_event_trims_to_recent_max_events(self):
        telemetry = {
            "query_class": "implementation_explanation",
            "diagnosis": "ranking_surfaces_routing_signal",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "routing.ndjson")
            with mock.patch.dict(
                os.environ,
                {
                    "LM_PROXY_ROUTING_TELEMETRY": "1",
                    "LM_PROXY_ROUTING_TELEMETRY_PATH": target,
                    "LM_PROXY_ROUTING_TELEMETRY_MAX_EVENTS": "2",
                },
                clear=False,
            ):
                module.append_routing_telemetry_event(
                    dict(telemetry, ordinal=1),
                    query="q1",
                    tool="search_codebase",
                )
                module.append_routing_telemetry_event(
                    dict(telemetry, ordinal=2),
                    query="q2",
                    tool="search_codebase",
                )
                module.append_routing_telemetry_event(
                    dict(telemetry, ordinal=3),
                    query="q3",
                    tool="search_codebase",
                )
            with open(target, "r", encoding="utf-8") as fh:
                events = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(events), 2)
        self.assertEqual([event["query"] for event in events], ["q2", "q3"])

    def test_context_payload_uses_source_url_when_file_path_missing(self):
        payload = json.loads(
            module._context_payload(
                [
                    {
                        "source_url": "https://neo4j.com/docs/python-manual/current/transactions/",
                        "metadata": {"domain": "neo4j.com"},
                    }
                ]
            )
        )
        self.assertEqual(
            payload[0]["file_path"],
            "https://neo4j.com/docs/python-manual/current/transactions/",
        )

    def test_duplicate_telemetry_enabled_defaults_on_and_can_disable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(module.duplicate_telemetry_enabled())
        with mock.patch.dict(os.environ, {"LM_PROXY_DUPLICATE_TELEMETRY": "0"}, clear=False):
            self.assertFalse(module.duplicate_telemetry_enabled())

    def test_duplicate_helpers_fail_open(self):
        rows = [
            {"file_path": "src/a.py", "project_id": "p", "rrf": 1.0, "content": "same-a"},
            {"file_path": "src/b.py", "project_id": "p", "rrf": 0.9, "content": "same-b"},
        ]
        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": None}):
            analysis = module.analyze_near_duplicate_results(rows)
            reranked = module.rerank_diverse_results(rows)
            collapsed = module.collapse_near_duplicate_results(rows)
        self.assertEqual(analysis["keep_indices"], [0, 1])
        self.assertEqual(reranked["keep_indices"], [0, 1])
        self.assertEqual(collapsed, rows)

    def test_render_results_formats_single_project_output(self):
        rows = [
            {
                "file_path": "src/a.py",
                "project_id": "proj",
                "rrf": 1.2345,
                "content": "def run():\n    pass",
                "_meta": {"language": "python"},
            }
        ]
        lines = module.render_results(
            rows,
            query="run",
            k=5,
            multi=False,
            pid_to_name={"proj": "repo"},
            include_metadata=True,
        )
        output = "\n".join(lines)
        self.assertIn("--- src/a.py ---", output)
        self.assertIn("lang=python", output)
        self.assertIn("def run()", output)

    def test_cargo_helpers_attach_and_filter(self):
        rows = [
            {"file_path": "crates/api/src/lib.rs", "project_id": "proj", "rrf": 1.0, "content": "fn run() {}", "_meta": {}},
            {"file_path": "crates/core/src/lib.rs", "project_id": "proj", "rrf": 0.9, "content": "fn serve() {}", "_meta": {}},
        ]
        crate_rows = [
            {"crate": "api", "crate_name": "api", "manifest_path": "crates/api/Cargo.toml"},
            {"crate": "core", "crate_name": "core_lib", "manifest_path": "crates/core/Cargo.toml"},
        ]
        module.attach_cargo_crate_meta(rows, crate_rows)
        self.assertEqual(rows[0]["_meta"]["cargo_crate"], "api")
        self.assertEqual(rows[1]["_meta"]["cargo_crate_name"], "core_lib")
        filtered = module.filter_by_cargo_crate(rows, "core")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["file_path"], "crates/core/src/lib.rs")
        self.assertTrue(any("crate=api" in line for line in module.format_meta(rows[0]["_meta"])))

    def test_is_doc_like_path_flags_docs_and_markdown(self):
        self.assertTrue(module.is_doc_like_path("docs/guide.md"))
        self.assertTrue(module.is_doc_like_path("QUICKBOOKS_INTEGRATION_GUIDE.md"))
        self.assertTrue(module.is_doc_like_path("src/guide.txt", {"docs_surface"}))
        self.assertFalse(module.is_doc_like_path("src/services/QuickBooksService.ts"))

    def test_implementation_query_intent_detects_code_seeking_queries(self):
        self.assertTrue(module.implementation_query_intent("QuickBooks accounting sync attempts and tenant credit application"))
        self.assertTrue(module.implementation_query_intent("where is the route handler for tenant credit"))
        self.assertTrue(module.implementation_query_intent("how are Neo4j writes retried in ts-pack indexing"))
        self.assertTrue(module.implementation_query_intent("how are Rust use imports resolved to local files in ts-pack-index"))
        self.assertTrue(module.implementation_query_intent("how does process(source, config) work in tree-sitter-language-pack"))
        self.assertTrue(module.implementation_query_intent("how does graph finalization build file graph links for GDS"))
        self.assertEqual(
            module.implementation_query_class("how does process(source, config) work in tree-sitter-language-pack"),
            "implementation_explanation",
        )
        self.assertEqual(
            module.implementation_query_class("where is process defined"),
            "api_definition_lookup",
        )
        self.assertEqual(
            module.implementation_query_class("where is it called"),
            "usage_lookup",
        )
        self.assertEqual(
            module.implementation_query_class("where is process called in tree-sitter-language-pack"),
            "usage_lookup",
        )
        self.assertEqual(module.implementation_query_class("process(source, config)"), "symbol_lookup")
        self.assertTrue(module.query_class_prefers_definitions("symbol_lookup"))
        self.assertTrue(module.query_class_prefers_definitions("api_definition_lookup"))
        self.assertTrue(module.query_class_prefers_definitions("implementation_explanation"))
        self.assertFalse(module.query_class_prefers_definitions("usage_lookup"))

    def test_is_low_signal_parser_data_path_flags_grammar_payloads(self):
        self.assertTrue(module.is_low_signal_parser_data_path("node-types/ocaml/ocaml-grammar.json"))
        self.assertTrue(module.is_low_signal_parser_data_path("grammars/python/grammar.json"))
        self.assertTrue(
            module.is_low_signal_parser_data_path(
                "crates/ts-pack-python/python/tree_sitter_language_pack/_semantic_payload.py"
            )
        )
        self.assertTrue(
            module.is_low_signal_parser_data_path(
                "crates/ts-pack-python/python/tree_sitter_language_pack/__init__.pyi"
            )
        )
        self.assertTrue(module.is_low_signal_parser_data_path("crates/ts-pack-node/index.d.ts"))
        self.assertTrue(
            module.is_low_signal_parser_data_path(
                "crates/ts-pack-java/src/main/java/io/github/treesitter/languagepack/ImportInfo.java"
            )
        )
        self.assertTrue(module.is_low_signal_parser_data_path("packages/go/v1/types.go"))
        self.assertTrue(module.is_low_signal_parser_data_path("packages/php/src/ProcessConfig.php"))
        self.assertFalse(module.is_low_signal_parser_data_path("repo_analyzer/parser.py"))
        self.assertFalse(module.implementation_query_intent("overview of the system"))

    def test_is_low_signal_binding_surface_path_flags_wrappers(self):
        self.assertTrue(
            module.is_low_signal_binding_surface_path(
                "crates/ts-pack-java/src/main/java/io/github/treesitter/languagepack/TsPackRegistry.java"
            )
        )
        self.assertTrue(module.is_low_signal_binding_surface_path("packages/csharp/TreeSitterLanguagePack/Models.cs"))
        self.assertTrue(module.is_low_signal_binding_surface_path("packages/go/v1/types.go"))
        self.assertFalse(module.is_low_signal_binding_surface_path("crates/ts-pack-core/src/lib.rs"))
        self.assertTrue(module.is_usage_heavy_path("crates/ts-pack-cli/src/main.rs"))
        self.assertTrue(module.is_usage_heavy_path("e2e/ruby/spec/process_spec.rb"))
        self.assertFalse(module.is_usage_heavy_path("crates/ts-pack-core/src/lib.rs"))
        self.assertTrue(module.is_low_signal_support_path("scripts/clone_vendors.py"))
        self.assertTrue(module.is_low_signal_support_path("tools/dev.py"))
        self.assertFalse(module.is_low_signal_support_path("crates/ts-pack-core/src/lib.rs"))
        self.assertFalse(
            module.is_low_signal_support_path(
                "tools/brain/search/semantic.py",
                {"support_surface", "implementation_surface"},
            )
        )
        self.assertTrue(
            module.is_low_signal_support_path(
                "tools/dev.py",
                {"support_surface"},
            )
        )

    def test_implementation_rank_tuple_prefers_code_over_docs_and_parser_data(self):
        rows = [
            {"file_path": "node-types/ocaml/ocaml-grammar.json", "low_signal_parser_data": True, "doc_like": False, "rank_score": 0.9},
            {"file_path": "docs/parser.md", "low_signal_parser_data": False, "doc_like": True, "rank_score": 0.8},
            {"file_path": "repo_analyzer/parser.py", "low_signal_parser_data": False, "doc_like": False, "rank_score": 0.7},
        ]
        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "repo_analyzer/parser.py")
        self.assertEqual(rows[-1]["file_path"], "node-types/ocaml/ocaml-grammar.json")

        rows = [
            {
                "file_path": "packages/csharp/TreeSitterLanguagePack/Models.cs",
                "low_signal_parser_data": False,
                "low_signal_binding_surface": True,
                "doc_like": False,
                "rank_score": 0.9,
            },
            {
                "file_path": "crates/ts-pack-core/src/lib.rs",
                "low_signal_parser_data": False,
                "low_signal_binding_surface": False,
                "doc_like": False,
                "rank_score": 0.8,
            },
        ]
        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "crates/ts-pack-core/src/lib.rs")

        rows = [
            {
                "file_path": "crates/ts-pack-cli/src/main.rs",
                "low_signal_parser_data": False,
                "low_signal_binding_surface": False,
                "low_signal_support_path": False,
                "doc_like": False,
                "implementation_usage_heavy_penalty": True,
                "implementation_definition_hit": 0,
                "implementation_api_entrypoint_hit": 0,
                "implementation_symbol_hit": 1,
                "rank_score": 0.9,
            },
            {
                "file_path": "crates/ts-pack-core/src/lib.rs",
                "low_signal_parser_data": False,
                "low_signal_binding_surface": False,
                "low_signal_support_path": False,
                "doc_like": False,
                "implementation_usage_heavy_penalty": False,
                "implementation_definition_hit": 1,
                "implementation_api_entrypoint_hit": 1,
                "implementation_symbol_hit": 1,
                "rank_score": 0.8,
            },
        ]
        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "crates/ts-pack-core/src/lib.rs")

        rows = [
            {
                "file_path": "scripts/clone_vendors.py",
                "low_signal_parser_data": False,
                "low_signal_binding_surface": False,
                "low_signal_support_path": True,
                "doc_like": False,
                "implementation_usage_heavy_penalty": False,
                "implementation_definition_hit": 1,
                "implementation_api_entrypoint_hit": 0,
                "implementation_symbol_hit": 1,
                "rank_score": 0.95,
            },
            {
                "file_path": "crates/ts-pack-core/src/lib.rs",
                "low_signal_parser_data": False,
                "low_signal_binding_surface": False,
                "low_signal_support_path": False,
                "doc_like": False,
                "implementation_usage_heavy_penalty": False,
                "implementation_definition_hit": 1,
                "implementation_api_entrypoint_hit": 1,
                "implementation_symbol_hit": 1,
                "rank_score": 0.8,
            },
        ]
        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "crates/ts-pack-core/src/lib.rs")

    def test_implementation_symbol_hit_prefers_exact_file_symbols(self):
        meta = {"file_symbols": ["process", "process_with_tree", "parse_source"]}
        self.assertGreaterEqual(
            module.implementation_symbol_hit(meta, "how does process(source, config) work"),
            1,
        )
        self.assertEqual(module.implementation_symbol_hit(meta, "overview of supported languages"), 0)

        rows = [
            {
                "file_path": "src/usage.rs",
                "low_signal_parser_data": False,
                "doc_like": False,
                "rank_score": 0.8,
                "implementation_symbol_hit": 0,
            },
            {
                "file_path": "src/lib.rs",
                "low_signal_parser_data": False,
                "doc_like": False,
                "rank_score": 0.7,
                "implementation_symbol_hit": 1,
            },
        ]
        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "src/lib.rs")

    def test_implementation_definition_and_api_entrypoint_hits(self):
        content = "pub fn process(source: &str, config: &ProcessConfig) -> Result<ProcessResult, Error> {"
        self.assertGreaterEqual(
            module.implementation_definition_hit(
                content,
                "how does process(source, config) work in tree-sitter-language-pack",
            ),
            1,
        )
        self.assertEqual(
            module.implementation_api_entrypoint_hit(
                "crates/ts-pack-core/src/lib.rs",
                1,
                current_contract_meta(file_roles=["api_surface", "library_facade_surface"]),
            ),
            1,
        )
        self.assertEqual(
            module.implementation_api_entrypoint_hit(
                "crates/ts-pack-cli/src/main.rs",
                1,
                current_contract_meta(file_roles=["runtime_entrypoint_surface"]),
            ),
            1,
        )
        self.assertEqual(
            module.implementation_api_entrypoint_hit(
                "src/app/OwnerController.java",
                1,
                current_contract_meta(file_roles=["api_surface"]),
            ),
            1,
        )

    def test_definition_kind_query_matches_literal_enum_declaration_without_symbol_metadata(self):
        content = """
        impl From<ColorChoice> for anstream::ColorChoice {
            fn from(value: ColorChoice) -> Self { Self::Auto }
        }

        #[derive(Subcommand)]
        pub enum Commands {
            Auth(AuthNamespace),
        }
        """
        query = "where is the uv command enum defined"
        self.assertIn("commands", module.implementation_query_definition_subject_identifiers(query))
        self.assertGreaterEqual(module.implementation_definition_hit(content, query), 1)

    def test_command_surface_metadata_promotes_rust_command_enum_definition(self):
        query = "where is the uv command enum defined"
        rows = [
            {
                "file_path": "docs/reference/cli.md",
                "project_id": "bench",
                "rrf": 0.94,
                "rank_score": 0.94,
                "content": "The uv Commands enum controls subcommands.",
                "metadata": current_contract_meta(
                    {
                        "chunk_role": "definition",
                        "declared_symbols": ["Commands"],
                    },
                    file_roles=["docs_surface"],
                ),
            },
            {
                "file_path": "crates/uv-cli/src/lib.rs",
                "project_id": "bench",
                "rrf": 0.72,
                "rank_score": 0.72,
                "content": "#[derive(Subcommand)]\npub enum Commands {\n    Auth(AuthNamespace),\n}",
                "metadata": current_contract_meta(
                    {
                        "chunk_role": "definition",
                        "declared_symbols": ["Commands"],
                        "file_symbols": ["Commands"],
                        "declared_symbol_roles": {"Commands": ["command_enum"]},
                    },
                    file_roles=["command_surface", "library_facade_surface"],
                ),
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(enriched[0]["file_path"], "crates/uv-cli/src/lib.rs")
        self.assertGreater(enriched[0]["implementation_command_definition_priority"], 0)

    def test_request_routing_query_infers_controller_filename_hint(self):
        hints = module.implementation_inferred_filename_hints(
            "where is owner request routing implemented in spring petclinic"
        )
        self.assertIn("ownercontroller.java", hints)

    def test_request_routing_controller_surface_promotes_public_api_role(self):
        row = {
            "file_path": "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
            "project_id": "bench",
            "rrf": 0.0,
            "rank_score": 0.0,
            "content": "@Controller\nclass OwnerController {}",
            "metadata": current_contract_meta(
                {
                    "chunk_role": "definition",
                    "file_symbols": ["OwnerController"],
                    "declared_symbols": ["OwnerController"],
                },
                file_roles=["api_surface", "controller_surface", "example_surface"],
            ),
        }
        row["_meta"] = row["metadata"]
        row["meta_score"] = module.meta_score(row["_meta"])
        module.enrich_implementation_result(
            row,
            query="where is owner request routing implemented in spring petclinic",
            query_class=module.implementation_query_class(
                "where is owner request routing implemented in spring petclinic"
            ),
            base_score=0.0,
            meta_boost=0.0,
        )
        self.assertEqual(row["implementation_role"], "public_api_definition")

    def test_implementation_entrypoint_boost_prefers_runtime_main_over_build_script(self):
        rows = [
            {
                "file_path": "packages/desktop/src-tauri/build.rs",
                "project_id": "bench",
                "rrf": 0.91,
                "rank_score": 0.91,
                "content": "fn main() { tauri_build::build() }",
                "metadata": {
                    "file_symbols": ["main"],
                    "node_types": ["function_item"],
                    "chunk_role": "definition",
                },
            },
            {
                "file_path": "packages/desktop/src-tauri/src/main.rs",
                "project_id": "bench",
                "rrf": 0.86,
                "rank_score": 0.86,
                "content": "fn main() { app::bootstrap() }",
                "metadata": {
                    "file_symbols": ["main"],
                    "node_types": ["function_item"],
                    "chunk_role": "definition",
                },
            },
        ]
        for row in rows:
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query="where is packages/desktop/src-tauri main entrypoint defined",
                query_class=module.implementation_query_class(
                    "where is packages/desktop/src-tauri main entrypoint defined"
                ),
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )

        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "packages/desktop/src-tauri/src/main.rs")

    def test_runtime_entrypoint_role_boosts_main_without_path_guess(self):
        self.assertEqual(
            module.implementation_runtime_main_entrypoint_hit_with_meta(
                "apps/cli/bootstrap.py",
                "where is the src/main entrypoint implemented",
                {"file_roles": ["runtime_entrypoint_surface"]},
            ),
            1,
        )

    def test_implementation_api_entrypoint_hit_uses_roles_before_path_fallback(self):
        self.assertEqual(
            module.implementation_api_entrypoint_hit(
                "apps/cli/bootstrap.py",
                1,
                {"file_roles": ["runtime_entrypoint_surface"]},
            ),
            1,
        )
        self.assertEqual(
            module.implementation_api_entrypoint_hit(
                "src/main.rs",
                1,
                {"file_roles": []},
            ),
            0,
        )

    def test_implementation_export_hit_skips_path_bonus_when_file_roles_are_present(self):
        content = "from .unified_indexer import process_repository_indexing\n__all__ = ['process_repository_indexing']"
        self.assertEqual(
            module.implementation_export_hit(
                content,
                "indexer/__init__.py",
                {"file_roles": []},
            ),
            0,
        )
        self.assertGreaterEqual(
            module.implementation_export_hit(
                content,
                "indexer/__init__.py",
                {"file_roles": ["library_facade_surface"]},
            ),
            1,
        )

    def test_library_facade_role_marks_facade_surface(self):
        hit = module.implementation_facade_surface_hit(
            "pkg/api.py",
            {"file_roles": ["library_facade_surface"]},
            symbol_hit=1,
            declared_symbol_hit=0,
            definition_hit=0,
            signature_hit=0,
            export_hit=0,
            api_context_hit=0,
        )
        self.assertEqual(hit, 1)

    def test_facade_surface_hit_skips_inference_when_file_roles_are_present(self):
        hit = module.implementation_facade_surface_hit(
            "pkg/api.py",
            {"file_roles": [], "node_types": ["module"], "chunk_role": "definition"},
            symbol_hit=1,
            declared_symbol_hit=0,
            definition_hit=0,
            signature_hit=0,
            export_hit=1,
            api_context_hit=1,
        )
        self.assertEqual(hit, 0)

    def test_implementation_query_path_hints_extract_repo_path(self):
        self.assertEqual(
            module.implementation_query_path_hints(
                "where is packages/desktop/src-tauri main entrypoint defined"
            ),
            ["packages/desktop/src-tauri"],
        )
        self.assertEqual(
            module.implementation_query_class(
                "where is packages/desktop/src-tauri main entrypoint defined"
            ),
            "implementation_search",
        )

    def test_implementation_query_relaxes_dir_cap_for_path_heavy_queries(self):
        self.assertTrue(
            module.implementation_query_relaxes_dir_cap(
                "where is packages/desktop/src-tauri main entrypoint defined"
            )
        )
        self.assertFalse(module.implementation_query_relaxes_dir_cap("how does process(source, config) work"))
        self.assertGreater(
            module.implementation_path_hint_hit(
                "packages/desktop/src-tauri/src/main.rs",
                path_hints=["packages/desktop/src-tauri"],
            ),
            0,
        )

    def test_path_hint_bonus_prefers_explicit_entrypoint_path_over_noisy_scripts(self):
        rows = [
            {
                "file_path": "script/github/close-issues.ts",
                "content": "async function main() { process.exit(1) }",
                "metadata": {"node_types": ["function_declaration"], "chunk_role": "script_support"},
                "rrf": 0.0,
            },
            {
                "file_path": "packages/desktop/src-tauri/src/main.rs",
                "content": "fn main() { configure_display_backend(); run() }",
                "metadata": {"node_types": ["function_item"], "file_symbols": ["main"], "chunk_role": "definition"},
                "rrf": 0.0,
            },
        ]
        enriched = []
        query = "where is packages/desktop/src-tauri main entrypoint defined"
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(enriched[0]["file_path"], "packages/desktop/src-tauri/src/main.rs")

    def test_dispatcher_bonus_prefers_infer_model_over_profile_helper(self):
        query = "where is model inference selected"
        rows = [
            {
                "file_path": "pydantic_ai_slim/pydantic_ai/profiles/deepseek.py",
                "content": "def deepseek_model_profile(model_name: str) -> ModelProfile | None:",
                "metadata": {
                    "node_types": ["function_definition"],
                    "file_symbols": ["deepseek_model_profile"],
                    "declared_symbols": ["deepseek_model_profile"],
                    "chunk_role": "definition",
                },
                "rrf": 0.95,
            },
            {
                "file_path": "pydantic_ai_slim/pydantic_ai/models/__init__.py",
                "content": "def infer_model(model: Model | KnownModelName) -> Model:",
                "metadata": {
                    "node_types": ["function_definition", "module"],
                    "file_symbols": ["infer_model"],
                    "declared_symbols": ["infer_model"],
                    "chunk_role": "definition",
                },
                "rrf": 0.75,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(
            enriched[0]["file_path"],
            "pydantic_ai_slim/pydantic_ai/models/__init__.py",
        )

    def test_dispatcher_bonus_prefers_canonical_model_dispatcher_over_embedding_facade(self):
        query = "where is model inference selected"
        rows = [
            {
                "file_path": "pydantic_ai_slim/pydantic_ai/embeddings/__init__.py",
                "content": "class Embedder:\n    def __init__(self, model: EmbeddingModel | KnownEmbeddingModelName | str):",
                "metadata": current_contract_meta(
                    {
                        "node_types": ["class_definition"],
                        "file_symbols": ["Embedder", "infer_embedding_model"],
                        "declared_symbols": ["Embedder"],
                        "chunk_role": "definition",
                    },
                    file_roles=["library_facade_surface", "dispatcher_surface", "model_dispatcher_surface"],
                ),
                "rrf": 0.95,
            },
            {
                "file_path": "pydantic_ai_slim/pydantic_ai/models/__init__.py",
                "content": "def infer_model(model: Model | KnownModelName) -> Model:",
                "metadata": current_contract_meta(
                    {
                        "node_types": ["function_definition", "module"],
                        "file_symbols": ["infer_model"],
                        "declared_symbols": ["infer_model"],
                        "declared_symbol_roles": {
                            "infer_model": ["canonical_dispatcher", "dispatcher", "model_selector"]
                        },
                        "chunk_role": "definition",
                    },
                    file_roles=["library_facade_surface", "dispatcher_surface", "model_dispatcher_surface"],
                ),
                "rrf": 0.75,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(
            enriched[0]["file_path"],
            "pydantic_ai_slim/pydantic_ai/models/__init__.py",
        )

    def test_dispatcher_bonus_prefers_callable_dispatcher_over_property_surface(self):
        query = "where is model inference selected"
        rows = [
            {
                "file_path": "Libraries/SwiftDiffusion/Sources/TeaCache/TeaCache.swift",
                "content": "public let modelIdentifier: String = \"flux\"",
                "metadata": {
                    "node_types": ["property_declaration"],
                    "file_symbols": ["modelIdentifier"],
                    "declared_symbols": ["modelIdentifier"],
                    "chunk_role": "definition",
                },
                "rrf": 0.96,
            },
            {
                "file_path": "Libraries/ModelOp/Sources/ModelImporter.swift",
                "content": "func inferModelSpecification(from file: String) -> ModelSpecification { }",
                "metadata": {
                    "node_types": ["function_declaration"],
                    "file_symbols": ["inferModelSpecification"],
                    "declared_symbols": ["inferModelSpecification"],
                    "chunk_role": "definition",
                },
                "rrf": 0.72,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(
            enriched[0]["file_path"],
            "Libraries/ModelOp/Sources/ModelImporter.swift",
        )

    def test_routing_query_demotes_generated_grpc_surface_below_service_impl(self):
        query = "how does gRPC server request routing work"
        self.assertEqual(module.implementation_query_class(query), "implementation_explanation")
        rows = [
            {
                "file_path": "Libraries/GRPC/Models/Sources/controlPanel/controlPanel.grpc.swift",
                "content": "public func registerMethods(with server: GRPCServer) { }",
                "metadata": {
                    "node_types": ["function_declaration"],
                    "file_symbols": ["registerMethods"],
                    "declared_symbols": ["registerMethods"],
                    "chunk_role": "definition",
                    "file_roles": ["generated_surface"],
                    "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
                },
                "rrf": 0.94,
            },
            {
                "file_path": "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
                "content": "func handleGenerateImage(_ request: GenerateImageRequest) async throws -> GenerateImageResponse { }",
                "metadata": {
                    "node_types": ["method_declaration"],
                    "file_symbols": ["handleGenerateImage"],
                    "declared_symbols": ["handleGenerateImage"],
                    "chunk_role": "definition",
                    "file_roles": [],
                    "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
                },
                "rrf": 0.78,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(
            enriched[0]["file_path"],
            "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
        )
        self.assertEqual(
            enriched[0]["implementation_rank_components"]["routing_bonus"],
            0.0,
        )
        self.assertEqual(
            enriched[0]["implementation_rank_components"]["request_handler_bonus"],
            0.0,
        )
        self.assertGreater(enriched[0]["implementation_routing_priority"], 0)
        self.assertGreater(enriched[0]["implementation_request_handler_priority"], 0)
        self.assertEqual(enriched[1]["implementation_role"], "generated_surface")
        self.assertGreater(
            enriched[1]["implementation_rank_components"]["generated_surface_penalty"],
            0.1,
        )

    def test_routing_query_demotes_server_infrastructure_below_service_handler(self):
        query = "how does gRPC server request routing work"
        rows = [
            {
                "file_path": "Libraries/GRPC/Server/Sources/GRPCServiceBrowser.swift",
                "content": "public func netServiceDidResolveAddress(_ sender: NetService) { }",
                "metadata": {
                    "node_types": ["method_declaration"],
                    "file_symbols": ["netServiceDidResolveAddress"],
                    "declared_symbols": ["netServiceDidResolveAddress"],
                    "chunk_role": "definition",
                },
                "rrf": 0.95,
            },
            {
                "file_path": "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
                "content": "private func handleGenerateImage(request: ImageGenerationRequest) async throws { }",
                "metadata": {
                    "node_types": ["method_declaration"],
                    "file_symbols": ["handleGenerateImage"],
                    "declared_symbols": ["handleGenerateImage"],
                    "chunk_role": "definition",
                },
                "rrf": 0.72,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(
            enriched[0]["file_path"],
            "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
        )
        self.assertGreater(
            enriched[1]["implementation_rank_components"]["server_infra_penalty"],
            0.0,
        )

    def test_request_handling_query_prefers_gin_handler_over_support_file(self):
        query = "where are incoming http requests handled in gin"
        rows = [
            {
                "file_path": "ginS/gins.go",
                "content": "func New() *Server { }",
                "metadata": {
                    "node_types": ["function_declaration"],
                    "file_symbols": ["New"],
                    "declared_symbols": ["New"],
                    "chunk_role": "definition",
                },
                "rrf": 0.94,
            },
            {
                "file_path": "gin.go",
                "content": "func (engine *Engine) handleHTTPRequest(c *Context) { }",
                "metadata": {
                    "node_types": ["method_declaration"],
                    "file_symbols": ["handleHTTPRequest"],
                    "declared_symbols": ["handleHTTPRequest"],
                    "chunk_role": "definition",
                },
                "rrf": 0.72,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(enriched[0]["file_path"], "gin.go")
        self.assertGreater(enriched[0]["implementation_routing_priority"], 0)
        self.assertGreater(enriched[0]["implementation_request_handler_priority"], 0)

    def test_request_routing_query_prefers_spring_controller_over_template(self):
        query = "where is owner request routing implemented in spring petclinic"
        rows = [
            {
                "file_path": "src/main/resources/templates/owners/createOrUpdateOwnerForm.html",
                "content": "<form action=\"#\">",
                "metadata": {
                    "node_types": ["element"],
                    "file_symbols": [],
                    "declared_symbols": [],
                    "chunk_role": "definition",
                },
                "rrf": 0.96,
            },
            {
                "file_path": "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
                "content": "@GetMapping(\"/owners\") public String processFindForm(@RequestParam(defaultValue = \"1\") int page, Owner owner, BindingResult result) { }",
                "metadata": {
                    "node_types": ["method_declaration", "class_declaration"],
                    "file_symbols": ["OwnerController", "processFindForm"],
                    "declared_symbols": ["OwnerController", "processFindForm"],
                    "chunk_role": "definition",
                },
                "rrf": 0.74,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(
            enriched[0]["file_path"],
            "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
        )
        self.assertGreater(enriched[0]["implementation_routing_priority"], 0)
        self.assertGreater(enriched[0]["implementation_request_handler_priority"], 0)
        self.assertGreater(enriched[0]["implementation_controller_entity_hit"], 0)

    def test_request_routing_priority_uses_semantic_routing_roles(self):
        query = "how does gRPC server request routing work"
        row = {
            "file_path": "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
            "content": "func routeImageRequest() {}",
            "metadata": current_contract_meta(
                {
                    "node_types": ["function_declaration", "class_declaration"],
                    "file_symbols": ["ImageGenerationServiceImpl", "routeImageRequest"],
                    "declared_symbols": ["ImageGenerationServiceImpl", "routeImageRequest"],
                    "declared_symbol_roles": {
                        "routeImageRequest": ["request_handler", "route_definition"],
                    },
                    "chunk_role": "definition",
                },
                file_roles=["request_handler_surface", "route_definition_surface"],
            ),
            "rrf": 0.6,
        }
        row["_meta"] = row["metadata"]
        row["meta_score"] = module.meta_score(row["_meta"])
        module.enrich_implementation_result(
            row,
            query=query,
            query_class=module.implementation_query_class(query),
            base_score=float(row["rrf"]),
            meta_boost=0.0,
        )
        self.assertGreaterEqual(row["implementation_routing_priority"], 3)
        self.assertGreaterEqual(row["implementation_request_handler_priority"], 3)

    def test_request_routing_path_fallback_requires_missing_file_roles(self):
        query = "how does gRPC server request routing work"
        row = {
            "file_path": "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
            "content": "func helper() {}",
            "metadata": current_contract_meta(
                {
                    "node_types": ["function_declaration"],
                    "file_symbols": ["helper"],
                    "declared_symbols": ["helper"],
                    "declared_symbol_roles": {},
                    "chunk_role": "definition",
                },
                file_roles=[],
            ),
            "rrf": 0.6,
        }
        row["_meta"] = row["metadata"]
        row["meta_score"] = module.meta_score(row["_meta"])
        module.enrich_implementation_result(
            row,
            query=query,
            query_class=module.implementation_query_class(query),
            base_score=float(row["rrf"]),
            meta_boost=0.0,
        )
        self.assertEqual(row["implementation_routing_priority"], 0)
        self.assertEqual(row["implementation_request_handler_priority"], 0)

    def test_request_routing_path_fallback_still_works_for_legacy_missing_roles(self):
        query = "how does gRPC server request routing work"
        row = {
            "file_path": "Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift",
            "content": "func helper() {}",
            "metadata": {
                "node_types": ["function_declaration"],
                "file_symbols": ["helper"],
                "declared_symbols": ["helper"],
                "chunk_role": "definition",
            },
            "rrf": 0.6,
        }
        row["_meta"] = row["metadata"]
        row["meta_score"] = module.meta_score(row["_meta"])
        module.enrich_implementation_result(
            row,
            query=query,
            query_class=module.implementation_query_class(query),
            base_score=float(row["rrf"]),
            meta_boost=0.0,
        )
        self.assertGreater(row["implementation_routing_priority"], 0)
        self.assertGreater(row["implementation_request_handler_priority"], 0)

    def test_request_routing_query_prefers_spring_controller_over_static_asset(self):
        query = "where is owner request routing implemented in spring petclinic"
        rows = [
            {
                "file_path": "src/main/resources/static/resources/css/petclinic.css",
                "content": ".owners-form { color: #333; }",
                "metadata": {
                    "node_types": ["stylesheet"],
                    "file_symbols": [],
                    "declared_symbols": [],
                    "chunk_role": "definition",
                },
                "rrf": 0.96,
            },
            {
                "file_path": "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
                "content": "@GetMapping(\"/owners\") public String processFindForm(@RequestParam(defaultValue = \"1\") int page, Owner owner, BindingResult result) { }",
                "metadata": {
                    "node_types": ["method_declaration", "class_declaration"],
                    "file_symbols": ["OwnerController", "processFindForm"],
                    "declared_symbols": ["OwnerController", "processFindForm"],
                    "chunk_role": "definition",
                },
                "rrf": 0.74,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(
            enriched[0]["file_path"],
            "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
        )
        self.assertTrue(enriched[1]["low_signal_support_path"])
        self.assertGreater(
            enriched[1]["implementation_rank_components"]["support_path_penalty"],
            0.0,
        )

    def test_request_routing_query_prefers_spring_controller_over_config_surface(self):
        query = "where is owner request routing implemented in spring petclinic"
        rows = [
            {
                "file_path": "k8s/petclinic.yml",
                "content": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: petclinic\n",
                "metadata": {
                    "node_types": ["document"],
                    "file_symbols": [],
                    "declared_symbols": [],
                    "chunk_role": "definition",
                    "file_roles": ["config_surface"],
                },
                "rrf": 0.96,
            },
            {
                "file_path": "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
                "content": "@GetMapping(\"/owners\") public String processFindForm(@RequestParam(defaultValue = \"1\") int page, Owner owner, BindingResult result) { }",
                "metadata": {
                    "node_types": ["method_declaration", "class_declaration"],
                    "file_symbols": ["OwnerController", "processFindForm"],
                    "declared_symbols": ["OwnerController", "processFindForm"],
                    "chunk_role": "definition",
                },
                "rrf": 0.74,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(
            enriched[0]["file_path"],
            "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
        )
        self.assertFalse(enriched[0]["implementation_usage_heavy_penalty"])
        self.assertGreater(
            enriched[1]["implementation_rank_components"]["support_path_penalty"],
            0.0,
        )

    def test_request_routing_query_prefers_owner_controller_over_sibling_controller(self):
        query = "where is owner request routing implemented in spring petclinic"
        rows = [
            {
                "file_path": "src/main/java/org/springframework/samples/petclinic/owner/VisitController.java",
                "content": "@GetMapping(\"/owners/{ownerId}/visits/new\") public String initNewVisitForm() { }",
                "metadata": {
                    "node_types": ["method_declaration", "class_declaration"],
                    "file_symbols": ["VisitController", "initNewVisitForm"],
                    "declared_symbols": ["VisitController", "initNewVisitForm"],
                    "chunk_role": "definition",
                },
                "rrf": 0.91,
            },
            {
                "file_path": "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
                "content": "@GetMapping(\"/owners\") public String processFindForm(@RequestParam(defaultValue = \"1\") int page, Owner owner, BindingResult result) { }",
                "metadata": {
                    "node_types": ["method_declaration", "class_declaration"],
                    "file_symbols": ["OwnerController", "processFindForm"],
                    "declared_symbols": ["OwnerController", "processFindForm"],
                    "chunk_role": "definition",
                },
                "rrf": 0.74,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(
            enriched[0]["file_path"],
            "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
        )
        self.assertGreater(enriched[0]["implementation_controller_entity_hit"], 0)
        self.assertEqual(enriched[1]["implementation_controller_entity_hit"], 0)

    def test_generated_surface_query_keeps_generated_penalty_off_when_explicitly_requested(self):
        query = "where is the generated grpc stub for request routing"
        row = {
            "file_path": "Libraries/GRPC/Models/Sources/controlPanel/controlPanel.grpc.swift",
            "content": "public func registerMethods(with router: inout GRPCCore.RPCRouter<Transport>) { }",
            "metadata": {
                "node_types": ["function_declaration"],
                "file_symbols": ["registerMethods"],
                "declared_symbols": ["registerMethods"],
                "chunk_role": "definition",
            },
            "rrf": 0.8,
        }
        row["_meta"] = row.get("metadata", {})
        row["meta_score"] = module.meta_score(row["_meta"])
        module.enrich_implementation_result(
            row,
            query=query,
            query_class=module.implementation_query_class(query),
            base_score=float(row.get("rrf", 0.0) or 0.0),
            meta_boost=0.0,
        )
        self.assertEqual(
            row["implementation_rank_components"]["generated_surface_penalty"],
            0.0,
        )

    def test_build_implementation_ranking_trace_surfaces_node_type_and_role(self):
        rows = [
            {
                "file_path": "crates/ts-pack-cli/src/main.rs",
                "project_id": "bench",
                "rrf": 0.92,
                "rank_score": 0.92,
                "content": "Commands::Process { let result = process(&source, &config)?; }",
                "metadata": {
                    "file_symbols": ["main"],
                    "context_path": ["Cli", "Process"],
                    "node_types": ["match_expression"],
                },
            },
            {
                "file_path": "crates/ts-pack-core/src/lib.rs",
                "project_id": "bench",
                "rrf": 0.81,
                "rank_score": 0.81,
                "content": "pub fn process(source: &str, config: &ProcessConfig) -> Result<ProcessResult, Error> { REGISTRY.process(source, config) }",
                "metadata": {
                    "file_symbols": ["process", "process_with_tree"],
                    "context_path": ["Core", "API"],
                    "node_types": ["function_item"],
                },
            },
        ]
        trace = module.build_implementation_ranking_trace(
            rows,
            "how does process(source, config) work in tree-sitter-language-pack",
        )
        self.assertEqual(trace["query_class"], "implementation_explanation")
        self.assertEqual(trace["rows"][0]["file_path"], "crates/ts-pack-core/src/lib.rs")
        self.assertEqual(trace["rows"][0]["role"], "public_api_definition")
        self.assertIn("function_item", trace["rows"][0]["node_types"])
        self.assertEqual(trace["rows"][1]["role"], "usage_callsite")

    def test_definition_lookup_prefers_true_definition_over_reexport_surface(self):
        query = "where is process_repository_indexing defined"
        rows = [
            {
                "file_path": "indexer/__init__.py",
                "content": "from .unified_indexer import (\n    UnifiedIndexer,\n    process_repository_indexing,\n)\n",
                "metadata": {
                    "node_types": ["module", "import_from_statement"],
                    "file_symbols": ["process_repository_indexing"],
                    "chunk_role": "definition",
                    "file_roles": ["library_facade_surface"],
                    "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
                },
                "rrf": 0.2144,
            },
            {
                "file_path": "indexer/unified_indexer.py",
                "content": (
                    "@handle_async_errors()\n"
                    "async def process_repository_indexing(repo_path: str, repo_id: int) -> None:\n"
                    "    pass\n"
                ),
                "metadata": {
                    "node_types": ["function_definition"],
                    "file_symbols": ["process_repository_indexing"],
                    "chunk_role": "definition",
                    "file_roles": [],
                    "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
                },
                "rrf": 0.1773,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        self.assertEqual(query_class, "api_definition_lookup")
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(enriched[0]["file_path"], "indexer/unified_indexer.py")
        self.assertEqual(enriched[0]["implementation_declared_symbol_hit"], 0)
        self.assertEqual(enriched[0]["implementation_reexport_surface_hit"], 0)
        self.assertEqual(enriched[1]["implementation_reexport_surface_hit"], 1)

    def test_definition_lookup_prefers_true_definition_over_facade_surface(self):
        query = "where is process_repository_indexing defined"
        rows = [
            {
                "file_path": "indexer/facade.py",
                "content": (
                    "class IndexerFacade:\n"
                    "    async def run(self, repo_path, repo_id):\n"
                    "        return await unified.process_repository_indexing(repo_path, repo_id)\n"
                ),
                "metadata": {
                    "node_types": ["module", "class_definition", "method_definition"],
                    "file_symbols": ["process_repository_indexing", "IndexerFacade"],
                    "declared_symbols": ["IndexerFacade", "run"],
                    "chunk_role": "definition",
                    "context_path": ["api"],
                    "file_roles": ["library_facade_surface"],
                    "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
                },
                "rrf": 0.21,
            },
            {
                "file_path": "indexer/unified_indexer.py",
                "content": (
                    "@handle_async_errors()\n"
                    "async def process_repository_indexing(repo_path: str, repo_id: int) -> None:\n"
                    "    pass\n"
                ),
                "metadata": {
                    "node_types": ["function_definition"],
                    "file_symbols": ["process_repository_indexing"],
                    "declared_symbols": ["process_repository_indexing"],
                    "chunk_role": "definition",
                    "file_roles": [],
                    "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
                },
                "rrf": 0.18,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(enriched[0]["file_path"], "indexer/unified_indexer.py")
        self.assertEqual(enriched[0]["implementation_declared_symbol_hit"], 1)
        self.assertEqual(enriched[0]["implementation_facade_surface_hit"], 0)
        self.assertEqual(enriched[1]["implementation_declared_symbol_hit"], 0)
        self.assertEqual(enriched[1]["implementation_facade_surface_hit"], 1)

    def test_exact_identifier_helper_detects_camel_case_api_names(self):
        self.assertEqual(
            module.implementation_query_exact_identifiers("where is fromJSONSchema implemented"),
            {"fromjsonschema"},
        )
        self.assertEqual(
            module.implementation_query_exact_identifiers("where is parse implemented"),
            set(),
        )

    def test_exact_identifier_bonus_prefers_exact_symbol_over_related_neighbor(self):
        query = "where is fromJSONSchema implemented"
        rows = [
            {
                "file_path": "packages/zod/src/v4/core/to-json-schema.ts",
                "content": "export const createToJSONSchemaMethod = () => {}",
                "metadata": {
                    "node_types": ["function_declaration"],
                    "file_symbols": ["createToJSONSchemaMethod", "toJSONSchema"],
                    "declared_symbols": ["createToJSONSchemaMethod"],
                    "chunk_role": "definition",
                },
                "rrf": 0.26,
            },
            {
                "file_path": "packages/zod/src/v4/classic/from-json-schema.ts",
                "content": "export function fromJSONSchema(schema: JSONSchema) {}",
                "metadata": {
                    "node_types": ["function_declaration"],
                    "file_symbols": ["fromJSONSchema"],
                    "declared_symbols": ["fromJSONSchema"],
                    "chunk_role": "definition",
                },
                "rrf": 0.18,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(enriched[0]["file_path"], "packages/zod/src/v4/classic/from-json-schema.ts")
        self.assertEqual(enriched[0]["implementation_exact_identifier_hit"], 1)
        self.assertEqual(enriched[1]["implementation_exact_identifier_hit"], 0)

    def test_duplicate_rerank_uses_node_type_aware_rank_score_for_representative_choice(self):
        case = load_benchmark_case("code_definition_entrypoint_beats_cli_usage")
        rows = []
        for result in case["results"]:
            row = dict(result)
            meta = dict(row.get("metadata", {}))
            if row.get("file_path") == "crates/ts-pack-core/src/lib.rs":
                meta = current_contract_meta(meta, file_roles=["api_surface", "library_facade_surface"])
            else:
                meta = current_contract_meta(meta)
            row["_meta"] = meta
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=case["query"],
                query_class=module.implementation_query_class(case["query"]),
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            rows.append(row)
        contract = module.rerank_retrieval_results_contract(
            rows,
            query=case["query"],
            mode="code",
            include_debug=True,
        )
        self.assertEqual(contract["selection"]["representative_indices"][0], 2)
        self.assertEqual(contract["results"][0]["file_path"], "crates/ts-pack-core/src/intel/mod.rs")

    def test_implementation_search_prefers_internal_implementation_over_cli_usage(self):
        case = load_benchmark_case("code_implementation_search_prefers_internal_impl_over_wrapper")
        rows = case["results"]
        trace = module.build_implementation_ranking_trace(
            rows,
            case["query"],
        )
        self.assertEqual(trace["query_class"], "implementation_search")
        self.assertEqual(trace["rows"][0]["file_path"], "crates/ts-pack-core/src/intel/mod.rs")
        self.assertNotEqual(trace["rows"][0]["file_path"], "crates/ts-pack-cli/src/main.rs")

    def test_duplicate_rerank_contract_enriches_raw_implementation_search_rows(self):
        case = load_benchmark_case("code_implementation_search_prefers_internal_impl_over_wrapper")
        contract = module.rerank_retrieval_results_contract(
            case["results"],
            query=case["query"],
            mode="code",
            include_debug=True,
        )
        self.assertEqual(contract["results"][0]["file_path"], "crates/ts-pack-core/src/intel/mod.rs")
        self.assertNotEqual(contract["results"][0]["file_path"], "crates/ts-pack-cli/src/main.rs")

    def test_definition_fallback_pattern_targets_definitions(self):
        pattern = fallbacks_module.build_definition_fallback_pattern(["process"])
        self.assertIn("pub\\s+fn", pattern)
        self.assertIn("process", pattern)

    def test_member_usage_fallback_pattern_targets_receiver_qualified_calls(self):
        pattern = fallbacks_module.build_member_usage_fallback_pattern(["parser.parse"])
        self.assertIn("parser", pattern)
        self.assertIn("parse", pattern)
        self.assertIn("\\s*\\.\\s*", pattern)

    def test_normalize_fallback_paths_strips_dot_slash_prefix(self):
        self.assertEqual(
            fallbacks_module.normalize_fallback_paths(
                ["./examples/python_smoke/main.py", "e2e/python/tests/test_parsing.py", ""]
            ),
            ["examples/python_smoke/main.py", "e2e/python/tests/test_parsing.py"],
        )

    def test_implementation_chunk_role_does_not_fall_back_to_file_path_for_stale_indexes(self):
        self.assertEqual(
            module.implementation_chunk_role({}, "examples/python_smoke/main.py"),
            "",
        )
        self.assertEqual(
            module.implementation_chunk_role({}, "e2e/python/tests/test_parsing.py"),
            "",
        )

    def test_implementation_chunk_role_prefers_ts_pack_file_roles_before_paths(self):
        self.assertEqual(
            module.implementation_chunk_role({"file_roles": ["example_surface"]}, None),
            "example_usage",
        )
        self.assertEqual(
            module.implementation_chunk_role({"file_roles": ["test_surface"]}, None),
            "test_usage",
        )
        self.assertEqual(
            module.implementation_chunk_role({"file_roles": ["support_surface"]}, None),
            "script_support",
        )
        self.assertEqual(
            module.implementation_chunk_role({"file_roles": ["docs_surface"]}, None),
            "docs_support",
        )
        self.assertEqual(
            module.implementation_chunk_role({"file_roles": ["config_surface"]}, None),
            "config_support",
        )

    def test_implementation_chunk_role_skips_path_fallback_when_file_roles_are_present(self):
        self.assertEqual(
            module.implementation_chunk_role({"file_roles": []}, "examples/python_smoke/main.py"),
            "",
        )

    def test_dispatcher_role_wins_over_coexisting_profile_role(self):
        meta = {
            "file_roles": [
                "dispatcher_surface",
                "model_dispatcher_surface",
                "profile_surface",
            ],
            "chunk_role": "canonical_dispatcher_definition",
        }
        self.assertFalse(
            module.implementation_is_profile_candidate(
                "pydantic_ai_slim/pydantic_ai/models/__init__.py",
                meta,
            )
        )
        self.assertTrue(
            module.implementation_is_profile_candidate(
                "pydantic_ai_slim/pydantic_ai/profiles/openai.py",
                {"file_roles": ["profile_surface"], "chunk_role": "profile_definition"},
            )
        )

    def test_runtime_entrypoint_hit_skips_path_fallback_when_file_roles_are_present(self):
        query = "where is the main entrypoint in cmd/server"
        self.assertEqual(
            module.implementation_runtime_main_entrypoint_hit_with_meta(
                "cmd/server/main.go",
                query,
                {"file_roles": []},
            ),
            0,
        )
        self.assertEqual(
            module.implementation_runtime_main_entrypoint_hit_with_meta(
                "cmd/server/main.go",
                query,
                {"file_roles": ["runtime_entrypoint_surface"]},
            ),
            1,
        )

    def test_candidate_relevance_score_prefers_rank_score(self):
        row = {"rrf": 0.2, "rank_score": 0.9}
        self.assertEqual(module.candidate_relevance_score(row), 0.9)

    def test_receiver_qualified_usage_queries_classify_as_usage_lookup(self):
        self.assertEqual(
            module.implementation_query_class("where is parser.parse used in tree-sitter-language-pack"),
            "usage_lookup",
        )
        self.assertEqual(
            module.implementation_query_class("how is parser.parse used in tree-sitter-language-pack"),
            "usage_lookup",
        )
        self.assertEqual(
            module.implementation_query_class("examples of parser.parse in tree-sitter-language-pack"),
            "usage_lookup",
        )

    def test_exact_member_usage_hit_detects_receiver_qualified_usage(self):
        self.assertEqual(
            module.implementation_exact_member_usage_hit(
                "parser = get_parser('python')\ntree = parser.parse(b'x')\n",
                "where is parser.parse used in tree-sitter-language-pack",
            ),
            1,
        )
        self.assertEqual(
            module.implementation_exact_member_usage_hit(
                "pub fn parse(source: &str) {}\n",
                "where is parser.parse used in tree-sitter-language-pack",
            ),
            0,
        )

    def test_exact_member_usage_hit_prefers_metadata_when_present(self):
        self.assertEqual(
            module.implementation_exact_member_usage_hit(
                "",
                "where is parser.parse used in tree-sitter-language-pack",
                {"member_usages": ["parser.parse", "parser.reset"]},
            ),
            1,
        )

    def test_chunk_role_informs_usage_role(self):
        role = module.implementation_result_role(
            "examples/python_smoke/main.py",
            {"chunk_role": "example_usage", "node_types": ["call_expression"]},
            definition_hit=0,
            export_hit=0,
            api_entrypoint_hit=0,
        )
        self.assertEqual(role, "test_example")

    def test_test_file_role_overrides_definition_chunk_role(self):
        role = module.implementation_result_role(
            "test_search_rerank_tools.py",
            {
                "file_roles": ["test_surface"],
                "chunk_role": "definition",
                "node_types": ["function_definition"],
            },
            definition_hit=1,
            export_hit=0,
            api_entrypoint_hit=0,
        )
        self.assertEqual(role, "test_example")

    def test_file_roles_can_mark_generated_surface_without_path_heuristics(self):
        role = module.implementation_result_role(
            "src/runtime/provider.py",
            {"file_roles": ["generated_surface"], "node_types": ["function_definition"]},
            definition_hit=1,
            export_hit=0,
            api_entrypoint_hit=0,
        )
        self.assertEqual(role, "generated_surface")

    def test_exact_member_usage_site_hit_requires_usage_context(self):
        self.assertTrue(
            module.implementation_exact_member_usage_site_hit(
                {
                    "implementation_exact_member_usage_hit": 1,
                    "implementation_role": "test_example",
                    "_meta": {"chunk_role": "example_usage"},
                }
            )
        )
        self.assertFalse(
            module.implementation_exact_member_usage_site_hit(
                {
                    "implementation_exact_member_usage_hit": 1,
                    "implementation_role": "internal_implementation",
                    "_meta": {"chunk_role": "definition"},
                }
            )
        )

    def test_usage_lookup_prefers_exact_receiver_qualified_usage_sites(self):
        query = "where is parser.parse used in tree-sitter-language-pack"
        rows = [
            {
                "file_path": "crates/ts-pack-core/src/lib.rs",
                "content": "pub fn get_parser(name: &str) -> Result<tree_sitter::Parser, Error> { ... }",
                "metadata": {"node_types": ["function_item"], "file_symbols": ["get_parser"], "chunk_role": "definition"},
                "rrf": 0.91,
            },
            {
                "file_path": "examples/python_smoke/main.py",
                "content": "parser = get_parser(\"python\")\ntree = parser.parse(b\"def hello(): pass\")\n",
                "metadata": {
                    "node_types": ["call_expression", "expression_statement"],
                    "file_symbols": [],
                    "chunk_role": "example_usage",
                    "member_usages": ["parser.parse"],
                },
                "rrf": 0.82,
            },
            {
                "file_path": "crates/ts-pack-core/src/parse.rs",
                "content": "pub fn parse_string(language: &str, source: &[u8]) -> Result<Tree, Error> { parser.parse(source, None) }",
                "metadata": {
                    "node_types": ["function_item", "call_expression"],
                    "file_symbols": ["parse_string"],
                    "chunk_role": "definition",
                    "member_usages": ["parser.parse"],
                },
                "rrf": 0.88,
            },
        ]
        enriched = []
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=module.implementation_query_class(query),
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(enriched[0]["file_path"], "examples/python_smoke/main.py")
        self.assertEqual(enriched[1]["file_path"], "crates/ts-pack-core/src/parse.rs")

    def test_usage_lookup_prefers_examples_over_unrelated_tests_for_exact_member_hits(self):
        query = "where is parser.parse used in tree-sitter-language-pack"
        rows = [
            {
                "file_path": "e2e/python/tests/test_error_handling.py",
                "content": "parser = get_parser('javascript')\ntree = parser.parse(b'')\n",
                "metadata": {
                    "node_types": ["call_expression"],
                    "file_symbols": [],
                    "chunk_role": "test_usage",
                    "member_usages": ["parser.parse"],
                },
                "rrf": 0.0,
            },
            {
                "file_path": "examples/python_smoke/main.py",
                "content": "parser = get_parser('python')\ntree = parser.parse(b'def hello(): pass')\n",
                "metadata": {
                    "node_types": ["call_expression"],
                    "file_symbols": [],
                    "chunk_role": "example_usage",
                    "member_usages": ["parser.parse"],
                },
                "rrf": 0.0,
            },
        ]
        enriched = []
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=module.implementation_query_class(query),
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(enriched[0]["file_path"], "examples/python_smoke/main.py")

    def test_usage_query_helpers_distinguish_examples_and_tests(self):
        self.assertFalse(module.usage_query_prefers_test_results("where is parser.parse used"))
        self.assertTrue(module.usage_query_prefers_test_results("where is parser.parse used in tests"))
        self.assertFalse(module.usage_query_prefers_example_results("where is parser.parse used"))
        self.assertTrue(module.usage_query_prefers_example_results("show parser.parse examples"))

    def test_role_policy_keeps_priority_and_score_in_one_table(self):
        usage_policy = module.implementation_role_policy("usage_lookup")
        self.assertEqual(usage_policy["test_example"]["priority"], 7)
        self.assertEqual(usage_policy["test_example"]["score"], 0.10)
        self.assertEqual(module.implementation_role_priority("test_example", "usage_lookup"), 7)
        self.assertEqual(module.implementation_role_score("test_example", "usage_lookup"), 0.10)

        explanation_policy = module.implementation_role_policy("implementation_explanation")
        self.assertEqual(explanation_policy["public_api_definition"]["priority"], 5)
        self.assertEqual(explanation_policy["public_api_definition"]["score"], 0.02)
        self.assertEqual(
            module.implementation_role_priority("public_api_definition", "implementation_explanation"),
            5,
        )
        self.assertEqual(
            module.implementation_role_score("public_api_definition", "implementation_explanation"),
            0.02,
        )

    def test_node_type_policy_keeps_priority_and_score_in_one_table(self):
        usage_policy = module.implementation_node_type_policy("usage_lookup")
        self.assertEqual(usage_policy["callsite"]["priority"], 4)
        self.assertEqual(usage_policy["callsite"]["score"], 0.05)

        definition_policy = module.implementation_node_type_policy("api_definition_lookup")
        self.assertEqual(definition_policy["declaration"]["priority"], 4)
        self.assertEqual(definition_policy["declaration"]["score"], 0.05)
        self.assertEqual(definition_policy["callsite"]["priority"], -4)
        self.assertEqual(definition_policy["callsite"]["score"], -0.06)

    def test_node_type_priority_and_score_follow_shared_policy(self):
        meta = {"node_types": ["function_definition", "export_statement", "source_file"]}
        self.assertEqual(module.implementation_node_type_priority(meta, "api_definition_lookup"), 7)
        self.assertEqual(module.implementation_node_type_score(meta, "api_definition_lookup"), 0.09)

        usage_meta = {"node_types": ["call_expression"]}
        self.assertEqual(module.implementation_node_type_priority(usage_meta, "usage_lookup"), 4)
        self.assertEqual(module.implementation_node_type_score(usage_meta, "usage_lookup"), 0.05)

    def test_intent_policy_keeps_bonus_weights_in_one_table(self):
        search_policy = module.implementation_intent_policy(
            "where is model inference selected",
            "implementation_search",
        )
        self.assertNotIn("allow_dispatcher_bonus", search_policy)
        self.assertNotIn("dispatcher_bonus_weight", search_policy)
        self.assertNotIn("request_handler_bonus_weight", search_policy)
        self.assertNotIn("routing_bonus_weight", search_policy)
        self.assertNotIn("controller_entity_bonus_weight", search_policy)
        self.assertNotIn("allow_routing_bonus", search_policy)
        self.assertNotIn("allow_request_handler_bonus", search_policy)
        self.assertEqual(search_policy["declared_symbol_bonus_search"], 0.02)
        self.assertEqual(search_policy["exact_identifier_bonus_weight"], 0.08)

        definition_policy = module.implementation_intent_policy(
            "where is process defined",
            "api_definition_lookup",
        )
        self.assertNotIn("allow_dispatcher_bonus", definition_policy)
        self.assertNotIn("allow_routing_bonus", definition_policy)
        self.assertEqual(definition_policy["declared_symbol_bonus_definition"], 0.05)
        self.assertEqual(definition_policy["exact_identifier_bonus_weight"], 0.08)
        self.assertEqual(definition_policy["reexport_surface_penalty_definition"], 0.07)

        usage_policy = module.implementation_intent_policy(
            "where is parser.parse used",
            "usage_lookup",
        )
        self.assertEqual(usage_policy["member_usage_bonus_usage"], 0.06)
        self.assertNotIn("allow_dispatcher_bonus", usage_policy)
        self.assertEqual(usage_policy["exact_identifier_bonus_weight"], 0.0)

    def test_support_surface_penalties_are_query_class_aware(self):
        penalties = module.implementation_support_surface_penalties(
            "docs/guide.md",
            query="where is process defined",
            query_class="api_definition_lookup",
            doc_like=True,
            low_signal_support=False,
            usage_heavy=False,
        )
        self.assertEqual(penalties["doc_penalty"], 0.05)
        self.assertEqual(penalties["support_path_penalty"], 0.0)
        self.assertEqual(penalties["usage_penalty"], 0.0)

        penalties = module.implementation_support_surface_penalties(
            "tools/dev.py",
            query="how does process work",
            query_class="implementation_explanation",
            doc_like=False,
            low_signal_support=True,
            usage_heavy=False,
        )
        self.assertGreater(penalties["support_path_penalty"], 0.0)

        penalties = module.implementation_support_surface_penalties(
            "tools/dev.py",
            query="how does the build script work",
            query_class="implementation_explanation",
            doc_like=False,
            low_signal_support=True,
            usage_heavy=False,
        )
        self.assertEqual(penalties["support_path_penalty"], 0.0)

    def test_usage_surface_bonus_prefers_examples_or_tests_only_when_relevant(self):
        self.assertEqual(
            module.implementation_usage_surface_bonus(
                chunk_role="example_usage",
                query="where is parser.parse used",
                query_class="usage_lookup",
                exact_member_hits=1,
            ),
            0.11,
        )
        self.assertGreater(
            module.implementation_usage_surface_bonus(
                chunk_role="example_usage",
                query="show parser.parse examples",
                query_class="usage_lookup",
                exact_member_hits=1,
            ),
            0.11,
        )
        self.assertGreater(
            module.implementation_usage_surface_bonus(
                chunk_role="test_usage",
                query="where is parser.parse used in tests",
                query_class="usage_lookup",
                exact_member_hits=1,
            ),
            0.06,
        )
        self.assertEqual(
            module.implementation_usage_surface_bonus(
                chunk_role="test_usage",
                query="where is parser.parse used",
                query_class="implementation_search",
                exact_member_hits=1,
            ),
            0.0,
        )

    def test_inferred_filename_hints_add_controller_candidates_for_routing_queries(self):
        hints = module.implementation_inferred_filename_hints(
            "where is owner request routing implemented in spring petclinic"
        )
        self.assertIn("ownercontroller.java", hints)
        self.assertNotIn("requestcontroller.java", hints)

    def test_inferred_filename_hints_add_view_candidates_for_ui_queries(self):
        hints = module.implementation_inferred_filename_hints(
            "where is the editor sidebar implemented in FrameCreator"
        )
        self.assertIn("sidebarview.swift", hints)
        self.assertIn("views/sidebarview.swift", hints)

    def test_view_surface_body_chunk_beats_inner_section_for_ui_definition_queries(self):
        query = "where is the editor sidebar implemented in FrameCreator"
        rows = [
            {
                "file_path": "FrameCreator/Views/SidebarView.swift",
                "content": "private var generationSettingsSection: some View { }",
                "metadata": {
                    "file_roles": ["view_surface"],
                    "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
                    "context_path": ["SidebarView", "generationSettingsSection"],
                    "node_types": ["property_declaration"],
                    "chunk_role": "definition",
                },
                "rrf": 0.82,
            },
            {
                "file_path": "FrameCreator/Views/SidebarView.swift",
                "content": "var body: some View { }",
                "metadata": {
                    "file_roles": ["view_surface"],
                    "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
                    "context_path": ["SidebarView", "body"],
                    "node_types": ["property_declaration"],
                    "chunk_role": "definition",
                },
                "rrf": 0.81,
            },
        ]
        enriched = []
        query_class = module.implementation_query_class(query)
        for result in rows:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=query,
                query_class=query_class,
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            enriched.append(row)
        enriched.sort(key=module.implementation_rank_tuple)
        self.assertEqual(enriched[0]["_meta"]["context_path"], ["SidebarView", "body"])
        self.assertGreater(enriched[0]["implementation_rank_components"]["view_body_bonus"], 0.0)

    def test_definition_entrypoint_golden_prefers_library_root_over_cli(self):
        case = load_benchmark_case("code_definition_entrypoint_beats_cli_usage")
        self.assertEqual(case["query_class"], "implementation_explanation")
        rows = []
        for result in case["results"]:
            row = dict(result)
            meta = dict(row.get("metadata", {}))
            if row.get("file_path") == "crates/ts-pack-core/src/lib.rs":
                meta = current_contract_meta(meta, file_roles=["api_surface", "library_facade_surface"])
            else:
                meta = current_contract_meta(meta)
            row["_meta"] = meta
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=case["query"],
                query_class=module.implementation_query_class(case["query"]),
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            rows.append(row)
        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "crates/ts-pack-core/src/intel/mod.rs")
        self.assertNotEqual(rows[0]["file_path"], "crates/ts-pack-cli/src/main.rs")

    def test_definition_lookup_golden_prefers_library_root_over_docs(self):
        case = load_benchmark_case("code_definition_lookup_surfaces_library_root")
        self.assertEqual(case["query_class"], "api_definition_lookup")
        rows = []
        for result in case["results"]:
            row = dict(result)
            meta = dict(row.get("metadata", {}))
            if row.get("file_path") == "crates/ts-pack-core/src/lib.rs":
                meta = current_contract_meta(meta, file_roles=["api_surface", "library_facade_surface"])
            else:
                meta = current_contract_meta(meta)
            row["_meta"] = meta
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=case["query"],
                query_class=module.implementation_query_class(case["query"]),
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            rows.append(row)
        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "crates/ts-pack-core/src/lib.rs")

    def test_symbol_lookup_golden_prefers_definition_over_usage(self):
        case = load_benchmark_case("code_symbol_lookup_prefers_definition_over_usage")
        self.assertEqual(case["query_class"], "symbol_lookup")
        rows = []
        for result in case["results"]:
            row = dict(result)
            meta = dict(row.get("metadata", {}))
            if row.get("file_path") == "crates/ts-pack-core/src/lib.rs":
                meta = current_contract_meta(meta, file_roles=["api_surface", "library_facade_surface"])
            else:
                meta = current_contract_meta(meta)
            row["_meta"] = meta
            row["meta_score"] = module.meta_score(row["_meta"])
            module.enrich_implementation_result(
                row,
                query=case["query"],
                query_class=module.implementation_query_class(case["query"]),
                base_score=float(row.get("rrf", 0.0) or 0.0),
                meta_boost=0.0,
            )
            rows.append(row)
        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "crates/ts-pack-core/src/lib.rs")

    def test_usage_lookup_golden_prefers_callsite_over_definition(self):
        case = load_benchmark_case("code_usage_lookup_prefers_callsite_over_definition")
        self.assertEqual(case["query_class"], "usage_lookup")
        rows = []
        for result in case["results"]:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["doc_like"] = module.is_doc_like_path(row.get("file_path"))
            row["low_signal_parser_data"] = module.is_low_signal_parser_data_path(row.get("file_path"))
            row["low_signal_binding_surface"] = module.is_low_signal_binding_surface_path(row.get("file_path"))
            row["implementation_symbol_hit"] = module.implementation_symbol_hit(row["_meta"], case["query"])
            row["implementation_definition_hit"] = module.implementation_definition_hit(
                row.get("content", ""), case["query"]
            )
            row["implementation_api_entrypoint_hit"] = module.implementation_api_entrypoint_hit(
                row.get("file_path", ""), row.get("implementation_definition_hit", 0)
            )
            row["implementation_usage_heavy_penalty"] = (
                module.query_class_prefers_definitions(module.implementation_query_class(case["query"]))
                and module.is_usage_heavy_path(row.get("file_path", ""))
            )
            rows.append(row)
        rows.sort(key=module.implementation_rank_tuple)
        self.assertIn(
            rows[0]["file_path"],
            {"crates/ts-pack-cli/src/main.rs", "crates/ts-pack-node/src/lib.rs"},
        )
        self.assertNotEqual(rows[0]["file_path"], "crates/ts-pack-core/src/lib.rs")

    def test_member_usage_lookup_golden_prefers_exact_receiver_usage(self):
        case = load_benchmark_case("code_member_usage_lookup_prefers_exact_receiver_usage")
        self.assertEqual(case["query_class"], "usage_lookup")
        contract = module.rerank_retrieval_results_contract(
            case["results"],
            query=case["query"],
            mode="code",
        )
        self.assertIn(
            contract["results"][0]["file_path"],
            {"examples/python_smoke/main.py", "e2e/python/tests/test_parsing.py"},
        )
        self.assertNotEqual(contract["results"][0]["file_path"], "crates/ts-pack-core/src/lib.rs")


if __name__ == "__main__":
    unittest.main()

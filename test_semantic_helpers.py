import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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
        self.assertIn("--- src/a.py (Score: 1.2345) ---", output)
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
            module.implementation_api_entrypoint_hit("crates/ts-pack-core/src/lib.rs", 1),
            1,
        )
        self.assertEqual(
            module.implementation_api_entrypoint_hit("crates/ts-pack-cli/src/main.rs", 1),
            1,
        )

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

    def test_duplicate_rerank_uses_node_type_aware_rank_score_for_representative_choice(self):
        case = load_benchmark_case("code_definition_entrypoint_beats_cli_usage")
        rows = []
        for result in case["results"]:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
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
        self.assertEqual(contract["selection"]["representative_indices"][0], 1)
        self.assertEqual(contract["results"][0]["file_path"], "crates/ts-pack-core/src/lib.rs")

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

    def test_implementation_chunk_role_falls_back_to_file_path(self):
        self.assertEqual(
            module.implementation_chunk_role({}, "examples/python_smoke/main.py"),
            "example_usage",
        )
        self.assertEqual(
            module.implementation_chunk_role({}, "e2e/python/tests/test_parsing.py"),
            "test_usage",
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

    def test_definition_entrypoint_golden_prefers_library_root_over_cli(self):
        case = load_benchmark_case("code_definition_entrypoint_beats_cli_usage")
        self.assertEqual(case["query_class"], "implementation_explanation")
        rows = []
        for result in case["results"]:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["doc_like"] = module.is_doc_like_path(row.get("file_path"))
            row["low_signal_parser_data"] = module.is_low_signal_parser_data_path(row.get("file_path"))
            row["low_signal_binding_surface"] = module.is_low_signal_binding_surface_path(
                row.get("file_path")
            )
            row["implementation_symbol_hit"] = module.implementation_symbol_hit(
                row["_meta"], case["query"]
            )
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
        self.assertEqual(rows[0]["file_path"], "crates/ts-pack-core/src/lib.rs")
        self.assertNotEqual(rows[0]["file_path"], "crates/ts-pack-cli/src/main.rs")

    def test_definition_lookup_golden_prefers_library_root_over_docs(self):
        case = load_benchmark_case("code_definition_lookup_surfaces_library_root")
        self.assertEqual(case["query_class"], "api_definition_lookup")
        rows = []
        for result in case["results"]:
            row = dict(result)
            row["_meta"] = row.get("metadata", {})
            row["doc_like"] = module.is_doc_like_path(row.get("file_path"))
            row["low_signal_parser_data"] = module.is_low_signal_parser_data_path(row.get("file_path"))
            row["low_signal_binding_surface"] = module.is_low_signal_binding_surface_path(
                row.get("file_path")
            )
            row["implementation_symbol_hit"] = module.implementation_symbol_hit(
                row["_meta"], case["query"]
            )
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
        self.assertEqual(rows[0]["file_path"], "crates/ts-pack-core/src/lib.rs")

    def test_symbol_lookup_golden_prefers_definition_over_usage(self):
        case = load_benchmark_case("code_symbol_lookup_prefers_definition_over_usage")
        self.assertEqual(case["query_class"], "symbol_lookup")
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

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/semantic_helpers.py"
FALLBACKS_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/fallbacks.py"
GOLDENS_PATH = "/Users/michaelmarler/Projects/rest_proxy/benchmarks/retrieval_duplicate_goldens.json"


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
            "definition_oriented",
        )
        self.assertEqual(
            module.implementation_query_class("where is process defined"),
            "definition_oriented",
        )
        self.assertEqual(
            module.implementation_query_class("where is it called"),
            "usage_oriented",
        )

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
            0,
        )

    def test_definition_fallback_pattern_targets_definitions(self):
        pattern = fallbacks_module.build_definition_fallback_pattern(["process"])
        self.assertIn("pub\\s+fn", pattern)
        self.assertIn("process", pattern)

    def test_candidate_relevance_score_prefers_rank_score(self):
        row = {"rrf": 0.2, "rank_score": 0.9}
        self.assertEqual(module.candidate_relevance_score(row), 0.9)

    def test_definition_entrypoint_golden_prefers_library_root_over_cli(self):
        case = load_benchmark_case("code_definition_entrypoint_beats_cli_usage")
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
                module.implementation_query_class(case["query"]) == "definition_oriented"
                and module.is_usage_heavy_path(row.get("file_path", ""))
            )
            rows.append(row)
        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "crates/ts-pack-core/src/lib.rs")
        self.assertNotEqual(rows[0]["file_path"], "crates/ts-pack-cli/src/main.rs")

    def test_definition_lookup_golden_prefers_library_root_over_docs(self):
        case = load_benchmark_case("code_definition_lookup_surfaces_library_root")
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
                module.implementation_query_class(case["query"]) == "definition_oriented"
                and module.is_usage_heavy_path(row.get("file_path", ""))
            )
            rows.append(row)
        rows.sort(key=module.implementation_rank_tuple)
        self.assertEqual(rows[0]["file_path"], "crates/ts-pack-core/src/lib.rs")


if __name__ == "__main__":
    unittest.main()

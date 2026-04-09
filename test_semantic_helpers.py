import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/semantic_helpers.py"


spec = importlib.util.spec_from_file_location("semantic_helpers_under_test", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


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

    def test_collapse_near_duplicate_results_prefers_lower_level_rerank_contract(self):
        rows = [
            {"file_path": "src/a.py", "project_id": "p", "rrf": 1.0, "content": "same-a"},
            {"file_path": "src/b.py", "project_id": "p", "rrf": 0.9, "content": "same-b"},
            {"file_path": "src/c.py", "project_id": "p", "rrf": 0.8, "content": "different"},
        ]
        fake_ts_pack = mock.Mock()
        fake_ts_pack.rerank_diverse_texts.return_value = {
            "mode": "code_retrieval",
            "keep_indices": [2, 0],
            "suppressed_indices": [1],
            "exact_suppressed_indices": [1],
            "group_order": [1, 0],
            "representative_indices": [2, 0],
            "mmr_lambda": 0.78,
        }
        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            collapsed = module.collapse_near_duplicate_results(rows, query="delete user helper", mode="code")
        self.assertEqual([row["file_path"] for row in collapsed], ["src/c.py", "src/a.py"])
        fake_ts_pack.rerank_diverse_texts.assert_called_once()

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


if __name__ == "__main__":
    unittest.main()

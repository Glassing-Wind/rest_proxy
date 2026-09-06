import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "dispatcher_telemetry_eval.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("dispatcher_telemetry_eval_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DispatcherTelemetryEvalTests(unittest.TestCase):
    def test_summarize_events_counts_diagnoses_and_rates(self):
        mod = _load_module()
        events = [
            {
                "tool": "search_codebase",
                "telemetry": {
                    "query_class": "implementation_search",
                    "diagnosis": "no_exact_dispatcher_signal",
                    "rescue_applied": False,
                    "semantic_top": {"exact_hit": False, "contract_hit": False},
                    "ranked_top": {"exact_hit": False, "contract_hit": False},
                    "final_top": {"exact_hit": False, "contract_hit": False},
                },
            },
            {
                "tool": "search_codebase",
                "telemetry": {
                    "query_class": "api_definition_lookup",
                    "diagnosis": "semantic_recall_missing_contract_candidate",
                    "rescue_applied": True,
                    "semantic_top": {"exact_hit": False, "contract_hit": False},
                    "ranked_top": {"exact_hit": False, "contract_hit": False},
                    "final_top": {"exact_hit": True, "contract_hit": True},
                },
            },
            {
                "tool": "search_codebase",
                "telemetry": {
                    "query_class": "implementation_explanation",
                    "diagnosis": "contract_missing_from_semantic_candidates",
                    "rescue_applied": False,
                    "semantic_top": {"exact_hit": True, "contract_hit": False},
                    "ranked_top": {"exact_hit": True, "contract_hit": False},
                    "final_top": {"exact_hit": True, "contract_hit": False},
                },
            },
        ]
        summary = mod.summarize_events(events)
        self.assertEqual(summary["total_events"], 3)
        self.assertEqual(summary["contract_eligible_events"], 2)
        self.assertEqual(summary["ignored_no_signal_events"], 1)
        self.assertEqual(summary["tool_counts"], {"search_codebase": 3})
        self.assertEqual(summary["overall_diagnosis_counts"]["no_exact_dispatcher_signal"], 1)
        self.assertEqual(
            summary["diagnosis_counts"],
            {
                "contract_missing_from_semantic_candidates": 1,
                "semantic_recall_missing_contract_candidate": 1,
            },
        )
        self.assertAlmostEqual(summary["rescue_applied_rate"], 0.5)
        self.assertAlmostEqual(summary["semantic_top_exact_hit_rate"], 0.5)
        self.assertAlmostEqual(summary["final_top_exact_hit_rate"], 1.0)
        self.assertAlmostEqual(summary["final_top_contract_hit_rate"], 0.5)

    def test_evaluate_telemetry_reads_ndjson_file(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "dispatcher.ndjson"
            target.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "tool": "search_codebase",
                                "telemetry": {
                                    "query_class": "api_definition_lookup",
                                    "diagnosis": "ranking_or_promotion_needed",
                                    "rescue_applied": False,
                                    "semantic_top": {"exact_hit": True, "contract_hit": True},
                                    "ranked_top": {"exact_hit": True, "contract_hit": True},
                                    "final_top": {"exact_hit": True, "contract_hit": True},
                                },
                            }
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            report = mod.evaluate_telemetry(str(target))
        self.assertEqual(report["telemetry_path"], str(target))
        self.assertEqual(report["summary"]["total_events"], 1)
        self.assertEqual(report["summary"]["contract_eligible_events"], 1)
        self.assertEqual(report["summary"]["diagnosis_counts"], {"ranking_or_promotion_needed": 1})
        self.assertEqual(report["recent_summary"]["contract_eligible_events"], 1)

    def test_evaluate_telemetry_recent_summary_uses_latest_eligible_window(self):
        mod = _load_module()
        events = [
            {
                "tool": "search_codebase",
                "telemetry": {
                    "diagnosis": "no_exact_dispatcher_signal",
                    "semantic_top": {"exact_hit": False, "contract_hit": False},
                    "ranked_top": {"exact_hit": False, "contract_hit": False},
                    "final_top": {"exact_hit": False, "contract_hit": False},
                },
            },
            {
                "tool": "search_codebase",
                "telemetry": {
                    "diagnosis": "contract_missing_from_semantic_candidates",
                    "query_class": "implementation_explanation",
                    "semantic_exact_match_count": 1,
                    "semantic_top": {"exact_hit": True, "contract_hit": False},
                    "ranked_top": {"exact_hit": True, "contract_hit": False},
                    "final_top": {"exact_hit": True, "contract_hit": False},
                },
            },
            {
                "tool": "search_codebase",
                "telemetry": {
                    "diagnosis": "ranking_surfaces_contract",
                    "query_class": "implementation_explanation",
                    "semantic_exact_match_count": 1,
                    "semantic_contract_match_count": 1,
                    "ranked_contract_match_count": 1,
                    "final_contract_match_count": 1,
                    "semantic_top": {"exact_hit": True, "contract_hit": True},
                    "ranked_top": {"exact_hit": True, "contract_hit": True},
                    "final_top": {"exact_hit": True, "contract_hit": True},
                },
            },
            {
                "tool": "search_codebase",
                "telemetry": {
                    "diagnosis": "semantic_recall_missing_contract_candidate",
                    "query_class": "api_definition_lookup",
                    "rescue_applied": True,
                    "semantic_top": {"exact_hit": False, "contract_hit": False},
                    "ranked_top": {"exact_hit": False, "contract_hit": False},
                    "final_top": {"exact_hit": True, "contract_hit": True},
                },
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "dispatcher.ndjson"
            target.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
            report = mod.evaluate_telemetry(str(target), recent_limit=2)
        self.assertEqual(report["summary"]["contract_eligible_events"], 3)
        self.assertEqual(report["recent_summary"]["contract_eligible_events"], 2)
        self.assertEqual(
            report["recent_summary"]["diagnosis_counts"],
            {
                "ranking_surfaces_contract": 1,
                "semantic_recall_missing_contract_candidate": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()

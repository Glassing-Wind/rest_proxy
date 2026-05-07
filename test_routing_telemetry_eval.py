import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "routing_telemetry_eval.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("routing_telemetry_eval_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RoutingTelemetryEvalTests(unittest.TestCase):
    def test_summarize_events_counts_diagnoses_and_rates(self):
        mod = _load_module()
        events = [
            {
                "tool": "search_codebase",
                "telemetry": {
                    "query_class": "implementation_explanation",
                    "diagnosis": "no_routing_signal",
                    "partition_applied": False,
                    "semantic_top": {"routing_hit": False, "request_handler_hit": False, "controller_entity_hit": False},
                    "ranked_top": {"routing_hit": False, "request_handler_hit": False, "controller_entity_hit": False},
                    "final_top": {"routing_hit": False, "request_handler_hit": False, "controller_entity_hit": False},
                },
            },
            {
                "tool": "search_codebase",
                "telemetry": {
                    "query_class": "api_definition_lookup",
                    "diagnosis": "partition_surfaces_routing_signal",
                    "partition_applied": True,
                    "semantic_signal_match_count": 1,
                    "semantic_top": {"routing_hit": False, "request_handler_hit": False, "controller_entity_hit": False},
                    "ranked_top": {"routing_hit": False, "request_handler_hit": False, "controller_entity_hit": False},
                    "final_top": {"routing_hit": True, "request_handler_hit": True, "controller_entity_hit": True},
                },
            },
            {
                "tool": "search_codebase",
                "telemetry": {
                    "query_class": "implementation_explanation",
                    "diagnosis": "ranking_surfaces_routing_signal",
                    "partition_applied": False,
                    "semantic_signal_match_count": 1,
                    "ranked_signal_match_count": 1,
                    "final_signal_match_count": 1,
                    "semantic_top": {"routing_hit": True, "request_handler_hit": False, "controller_entity_hit": False},
                    "ranked_top": {"routing_hit": True, "request_handler_hit": False, "controller_entity_hit": False},
                    "final_top": {"routing_hit": True, "request_handler_hit": False, "controller_entity_hit": False},
                },
            },
        ]
        summary = mod.summarize_events(events)
        self.assertEqual(summary["total_events"], 3)
        self.assertEqual(summary["signal_eligible_events"], 2)
        self.assertEqual(summary["ignored_no_signal_events"], 1)
        self.assertEqual(summary["tool_counts"], {"search_codebase": 3})
        self.assertEqual(summary["overall_diagnosis_counts"]["no_routing_signal"], 1)
        self.assertEqual(
            summary["diagnosis_counts"],
            {
                "partition_surfaces_routing_signal": 1,
                "ranking_surfaces_routing_signal": 1,
            },
        )
        self.assertAlmostEqual(summary["partition_applied_rate"], 0.5)
        self.assertAlmostEqual(summary["final_top_signal_hit_rate"], 1.0)

    def test_evaluate_telemetry_reads_ndjson_file(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "routing.ndjson"
            target.write_text(
                json.dumps(
                    {
                        "tool": "search_codebase",
                        "telemetry": {
                            "query_class": "implementation_explanation",
                            "diagnosis": "ranking_surfaces_routing_signal",
                            "semantic_signal_match_count": 1,
                            "ranked_signal_match_count": 1,
                            "final_signal_match_count": 1,
                            "semantic_top": {
                                "routing_hit": True,
                                "request_handler_hit": False,
                                "controller_entity_hit": False,
                            },
                            "ranked_top": {
                                "routing_hit": True,
                                "request_handler_hit": False,
                                "controller_entity_hit": False,
                            },
                            "final_top": {
                                "routing_hit": True,
                                "request_handler_hit": False,
                                "controller_entity_hit": False,
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            report = mod.evaluate_telemetry(str(target))
        self.assertEqual(report["telemetry_path"], str(target))
        self.assertEqual(report["summary"]["total_events"], 1)
        self.assertEqual(report["summary"]["signal_eligible_events"], 1)
        self.assertEqual(report["summary"]["diagnosis_counts"], {"ranking_surfaces_routing_signal": 1})


if __name__ == "__main__":
    unittest.main()

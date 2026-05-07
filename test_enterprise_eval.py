import importlib.util
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "scripts" / "run_enterprise_eval.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("enterprise_eval_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class EnterpriseEvalSummaryTests(unittest.TestCase):
    def test_build_summary_prefers_highest_mrr_config(self):
        mod = _load_module()
        payload = {
            "retrieval_eval": {
                "summary": {
                    "baseline": {"mrr": 0.7},
                    "group_representatives": {"mrr": 0.95, "hit_at_k": 1.0},
                    "query_aware": {"mrr": 0.9},
                },
                "alerts": {},
                "query_class_counts": {"usage_lookup": 2},
                "cases": [],
            },
            "dispatcher_eval": {
                "summary": {
                    "dispatcher_anchor_contract_capability": "focused_dispatcher_anchor_v1",
                    "dispatcher_anchor_contract_version": 1,
                    "semantic_candidate_hit_rate": 0.5,
                    "semantic_candidate_contract_hit_rate": 0.5,
                    "implementation_ranking_top_hit_rate": 0.5,
                    "implementation_ranking_contract_top_hit_rate": 0.5,
                    "final_dispatcher_selection_top_hit_rate": 1.0,
                    "final_dispatcher_selection_contract_top_hit_rate": 1.0,
                    "diagnosis_counts": {"ranking_fixed": 2},
                }
            },
            "dispatcher_telemetry_eval": {
                "summary": {
                    "total_events": 3,
                    "contract_eligible_events": 3,
                    "ignored_no_signal_events": 0,
                    "rescue_applied_rate": 0.3333,
                    "semantic_top_exact_hit_rate": 0.3333,
                    "final_top_exact_hit_rate": 1.0,
                    "semantic_top_contract_hit_rate": 0.3333,
                    "implementation_ranking_top_contract_hit_rate": 0.6667,
                    "final_top_contract_hit_rate": 1.0,
                    "diagnosis_counts": {
                        "ranking_or_promotion_needed": 1,
                        "semantic_recall_missing_contract_candidate": 2,
                    },
                },
                "recent_summary": {
                    "contract_eligible_events": 2,
                    "rescue_applied_rate": 0.5,
                    "semantic_top_contract_hit_rate": 0.5,
                    "implementation_ranking_top_contract_hit_rate": 1.0,
                    "final_top_contract_hit_rate": 1.0,
                    "diagnosis_counts": {
                        "ranking_surfaces_contract": 1,
                        "semantic_recall_missing_contract_candidate": 1,
                    },
                },
            },
            "routing_telemetry_eval": {
                "summary": {
                    "total_events": 4,
                    "signal_eligible_events": 3,
                    "ignored_no_signal_events": 1,
                    "partition_applied_rate": 0.3333,
                    "semantic_top_signal_hit_rate": 0.6667,
                    "implementation_ranking_top_signal_hit_rate": 0.6667,
                    "final_top_signal_hit_rate": 1.0,
                    "diagnosis_counts": {
                        "partition_surfaces_routing_signal": 1,
                        "ranking_surfaces_routing_signal": 2,
                    },
                },
                "recent_summary": {
                    "signal_eligible_events": 2,
                    "partition_applied_rate": 0.5,
                    "semantic_top_signal_hit_rate": 0.5,
                    "implementation_ranking_top_signal_hit_rate": 1.0,
                    "final_top_signal_hit_rate": 1.0,
                    "diagnosis_counts": {
                        "partition_surfaces_routing_signal": 1,
                        "ranking_surfaces_routing_signal": 1,
                    },
                },
            },
            "live_graph_goldens": {
                "ok": True,
                "skipped": False,
                "workspaces": ["/tmp/repo"],
            },
        }
        summary = mod.build_enterprise_summary(payload)
        self.assertTrue(summary["live_graph_ok"])
        self.assertEqual(summary["best_retrieval_config"]["name"], "group_representatives")
        self.assertEqual(summary["retrieval_query_class_counts"], {"usage_lookup": 2})
        self.assertEqual(
            summary["dispatcher_summary"]["final_dispatcher_selection_top_hit_rate"],
            1.0,
        )
        self.assertEqual(summary["dispatcher_telemetry_summary"]["total_events"], 3)
        self.assertEqual(summary["dispatcher_telemetry_recent_summary"]["contract_eligible_events"], 2)
        self.assertEqual(summary["routing_telemetry_summary"]["total_events"], 4)
        self.assertEqual(summary["routing_telemetry_recent_summary"]["signal_eligible_events"], 2)

    def test_build_summary_collects_only_configs_with_alerts(self):
        mod = _load_module()
        payload = {
            "retrieval_eval": {
                "summary": {"baseline": {"mrr": 0.7}},
                "alerts": {"promoted_non_exact": ["ndcg_regressed"]},
                "query_class_counts": {},
                "cases": [
                    {
                        "id": "docs_case",
                        "configs": {
                            "baseline": {"promotion_alerts": []},
                            "promoted_non_exact": {"promotion_alerts": ["ndcg_regressed"]},
                            "query_aware": {"promotion_alerts": []},
                        },
                    }
                ],
            },
            "dispatcher_eval": {"summary": {}},
            "dispatcher_telemetry_eval": {"summary": {}},
            "routing_telemetry_eval": {"summary": {}},
            "live_graph_goldens": {
                "ok": False,
                "skipped": True,
                "workspaces": [],
            },
        }
        summary = mod.build_enterprise_summary(payload)
        self.assertTrue(summary["live_graph_ok"])
        self.assertEqual(
            summary["retrieval_regressions"],
            [{"case_id": "docs_case", "config": "promoted_non_exact", "alerts": ["ndcg_regressed"]}],
        )

    def test_write_enterprise_artifacts_writes_latest_and_history(self):
        mod = _load_module()
        payload = {
            "enterprise_summary": {"live_graph_ok": True},
            "retrieval_eval": {"summary": {"group_representatives": {"mrr": 0.95}}},
            "dispatcher_eval": {"summary": {"final_dispatcher_selection_top_hit_rate": 1.0}},
            "dispatcher_telemetry_eval": {"summary": {"total_events": 2}},
            "routing_telemetry_eval": {"summary": {"total_events": 1}},
            "live_graph_goldens": {"ok": True, "workspaces": ["/tmp/repo"]},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            result = mod.write_enterprise_artifacts(payload, tmpdir)
            latest_path = Path(result["latest_path"])
            history_path = Path(result["history_path"])
            self.assertTrue(latest_path.exists())
            self.assertTrue(history_path.exists())
            self.assertEqual(latest_path.parent, Path(tmpdir))
            latest_payload = json.loads(latest_path.read_text())
            history_payload = json.loads(history_path.read_text())
            self.assertIn("artifact_meta", latest_payload)
            self.assertEqual(latest_payload["artifact_meta"]["timestamp"], result["timestamp"])
            self.assertEqual(history_payload["enterprise_summary"]["live_graph_ok"], True)

    def test_run_dispatcher_eval_returns_summary(self):
        mod = _load_module()
        report = mod.run_dispatcher_eval()
        self.assertIn("summary", report)
        self.assertIn("cases", report)
        self.assertIn("final_dispatcher_selection_top_hit_rate", report["summary"])

    def test_run_dispatcher_telemetry_eval_returns_summary(self):
        mod = _load_module()
        report = mod.run_dispatcher_telemetry_eval()
        self.assertIn("summary", report)
        self.assertIn("total_events", report["summary"])

    def test_run_routing_telemetry_eval_returns_summary(self):
        mod = _load_module()
        report = mod.run_routing_telemetry_eval()
        self.assertIn("summary", report)
        self.assertIn("total_events", report["summary"])

    def test_build_trend_summary_computes_metric_deltas_from_previous_latest(self):
        mod = _load_module()
        previous_payload = {
            "enterprise_summary": {
                "live_graph_ok": True,
                "best_retrieval_config": {
                    "name": "query_aware",
                    "metrics": {
                        "mrr": 0.9,
                        "ndcg": 0.95,
                        "hit_at_k": 1.0,
                        "topk_redundancy_rate": 0.3,
                    },
                },
                "retrieval_alerts": {"promoted_non_exact": ["ndcg_regressed"]},
            }
        }
        current_payload = {
            "enterprise_summary": {
                "live_graph_ok": True,
                "best_retrieval_config": {
                    "name": "group_representatives",
                    "metrics": {
                        "mrr": 0.95,
                        "ndcg": 0.97,
                        "hit_at_k": 1.0,
                        "topk_redundancy_rate": 0.25,
                    },
                },
                "retrieval_alerts": {},
            }
        }
        trend = mod.build_trend_summary(previous_payload, current_payload)
        self.assertTrue(trend["has_previous"])
        self.assertEqual(trend["previous_best_config"], "query_aware")
        self.assertEqual(trend["current_best_config"], "group_representatives")
        self.assertAlmostEqual(trend["metric_deltas"]["mrr"], 0.05)
        self.assertEqual(trend["metric_statuses"]["mrr"], "improved")
        self.assertEqual(trend["metric_statuses"]["hit_at_k"], "flat")
        self.assertEqual(trend["metric_statuses"]["topk_redundancy_rate"], "improved")
        self.assertEqual(trend["previous_alert_configs"], ["promoted_non_exact"])
        self.assertEqual(trend["current_alert_configs"], [])
        self.assertEqual(trend["overall_status"], "warning")
        self.assertEqual(trend["attention_needed"], ["best_config_changed"])

    def test_build_trend_summary_marks_healthy_when_no_attention_needed(self):
        mod = _load_module()
        previous_payload = {
            "enterprise_summary": {
                "live_graph_ok": True,
                "best_retrieval_config": {
                    "name": "group_representatives",
                    "metrics": {
                        "mrr": 0.95,
                        "ndcg": 0.97,
                        "hit_at_k": 1.0,
                        "topk_redundancy_rate": 0.25,
                    },
                },
                "retrieval_alerts": {},
                "retrieval_regressions": [],
            }
        }
        current_payload = {
            "enterprise_summary": {
                "live_graph_ok": True,
                "best_retrieval_config": {
                    "name": "group_representatives",
                    "metrics": {
                        "mrr": 0.951,
                        "ndcg": 0.971,
                        "hit_at_k": 1.0,
                        "topk_redundancy_rate": 0.249,
                    },
                },
                "retrieval_alerts": {},
                "retrieval_regressions": [],
            }
        }
        trend = mod.build_trend_summary(previous_payload, current_payload)
        self.assertEqual(trend["overall_status"], "healthy")
        self.assertEqual(trend["attention_needed"], [])

    def test_build_trend_summary_marks_regressed_for_hard_failures(self):
        mod = _load_module()
        previous_payload = {
            "enterprise_summary": {
                "live_graph_ok": True,
                "best_retrieval_config": {
                    "name": "group_representatives",
                    "metrics": {
                        "mrr": 0.95,
                        "ndcg": 0.97,
                        "hit_at_k": 1.0,
                    },
                },
                "retrieval_alerts": {},
                "retrieval_regressions": [],
            }
        }
        current_payload = {
            "enterprise_summary": {
                "live_graph_ok": False,
                "best_retrieval_config": {
                    "name": "group_representatives",
                    "metrics": {
                        "mrr": 0.92,
                        "ndcg": 0.96,
                        "hit_at_k": 0.95,
                    },
                },
                "retrieval_alerts": {"baseline": ["ndcg_regressed"]},
                "retrieval_regressions": [{"case_id": "x", "config": "baseline", "alerts": ["ndcg_regressed"]}],
            }
        }
        trend = mod.build_trend_summary(previous_payload, current_payload)
        self.assertEqual(trend["overall_status"], "regressed")
        self.assertIn("live_graph_failed", trend["attention_needed"])
        self.assertIn("retrieval_alerts_present", trend["attention_needed"])
        self.assertIn("retrieval_regressions_present", trend["attention_needed"])
        self.assertIn("mrr_regressed", trend["attention_needed"])
        self.assertIn("ndcg_regressed", trend["attention_needed"])
        self.assertIn("hit_at_k_regressed", trend["attention_needed"])

    def test_run_live_graph_goldens_retries_once_on_failure(self):
        mod = _load_module()

        failure = mock.Mock(returncode=1, stdout="bad", stderr="flaky")
        success = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(mod.subprocess, "run", side_effect=[failure, success]) as run_mock:
            result = mod.run_live_graph_goldens(["/tmp/repo"], "python")
        self.assertTrue(result["ok"])
        self.assertEqual(run_mock.call_count, 2)
        self.assertEqual(result["attempts"][0]["returncode"], 1)
        self.assertEqual(result["attempts"][1]["returncode"], 0)

    def test_run_live_graph_goldens_uses_direct_fallback_validation(self):
        mod = _load_module()

        failure = mock.Mock(returncode=1, stdout="bad", stderr="flaky")
        direct_success = mock.Mock(returncode=0)
        with mock.patch.object(mod.subprocess, "run", side_effect=[failure, failure, direct_success]) as run_mock:
            result = mod.run_live_graph_goldens(["/tmp/repo"], "python")
        self.assertTrue(result["ok"])
        self.assertTrue(result["fallback_validated"])
        self.assertEqual(run_mock.call_count, 3)
        self.assertEqual(result["attempts"][-1]["mode"], "direct_stdio_fallback")


if __name__ == "__main__":
    unittest.main()

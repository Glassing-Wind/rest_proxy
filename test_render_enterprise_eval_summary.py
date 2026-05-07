import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "scripts" / "render_enterprise_eval_summary.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("enterprise_eval_summary_renderer", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class EnterpriseEvalSummaryRenderTests(unittest.TestCase):
    def test_render_summary_includes_status_attention_and_metrics(self):
        mod = _load_module()
        payload = {
            "enterprise_summary": {
                "live_graph_ok": True,
                "best_retrieval_config": {
                    "name": "group_representatives",
                    "metrics": {
                        "mrr": 0.95,
                        "ndcg": 0.97,
                        "hit_at_k": 1.0,
                        "topk_redundancy_rate": 0.1,
                        "false_collapse_rate": 0.0,
                        "false_separation_rate": 0.0,
                    },
                },
                "dispatcher_summary": {
                    "dispatcher_anchor_contract_capability": "focused_dispatcher_anchor_v1",
                    "dispatcher_anchor_contract_version": 1,
                    "semantic_candidate_hit_rate": 0.5,
                    "semantic_candidate_contract_hit_rate": 0.5,
                    "implementation_ranking_top_hit_rate": 0.5,
                    "implementation_ranking_contract_top_hit_rate": 0.5,
                    "final_dispatcher_selection_top_hit_rate": 1.0,
                    "final_dispatcher_selection_contract_top_hit_rate": 1.0,
                    "diagnosis_counts": {
                        "ranking_fixed": 2,
                    },
                },
                "dispatcher_telemetry_summary": {
                    "total_events": 3,
                    "contract_eligible_events": 2,
                    "ignored_no_signal_events": 1,
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
                "dispatcher_telemetry_recent_summary": {
                    "contract_eligible_events": 2,
                    "rescue_applied_rate": 0.5000,
                    "semantic_top_contract_hit_rate": 0.5000,
                    "implementation_ranking_top_contract_hit_rate": 1.0000,
                    "final_top_contract_hit_rate": 1.0000,
                    "diagnosis_counts": {
                        "ranking_surfaces_contract": 1,
                        "semantic_recall_missing_contract_candidate": 1,
                    },
                },
                "retrieval_query_class_counts": {"usage_lookup": 2, "implementation_search": 3},
            },
            "trend_summary": {
                "overall_status": "warning",
                "attention_needed": ["best_config_changed"],
                "metric_deltas": {"mrr": 0.01, "ndcg": 0.02},
                "metric_statuses": {"mrr": "improved", "ndcg": "improved"},
            },
        }
        text = mod.render_summary(payload)
        self.assertIn("Overall status: `warning`", text)
        self.assertIn("Attention needed: `best_config_changed`", text)
        self.assertIn("`group_representatives`", text)
        self.assertIn("| `mrr` | 0.9500 | 0.0100 | `improved` |", text)
        self.assertIn("### Dispatcher Eval", text)
        self.assertIn("Semantic candidate hit rate: `0.5000`", text)
        self.assertIn("Semantic candidate contract hit rate: `0.5000`", text)
        self.assertIn("Final dispatcher selection top-hit rate: `1.0000`", text)
        self.assertIn("Final dispatcher selection contract top-hit rate: `1.0000`", text)
        self.assertIn("Dispatcher anchor contract: `focused_dispatcher_anchor_v1` (version `1`)", text)
        self.assertIn("### Live Dispatcher Telemetry", text)
        self.assertIn("Total events: `3`", text)
        self.assertIn("Contract-eligible events: `2`", text)
        self.assertIn("Ignored no-signal events: `1`", text)
        self.assertIn("Rescue applied rate: `0.3333`", text)
        self.assertIn("Live diagnosis `semantic_recall_missing_contract_candidate`: 2", text)
        self.assertIn("### Recent Dispatcher Telemetry", text)
        self.assertIn("Recent contract-eligible events: `2`", text)
        self.assertIn("Recent final top contract-hit rate: `1.0000`", text)
        self.assertIn("Recent diagnosis `ranking_surfaces_contract`: 1", text)
        self.assertIn("- `implementation_search`: 3", text)

    def test_render_pr_comment_includes_marker_and_alert_header(self):
        mod = _load_module()
        payload = {
            "enterprise_summary": {
                "live_graph_ok": False,
                "best_retrieval_config": {"name": "baseline", "metrics": {"mrr": 0.7}},
                "retrieval_query_class_counts": {},
            },
            "trend_summary": {
                "overall_status": "regressed",
                "attention_needed": ["mrr_regressed"],
                "metric_deltas": {"mrr": -0.1},
                "metric_statuses": {"mrr": "regressed"},
            },
        }
        text = mod.render_pr_comment(payload)
        self.assertIn("<!-- enterprise-eval-comment -->", text)
        self.assertIn("## Enterprise Eval Alert", text)
        self.assertIn("Status: `regressed`", text)
        self.assertIn("Overall status: `regressed`", text)


if __name__ == "__main__":
    unittest.main()

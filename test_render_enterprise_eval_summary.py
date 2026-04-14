import importlib.util
import unittest


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/scripts/render_enterprise_eval_summary.py"


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
        self.assertIn("- `implementation_search`: 3", text)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/scripts/run_enterprise_eval.py"


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


if __name__ == "__main__":
    unittest.main()

import importlib.util
import unittest
from pathlib import Path

from _semantic_contract import (
    FOCUSED_DISPATCHER_ANCHOR_CAPABILITY,
    FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION,
)


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "dispatcher_eval.py"


def load_eval_module():
    spec = importlib.util.spec_from_file_location("dispatcher_eval_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DispatcherEvalTests(unittest.TestCase):
    def test_evaluation_reports_stage_metrics(self):
        module = load_eval_module()
        report = module.evaluate_benchmarks()
        self.assertIn("summary", report)
        self.assertIn("cases", report)
        self.assertEqual(
            report["summary"]["dispatcher_anchor_contract_capability"],
            FOCUSED_DISPATCHER_ANCHOR_CAPABILITY,
        )
        self.assertEqual(
            report["summary"]["dispatcher_anchor_contract_version"],
            FOCUSED_DISPATCHER_ANCHOR_CONTRACT_VERSION,
        )
        self.assertGreaterEqual(report["summary"]["semantic_candidate_hit_rate"], 0.0)
        self.assertGreaterEqual(report["summary"]["semantic_candidate_contract_hit_rate"], 0.0)
        self.assertGreaterEqual(report["summary"]["final_dispatcher_selection_top_hit_rate"], 0.0)

    def test_ranking_case_attributes_success_to_implementation_ranking(self):
        module = load_eval_module()
        report = module.evaluate_benchmarks()
        case = next(case for case in report["cases"] if case["id"] == "dispatcher_ranking_promotes_canonical_models_init")
        self.assertTrue(case["stages"]["semantic_candidates"]["candidate_hit"])
        self.assertTrue(case["stages"]["semantic_candidates"]["contract_candidate_hit"])
        self.assertFalse(case["stages"]["semantic_candidates"]["top_hit"])
        self.assertTrue(case["stages"]["implementation_ranking"]["top_hit"])
        self.assertTrue(case["stages"]["implementation_ranking"]["contract_top_hit"])
        self.assertEqual(
            case["dispatcher_anchor_contract"]["capability"],
            FOCUSED_DISPATCHER_ANCHOR_CAPABILITY,
        )
        self.assertEqual(case["first_success_stage"], "implementation_ranking")
        self.assertEqual(case["diagnosis"], "ranking_fixed")

    def test_final_promotion_case_attributes_current_contract_gap(self):
        module = load_eval_module()
        report = module.evaluate_benchmarks()
        case = next(
            case
            for case in report["cases"]
            if case["id"] == "dispatcher_final_promotion_surfaces_canonical_candidate"
        )
        self.assertTrue(case["stages"]["semantic_candidates"]["candidate_hit"])
        self.assertTrue(case["stages"]["semantic_candidates"]["contract_candidate_hit"])
        self.assertFalse(case["stages"]["implementation_ranking"]["top_hit"])
        self.assertTrue(case["stages"]["final_dispatcher_selection"]["top_hit"])
        self.assertTrue(case["stages"]["final_dispatcher_selection"]["contract_top_hit"])
        self.assertFalse(case["rescue_applied"])
        self.assertEqual(case["first_success_stage"], "final_dispatcher_selection")
        self.assertEqual(case["diagnosis"], "final_promotion_needed")


if __name__ == "__main__":
    unittest.main()

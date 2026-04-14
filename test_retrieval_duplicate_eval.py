import json
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "duplicate_eval.py"


def _run_eval_in_lmproxy() -> dict:
    command = [
        "conda",
        "run",
        "-n",
        "lmproxy",
        "python",
        "-c",
        (
            "import importlib.util, json; "
            f"spec = importlib.util.spec_from_file_location('duplicate_eval_under_test', '{MODULE_PATH}'); "
            "mod = importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(mod); "
            "print(json.dumps(mod.evaluate_benchmarks()))"
        ),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


class RetrievalDuplicateEvalTests(unittest.TestCase):
    def test_evaluation_reports_expected_configs(self):
        report = _run_eval_in_lmproxy()
        self.assertIn("summary", report)
        self.assertIn("cases", report)
        self.assertIn("query_aware", report["summary"])
        self.assertIn("narrow_non_exact_experiments", report["summary"])
        self.assertIn("promoted_non_exact", report["summary"])
        self.assertIn("by_mode", report)
        self.assertIn("alerts", report)

    def test_query_aware_keeps_best_answers_and_reduces_repetition(self):
        report = _run_eval_in_lmproxy()
        summary = report["summary"]
        self.assertGreaterEqual(summary["query_aware"]["hit_at_k"], summary["baseline"]["hit_at_k"])
        self.assertLessEqual(
            summary["query_aware"]["topk_redundancy_rate"],
            summary["baseline"]["topk_redundancy_rate"],
        )

    def test_docs_case_prefers_canonical_source(self):
        report = _run_eval_in_lmproxy()
        docs_case = next(case for case in report["cases"] if case["id"] == "docs_canonical_mirror_preferred")
        query_aware = docs_case["configs"]["query_aware"]
        self.assertEqual(query_aware["top_k"][0], 0)
        narrow = docs_case["configs"]["narrow_non_exact_experiments"]
        self.assertEqual(narrow["top_k"][0], 0)

    def test_code_case_preserves_query_distinct_result(self):
        report = _run_eval_in_lmproxy()
        code_case = next(case for case in report["cases"] if case["id"] == "code_renamed_helper_clones")
        top = code_case["configs"]["query_aware"]["top_k"]
        self.assertIn(2, top)
        self.assertIn(0, top)

    def test_promoted_non_exact_docs_case_still_keeps_canonical_top_result(self):
        report = _run_eval_in_lmproxy()
        docs_case = next(case for case in report["cases"] if case["id"] == "docs_canonical_mirror_preferred")
        promoted = docs_case["configs"]["promoted_non_exact"]
        self.assertEqual(promoted["top_k"][0], 0)
        self.assertNotIn("hit_at_k_regressed", promoted["promotion_alerts"])
        self.assertNotIn("best_answer_retention_regressed", promoted["promotion_alerts"])

    def test_exact_duplicate_case_does_not_raise_ndcg_alert_when_redundancy_improves(self):
        report = _run_eval_in_lmproxy()
        case = next(case for case in report["cases"] if case["id"] == "code_exact_duplicate_helpers")
        promoted = case["configs"]["group_representatives"]
        self.assertNotIn("ndcg_regressed", promoted["promotion_alerts"])

    def test_small_ndcg_shift_without_other_regressions_does_not_alert(self):
        report = _run_eval_in_lmproxy()
        case = next(case for case in report["cases"] if case["id"] == "docs_prose_near_duplicates_do_not_overcollapse")
        query_aware = case["configs"]["query_aware"]
        self.assertNotIn("ndcg_regressed", query_aware["promotion_alerts"])

    def test_docs_canonical_mirror_suppression_does_not_alert_when_canonical_and_version_survive(self):
        report = _run_eval_in_lmproxy()
        case = next(case for case in report["cases"] if case["id"] == "docs_canonical_mirror_preferred")
        promoted = case["configs"]["promoted_non_exact"]
        self.assertTrue(promoted["canonical_doc_preference_success"])
        self.assertTrue(promoted["version_sensitive_doc_retention"])
        self.assertNotIn("ndcg_regressed", promoted["promotion_alerts"])


if __name__ == "__main__":
    unittest.main()

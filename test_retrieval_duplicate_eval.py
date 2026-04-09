import json
import subprocess
import unittest


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/duplicate_eval.py"


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


if __name__ == "__main__":
    unittest.main()

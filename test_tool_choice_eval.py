import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tool_choice_eval import evaluate_tool_choice


class ToolChoiceEvalTests(unittest.TestCase):
    def test_preferred_first_tool_scores_healthy(self):
        report = evaluate_tool_choice("repo_onboarding", ["get_project_overview", "search_codebase"])
        self.assertEqual("healthy", report["status"])
        self.assertTrue(report["first_tool_preferred"])
        self.assertGreaterEqual(report["score"], 1.0)

    def test_fallback_first_tool_scores_warning(self):
        report = evaluate_tool_choice("repo_onboarding", ["describe_file", "get_project_overview"])
        self.assertEqual("warning", report["status"])
        self.assertTrue(report["first_tool_fallback"])
        self.assertIn("preferred first tool", " ".join(report["guidance"]).lower())

    def test_avoided_first_tool_scores_regressed(self):
        report = evaluate_tool_choice("repo_onboarding", ["query_graph", "get_project_overview"])
        self.assertEqual("regressed", report["status"])
        self.assertTrue(report["first_tool_avoided"])

    def test_cli_script_emits_json(self):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "evaluate_tool_choice.py"),
                "symbol_deep_dive",
                "get_symbol_context",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual("symbol_deep_dive", payload["id"])
        self.assertEqual("healthy", payload["status"])


if __name__ == "__main__":
    unittest.main()

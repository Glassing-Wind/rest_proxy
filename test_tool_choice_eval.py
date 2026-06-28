import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tool_choice_eval import (  # noqa: E402
    evaluate_catalog_tool_choice,
    evaluate_tool_choice,
    extract_catalog_tools,
)


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

    def test_secondary_architecture_tools_do_not_start_onboarding(self):
        for tool_name in ("get_code_importance", "get_code_communities"):
            with self.subTest(tool_name=tool_name):
                report = evaluate_tool_choice(
                    "repo_onboarding", [tool_name, "get_project_overview"]
                )
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

    def test_extract_catalog_tools_preserves_order(self):
        rendered = "\n".join(
            [
                "MCP tool catalog:",
                "- `search_codebase` [primary / code investigation] - Search.",
                "- `grep_codebase` [support / code search] - Grep.",
            ]
        )
        self.assertEqual(
            ["search_codebase", "grep_codebase"],
            extract_catalog_tools(rendered),
        )

    def test_catalog_output_scores_against_goldens(self):
        for case_id in (
            "repo_onboarding",
            "documentation_lookup",
            "documentation_inventory",
            "changed_code_review",
            "retrieval_ranking_debug",
        ):
            with self.subTest(case_id=case_id):
                report = evaluate_catalog_tool_choice(case_id)
                self.assertEqual("healthy", report["status"], report["catalog_output"])
                self.assertTrue(report["first_tool_preferred"])


if __name__ == "__main__":
    unittest.main()

import unittest

from scripts.run_mcp_investigation_pass import (
    TrustHesitation,
    _check_tool_choice,
    _load_workflows,
    _require_output,
    _run_trust_checks,
    _select_workflows,
)


class McpInvestigationPassTests(unittest.TestCase):
    def test_fixture_loads_preferred_stack_workflow(self):
        workflows = _load_workflows()
        workflow_ids = {workflow["id"] for workflow in workflows}

        self.assertIn("rest_proxy_preferred_investigation_stack", workflow_ids)

    def test_unknown_workflow_fails_clearly(self):
        with self.assertRaisesRegex(ValueError, "Unknown workflow id"):
            _select_workflows(_load_workflows(), ["not_a_workflow"])

    def test_missing_expected_output_reports_first_hesitation(self):
        step = {
            "name": "Search for implementation",
            "tool": "search_codebase",
            "required_substrings": ["real_surface.py"],
        }

        with self.assertRaises(TrustHesitation) as ctx:
            _require_output(
                workflow_id="wf",
                step=step,
                tool_name="search_codebase",
                output="only docs.md",
            )

        self.assertEqual("wf", ctx.exception.workflow_id)
        self.assertEqual("Search for implementation", ctx.exception.step_name)
        self.assertEqual("search_codebase", ctx.exception.tool_name)
        self.assertIn("missing expected evidence", ctx.exception.reason)
        self.assertIn("FIRST TRUST HESITATION", ctx.exception.format())

    def test_forbidden_output_reports_hesitation(self):
        step = {
            "name": "Catalog",
            "tool": "get_mcp_tool_catalog",
            "forbidden_substrings": ["`query_graph`"],
        }

        with self.assertRaisesRegex(TrustHesitation, "hit forbidden evidence"):
            _require_output(
                workflow_id="wf",
                step=step,
                tool_name="get_mcp_tool_catalog",
                output="- `query_graph`",
            )

    def test_tool_choice_case_uses_catalog_output(self):
        step = {
            "name": "Catalog repo onboarding",
            "tool": "get_mcp_tool_catalog",
            "tool_choice_case": "repo_onboarding",
        }
        output = "\n".join(
            [
                "MCP tool catalog for `indexed repo start`:",
                "- `query_graph` - raw graph query.",
                "- `get_project_overview` - summarize architecture.",
            ]
        )

        with self.assertRaisesRegex(TrustHesitation, "tool-choice regression"):
            _check_tool_choice(
                workflow_id="wf",
                step=step,
                tool_name="get_mcp_tool_catalog",
                output=output,
            )

    def test_trust_check_requires_any_expected_value(self):
        step = {
            "name": "Health",
            "tool": "get_indexing_health",
            "trust_checks": [
                {
                    "kind": "contains_any",
                    "label": "sync healthy",
                    "values": ["**Sync Status**: Healthy"],
                }
            ],
        }

        with self.assertRaisesRegex(TrustHesitation, "sync healthy"):
            _run_trust_checks(
                workflow_id="wf",
                step=step,
                tool_name="get_indexing_health",
                output="**Sync Status**: Stale",
            )


if __name__ == "__main__":
    unittest.main()

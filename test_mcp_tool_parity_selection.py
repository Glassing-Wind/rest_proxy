import unittest
from unittest import mock

from scripts.check_mcp_tool_parity import _normalized_expected_variants, _select_parity_inputs


class McpToolParitySelectionTests(unittest.TestCase):
    def test_branch_contract_uses_current_native_branch(self):
        with mock.patch('scripts.check_mcp_tool_parity.subprocess.check_output',
                        return_value='codex/example\n'):
            self.assertIn('**Branch:** `codex/example`',
                          _normalized_expected_variants('**Branch:** `$current_branch`'))

    def test_selects_direct_parity_case_id(self):
        checks, cases = _select_parity_inputs(["mcp_tool_catalog_natural_docs_intent"])

        self.assertEqual(["mcp_tool_catalog_natural_docs_intent"], [c["id"] for c in checks])
        self.assertEqual([], cases)

    def test_selects_graph_golden_case_id(self):
        checks, cases = _select_parity_inputs(["rest_proxy_backend_flow_summary"])

        self.assertEqual([], checks)
        self.assertEqual(["rest_proxy_backend_flow_summary"], [c["id"] for c in cases])

    def test_selects_mixed_case_ids(self):
        checks, cases = _select_parity_inputs(
            [
                "mcp_tool_catalog_natural_docs_intent",
                "rest_proxy_backend_flow_summary",
            ]
        )

        self.assertEqual(["mcp_tool_catalog_natural_docs_intent"], [c["id"] for c in checks])
        self.assertEqual(["rest_proxy_backend_flow_summary"], [c["id"] for c in cases])

    def test_unknown_case_id_still_fails(self):
        with self.assertRaisesRegex(AssertionError, "Unknown case ids"):
            _select_parity_inputs(["not_a_real_case"])


if __name__ == "__main__":
    unittest.main()

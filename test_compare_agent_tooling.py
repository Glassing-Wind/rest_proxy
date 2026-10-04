import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "scripts" / "compare_agent_tooling.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("compare_agent_tooling_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _result(condition: str, case_ids: list[str]) -> dict:
    return {
        "schema_version": 1,
        "condition": condition,
        "repository": "demo",
        "index_state": "not_applicable" if condition == "native" else "warm",
        "setup_seconds": 0 if condition == "native" else 12,
        "runs": [
            {
                "case_id": case_id,
                "correct": True,
                "unsupported_claims": 0,
                "elapsed_seconds": 10 if condition == "native" else 5,
                "tool_calls": 4 if condition == "native" else 2,
                "files_opened": 3 if condition == "native" else 1,
                "input_tokens": 100,
                "output_tokens": 50,
                "evidence": [f"{case_id}.py:10 expected_symbol"],
            }
            for case_id in case_ids
        ],
    }


class AgentToolingComparisonTests(unittest.TestCase):
    def test_compare_reports_paired_efficiency_and_evidence(self):
        mod = _load_module()
        cases = {
            "cases": [
                {"id": "alpha", "expected_evidence": ["alpha.py", "expected_symbol"]},
                {"id": "beta", "expected_evidence": ["beta.py"]},
            ]
        }

        report = mod.compare_results(_result("native", ["alpha", "beta"]), _result("mcp", ["alpha", "beta"]), cases)

        self.assertEqual(report["case_count"], 2)
        self.assertEqual(report["native"]["correct_rate"], 1.0)
        self.assertEqual(report["mcp"]["evidence_coverage"], 1.0)
        self.assertEqual(report["paired_cases"][0]["elapsed_seconds_delta"], -5)

    def test_compare_rejects_missing_case(self):
        mod = _load_module()
        cases = {"cases": [{"id": "alpha", "expected_evidence": []}, {"id": "beta", "expected_evidence": []}]}

        with self.assertRaisesRegex(ValueError, "every benchmark case"):
            mod.compare_results(_result("native", ["alpha"]), _result("mcp", ["alpha", "beta"]), cases)

    def test_committed_schema_accepts_a_valid_result(self):
        mod = _load_module()
        schema = json.loads((REPO_ROOT / "benchmarks" / "agent_tooling_result.schema.json").read_text())

        mod._validate_result(_result("native", ["alpha"]), schema, "native")

    def test_compare_rejects_different_revisions_and_empty_cases(self):
        mod = _load_module()
        native = _result("native", ["alpha"])
        mcp = _result("mcp", ["alpha"])
        native["revision"] = "abc"
        with self.assertRaisesRegex(ValueError, "revisions"):
            mod.compare_results(native, mcp, {"cases": [{"id": "alpha"}]})
        mcp["revision"] = "abc"
        with self.assertRaisesRegex(ValueError, "empty"):
            mod.compare_results(native, mcp, {"cases": []})

    def test_report_discloses_estimates_fallbacks_and_all_pairs(self):
        mod = _load_module()
        native = _result("native", ["alpha", "beta"])
        mcp = _result("mcp", ["alpha", "beta"])
        for payload in [native, mcp]:
            payload["token_measurement"] = "estimated"
            payload["measurement_notes"] = "Tool latency only; chars/4 tokens."
        mcp["runs"][0]["native_fallback_calls"] = 2
        report = mod.compare_results(native, mcp, {"cases": [{"id": "alpha"}, {"id": "beta"}]})
        rendered = mod.render_markdown(report)
        self.assertIn("do not establish model token savings", rendered)
        self.assertIn("MCP native fallback calls: 2", rendered)
        self.assertIn("| alpha |", rendered)
        self.assertIn("| beta |", rendered)

    def test_mixed_timing_methods_never_emit_a_speed_delta(self):
        mod = _load_module()
        native = _result("native", ["alpha"])
        mcp = _result("mcp", ["alpha"])
        native["elapsed_measurement"] = "end_to_end"
        mcp["elapsed_measurement"] = "tool_latency"
        report = mod.compare_results(native, mcp, {"cases": [{"id": "alpha"}]})
        self.assertFalse(report["elapsed_comparable"])
        self.assertIsNone(report["paired_cases"][0]["elapsed_seconds_delta"])
        self.assertIn("N/A", mod.render_markdown(report))

    def test_cli_emits_markdown(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases_path = root / "cases.json"
            native_path = root / "native.json"
            mcp_path = root / "mcp.json"
            cases_path.write_text(json.dumps({"cases": [{"id": "alpha", "expected_evidence": ["alpha.py"]}]}))
            native_path.write_text(json.dumps(_result("native", ["alpha"])))
            mcp_path.write_text(json.dumps(_result("mcp", ["alpha"])))
            old_argv = mod.sys.argv
            mod.sys.argv = [
                "compare_agent_tooling.py",
                "--native", str(native_path),
                "--mcp", str(mcp_path),
                "--cases", str(cases_path),
            ]
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(mod.main(), 0)
                self.assertIn("# Native vs MCP Agent Tooling", output.getvalue())
            finally:
                mod.sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()

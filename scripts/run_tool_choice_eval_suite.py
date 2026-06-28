#!/usr/bin/env python3
"""Run canonical intent-to-tool workflow checks."""

from __future__ import annotations

import json
import os
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tool_choice_eval import evaluate_catalog_tool_choice, evaluate_tool_choice  # noqa: E402


CANONICAL_CASES = [
    ("repo_onboarding", ["get_project_overview", "search_codebase"], "healthy"),
    ("symbol_deep_dive", ["get_symbol_context"], "healthy"),
    ("execution_path", ["get_call_chain", "find_references"], "healthy"),
    ("code_search_general", ["search_codebase", "find_references"], "healthy"),
    ("definition_disambiguation", ["find_definitions", "search_codebase"], "healthy"),
    ("graph_suspect_result", ["trace_graph_provenance", "get_symbol_context"], "healthy"),
    ("documentation_lookup", ["search_documentation"], "healthy"),
    ("documentation_inventory", ["list_documentation_sources"], "healthy"),
    ("memory_capture", ["add_memory"], "healthy"),
    ("memory_review", ["list_memories"], "healthy"),
    ("changed_code_review", ["git_summary", "get_changed_symbols"], "healthy"),
    ("coverage_lookup", ["get_test_coverage_for"], "healthy"),
    ("retrieval_ranking_debug", ["trace_code_ranking"], "healthy"),
]


def main() -> int:
    reports = []
    catalog_reports = []
    failures = []
    for case_id, proposed_tools, expected_status in CANONICAL_CASES:
        report = evaluate_tool_choice(case_id, proposed_tools)
        report["expected_status"] = expected_status
        reports.append(report)
        if report["status"] != expected_status:
            failures.append(report)

        catalog_report = evaluate_catalog_tool_choice(case_id)
        catalog_report["expected_status"] = "healthy"
        catalog_reports.append(catalog_report)
        if catalog_report["status"] != "healthy":
            failures.append(catalog_report)

    if not failures:
        for report in catalog_reports:
            report.pop("catalog_output", None)

    payload = {
        "cases": reports,
        "catalog_cases": catalog_reports,
        "failures": failures,
        "ok": not failures,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

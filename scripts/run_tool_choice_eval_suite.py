#!/usr/bin/env python3
"""Run canonical intent-to-tool workflow checks."""

from __future__ import annotations

import json
import os
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tool_choice_eval import evaluate_tool_choice


CANONICAL_CASES = [
    ("repo_onboarding", ["get_project_overview", "search_codebase"], "healthy"),
    ("symbol_deep_dive", ["get_symbol_context"], "healthy"),
    ("execution_path", ["get_call_chain", "find_references"], "healthy"),
    ("code_search_general", ["search_codebase", "find_references"], "healthy"),
    ("definition_disambiguation", ["find_definitions", "search_codebase"], "healthy"),
    ("graph_suspect_result", ["trace_graph_provenance", "get_symbol_context"], "healthy"),
]


def main() -> int:
    reports = []
    failures = []
    for case_id, proposed_tools, expected_status in CANONICAL_CASES:
        report = evaluate_tool_choice(case_id, proposed_tools)
        report["expected_status"] = expected_status
        reports.append(report)
        if report["status"] != expected_status:
            failures.append(report)

    payload = {"cases": reports, "failures": failures, "ok": not failures}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

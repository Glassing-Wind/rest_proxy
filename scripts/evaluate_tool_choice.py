#!/usr/bin/env python3
"""Evaluate whether a proposed tool workflow matches the preferred tool-choice baseline."""

from __future__ import annotations

import argparse
import json
import os
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tool_choice_eval import evaluate_tool_choice


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate an intent-to-tool workflow.")
    parser.add_argument("case_id", help="Tool-choice case id from benchmarks/tool_choice_goldens.json")
    parser.add_argument("tools", nargs="*", help="Ordered list of proposed tools")
    args = parser.parse_args()

    report = evaluate_tool_choice(args.case_id, args.tools)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

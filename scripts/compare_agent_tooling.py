#!/usr/bin/env python3
"""Compare paired native-tool and GraphRAG MCP repository investigations."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = REPO_ROOT / "benchmarks" / "agent_tooling_cases.json"
DEFAULT_SCHEMA = REPO_ROOT / "benchmarks" / "agent_tooling_result.schema.json"
METRICS = (
    "elapsed_seconds",
    "tool_calls",
    "files_opened",
    "input_tokens",
    "output_tokens",
    "unsupported_claims",
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _validate_result(payload: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors[:8]
        )
        raise ValueError(f"invalid {label} result: {details}")


def _runs_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for run in payload["runs"]:
        case_id = run["case_id"]
        if case_id in indexed:
            raise ValueError(f"duplicate case_id {case_id!r} in {payload['condition']} result")
        indexed[case_id] = run
    return indexed


def _evidence_coverage(run: dict[str, Any], expected: list[str]) -> float:
    if not expected:
        return 1.0
    haystack = "\n".join([run.get("answer", ""), *run.get("evidence", [])]).lower()
    matched = sum(1 for item in expected if item.lower() in haystack)
    return matched / len(expected)


def compare_results(
    native: dict[str, Any],
    mcp: dict[str, Any],
    cases_payload: dict[str, Any],
) -> dict[str, Any]:
    if native["condition"] != "native" or mcp["condition"] != "mcp":
        raise ValueError("--native must contain condition=native and --mcp condition=mcp")
    if native["repository"] != mcp["repository"]:
        raise ValueError("result repository values do not match")
    if native.get("revision") != mcp.get("revision"):
        raise ValueError("result revisions do not match")
    elapsed_comparable = native.get("elapsed_measurement") == mcp.get("elapsed_measurement")
    if not cases_payload.get("cases"):
        raise ValueError("benchmark cases must not be empty")

    native_runs = _runs_by_id(native)
    mcp_runs = _runs_by_id(mcp)
    case_ids = [case["id"] for case in cases_payload.get("cases", [])]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("benchmark cases contain duplicate ids")
    expected_ids = set(case_ids)
    if set(native_runs) != expected_ids or set(mcp_runs) != expected_ids:
        raise ValueError("native and MCP results must each contain every benchmark case exactly once")

    expected_by_id = {
        case["id"]: [str(item) for item in case.get("expected_evidence", [])]
        for case in cases_payload["cases"]
    }

    def summarize(runs: dict[str, dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
        ordered = [runs[case_id] for case_id in case_ids]
        summary: dict[str, Any] = {
            "correct_rate": sum(bool(run["correct"]) for run in ordered) / len(ordered),
            "evidence_coverage": statistics.fmean(
                _evidence_coverage(run, expected_by_id[run["case_id"]]) for run in ordered
            ),
            "setup_seconds": payload["setup_seconds"],
            "index_state": payload["index_state"],
            "token_measurement": payload.get("token_measurement", "unspecified"),
            "elapsed_measurement": payload.get("elapsed_measurement", "unspecified"),
            "measurement_notes": payload.get("measurement_notes", ""),
            "native_fallback_calls": sum(run.get("native_fallback_calls", 0) for run in ordered),
        }
        for metric in METRICS:
            values = [float(run[metric]) for run in ordered]
            summary[f"mean_{metric}"] = statistics.fmean(values)
            summary[f"total_{metric}"] = sum(values)
        return summary

    native_summary = summarize(native_runs, native)
    mcp_summary = summarize(mcp_runs, mcp)
    paired = []
    for case_id in case_ids:
        native_run = native_runs[case_id]
        mcp_run = mcp_runs[case_id]
        paired.append(
            {
                "case_id": case_id,
                "native_correct": native_run["correct"],
                "mcp_correct": mcp_run["correct"],
                "native_evidence_coverage": _evidence_coverage(native_run, expected_by_id[case_id]),
                "mcp_evidence_coverage": _evidence_coverage(mcp_run, expected_by_id[case_id]),
                "elapsed_seconds_delta": (
                    mcp_run["elapsed_seconds"] - native_run["elapsed_seconds"]
                    if elapsed_comparable else None
                ),
                "tool_calls_delta": mcp_run["tool_calls"] - native_run["tool_calls"],
                "total_tokens_delta": (
                    mcp_run["input_tokens"]
                    + mcp_run["output_tokens"]
                    - native_run["input_tokens"]
                    - native_run["output_tokens"]
                ),
            }
        )

    return {
        "schema_version": 1,
        "repository": native["repository"],
        "revision": native.get("revision"),
        "elapsed_comparable": elapsed_comparable,
        "case_count": len(case_ids),
        "native": native_summary,
        "mcp": mcp_summary,
        "paired_cases": paired,
    }


def render_markdown(report: dict[str, Any]) -> str:
    native = report["native"]
    mcp = report["mcp"]
    lines = [
        "# Native vs MCP Agent Tooling",
        "",
        f"Repository: `{report['repository']}` · Cases: {report['case_count']}",
        "",
        "| Metric | Native | MCP |",
        "|---|---:|---:|",
        f"| Recorded correct (see grading notes) | {native['correct_rate']:.1%} | {mcp['correct_rate']:.1%} |",
        f"| Evidence coverage | {native['evidence_coverage']:.1%} | {mcp['evidence_coverage']:.1%} |",
        f"| Mean recorded seconds ({'same method' if report['elapsed_comparable'] else 'NOT comparable'}) | "
        f"{native['mean_elapsed_seconds']:.2f} | {mcp['mean_elapsed_seconds']:.2f} |",
        f"| Mean tool calls | {native['mean_tool_calls']:.2f} | {mcp['mean_tool_calls']:.2f} |",
        f"| Mean files opened | {native['mean_files_opened']:.2f} | {mcp['mean_files_opened']:.2f} |",
        f"| Recorded tokens (see measurement method) | {native['total_input_tokens'] + native['total_output_tokens']:.0f} | "
        f"{mcp['total_input_tokens'] + mcp['total_output_tokens']:.0f} |",
        f"| Unsupported claims | {native['total_unsupported_claims']:.0f} | "
        f"{mcp['total_unsupported_claims']:.0f} |",
        f"| Setup/index seconds | {native['setup_seconds']:.2f} | {mcp['setup_seconds']:.2f} |",
        "",
        f"MCP index state: `{mcp['index_state']}`. Setup/index time is excluded from per-case elapsed time.",
        "",
        f"Token measurement: native `{native['token_measurement']}`, MCP `{mcp['token_measurement']}`. "
        "Estimated or unspecified values do not establish model token savings.",
        f"MCP native fallback calls: {mcp['native_fallback_calls']}.",
        f"Elapsed measurement: native `{native['elapsed_measurement']}`, MCP `{mcp['elapsed_measurement']}`; "
        "tool latency excludes agent reasoning. Differing methods suppress timing deltas.",
        "",
        f"Native measurement notes: {native['measurement_notes'] or 'Not supplied.'}",
        f"MCP measurement notes: {mcp['measurement_notes'] or 'Not supplied.'}",
        "",
        "Evidence coverage is keyword overlap, not independent verification of correctness.",
        "",
        "| Case | Native correct | MCP correct | Native evidence | MCP evidence | Seconds delta (MCP − native) | Calls delta | Tokens delta |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for case in report["paired_cases"]:
        elapsed_delta = case["elapsed_seconds_delta"]
        elapsed_text = "N/A" if elapsed_delta is None else f"{elapsed_delta:+.2f}"
        lines.append(
            f"| {case['case_id']} | {case['native_correct']} | {case['mcp_correct']} | "
            f"{case['native_evidence_coverage']:.0%} | {case['mcp_evidence_coverage']:.0%} | "
            f"{elapsed_text} | {case['tool_calls_delta']:+d} | "
            f"{case['total_tokens_delta']:+d} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--mcp", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()

    try:
        schema = _load_json(args.schema)
        native = _load_json(args.native)
        mcp = _load_json(args.mcp)
        _validate_result(native, schema, "native")
        _validate_result(mcp, schema, "MCP")
        report = compare_results(native, mcp, _load_json(args.cases))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"benchmark comparison failed: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

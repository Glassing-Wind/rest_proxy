"""Intent-to-tool evaluation helpers for preferred GraphRAG workflows."""

from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
GOLDENS_PATH = REPO_ROOT / "benchmarks" / "tool_choice_goldens.json"


def load_tool_choice_goldens() -> dict:
    return json.loads(GOLDENS_PATH.read_text(encoding="utf-8"))


def get_tool_choice_case(case_id: str) -> dict:
    payload = load_tool_choice_goldens()
    for case in payload.get("cases", []):
        if case.get("id") == case_id:
            return case
    raise KeyError(f"Unknown tool-choice case: {case_id}")


def evaluate_tool_choice(case_id: str, proposed_tools: list[str]) -> dict:
    case = get_tool_choice_case(case_id)
    proposed = [tool for tool in proposed_tools if tool]
    preferred = case.get("preferred_tools", [])
    fallbacks = case.get("acceptable_fallbacks", [])
    avoided = set(case.get("avoid_as_primary", []))

    first_tool = proposed[0] if proposed else None
    preferred_used = [tool for tool in proposed if tool in preferred]
    fallback_used = [tool for tool in proposed if tool in fallbacks]
    avoided_used = [tool for tool in proposed if tool in avoided]

    first_tool_preferred = first_tool in preferred if first_tool else False
    first_tool_fallback = first_tool in fallbacks if first_tool else False
    first_tool_avoided = first_tool in avoided if first_tool else False

    score = 0.0
    if first_tool_preferred:
        score = 1.0
    elif first_tool_fallback:
        score = 0.65
    elif first_tool_avoided:
        score = 0.0
    elif preferred_used:
        score = 0.5
    elif fallback_used:
        score = 0.35
    elif proposed:
        score = 0.2

    if first_tool_avoided:
        status = "regressed"
    elif first_tool_preferred:
        status = "healthy"
    elif preferred_used or first_tool_fallback:
        status = "warning"
    else:
        status = "regressed"

    guidance = []
    if not proposed:
        guidance.append("No tools proposed.")
    elif first_tool_avoided:
        guidance.append(
            f"Do not start with `{first_tool}` for this intent. Prefer `{preferred[0]}`."
        )
    elif first_tool_fallback and preferred:
        guidance.append(
            f"`{first_tool}` is an acceptable fallback, but `{preferred[0]}` is the preferred first tool."
        )
    elif not first_tool_preferred and preferred:
        guidance.append(f"Preferred first tool: `{preferred[0]}`.")

    return {
        "id": case["id"],
        "intent": case["intent"],
        "status": status,
        "score": score,
        "first_tool": first_tool,
        "first_tool_preferred": first_tool_preferred,
        "first_tool_fallback": first_tool_fallback,
        "first_tool_avoided": first_tool_avoided,
        "preferred_tools": preferred,
        "acceptable_fallbacks": fallbacks,
        "avoid_as_primary": list(avoided),
        "proposed_tools": proposed,
        "preferred_used": preferred_used,
        "fallback_used": fallback_used,
        "avoided_used": avoided_used,
        "guidance": guidance,
    }


def extract_catalog_tools(rendered_catalog: str) -> list[str]:
    """Extract ordered tool names from `get_mcp_tool_catalog` text output."""

    return re.findall(r"^- `([^`]+)`", rendered_catalog, flags=re.MULTILINE)


def evaluate_catalog_tool_choice(case_id: str, *, limit: int = 10) -> dict:
    """Evaluate the actual catalog output against a golden tool-choice case."""

    from tools.brain.tool_catalog import render_tool_catalog

    case = get_tool_choice_case(case_id)
    catalog_intent = case.get("catalog_intent") or case.get("intent", "")
    rendered = render_tool_catalog(intent=catalog_intent, limit=limit)
    report = evaluate_tool_choice(case_id, extract_catalog_tools(rendered))
    report["catalog_intent"] = catalog_intent
    report["catalog_output"] = rendered
    return report

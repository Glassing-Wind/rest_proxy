"""memory_summary.py – Summarization and tool-output compaction for the memory layer.

Upgrades:
- Structured rolling summary JSON with stable sections.
- Optional retrieval-aware summary updates via relevant_memories.
- Graceful fallback to deterministic structured compaction.
- Helpers to parse / format summary objects cleanly.
- Tool outputs still compacted aggressively.

The rolling summary is stored as a JSON string, e.g.

{
  "goals": [],
  "decisions": [],
  "errors": [],
  "files_changed": [],
  "next_steps": [],
  "notes": []
}
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# Config (read once at import time from environment)
# ---------------------------------------------------------------------------
_SUMMARIZER_MODEL = os.getenv("LM_PROXY_MEMORY_SUMMARIZER_MODEL", "").strip()
_SUMMARIZER_BASE_URL = os.getenv(
    "LM_PROXY_MEMORY_SUMMARIZER_BASE_URL",
    os.getenv("LM_BASE", "http://127.0.0.1:1234"),
).rstrip("/")

_TOOL_RAW_MAX_CHARS = int(os.getenv("LM_PROXY_MEMORY_TOOL_RAW_MAX_CHARS", "8000"))
_TOOL_SUMMARY_MAX_CHARS = int(
    os.getenv("LM_PROXY_MEMORY_TOOL_SUMMARY_MAX_CHARS", "400")
)
_ROLLING_SUMMARY_MAX_WORDS = int(os.getenv("LM_PROXY_MEMORY_SUMMARY_MAX_WORDS", "300"))
_ROLLING_SUMMARY_MAX_ITEMS_PER_SECTION = int(
    os.getenv("LM_PROXY_MEMORY_SUMMARY_MAX_ITEMS_PER_SECTION", "8")
)
_ENABLE_DEBUG = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_SUMMARY_KEYS = (
    "goals",
    "decisions",
    "errors",
    "files_changed",
    "next_steps",
    "notes",
)


def _debug(message: str, **fields: Any) -> None:
    if not _ENABLE_DEBUG:
        return
    payload = {"message": message}
    payload.update(fields)
    try:
        print(
            f"[lm-proxy:memory_summary] {json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}",
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        print(
            f"[lm-proxy:memory_summary] {message} {fields}", file=sys.stderr, flush=True
        )


# ---------------------------------------------------------------------------
# Deterministic text compaction helpers (no external calls, always safe)
# ---------------------------------------------------------------------------


def _truncate_head_tail(text: str, max_chars: int) -> str:
    """Preserve head and tail of text, elide middle."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    half = max_chars // 2
    tail = max_chars - half - 15
    if tail < 0:
        tail = 0
    tail_text = text[-tail:] if tail else ""
    return f"{text[:half]}\n\n[...snip...]\n\n{tail_text}"


def _extract_bullets(text: str) -> List[str]:
    """Extract obvious bullet / key-value lines from a block of text."""
    bullets: List[str] = []
    error_pat = re.compile(r"(?i)(error|exception|fail|traceback|raise)", re.IGNORECASE)
    file_pat = re.compile(r"\b[\w./\\-]+\.\w{1,8}\b")
    decision_pat = re.compile(
        r"(?i)(decided|choosing|choose|will use|approach|strategy|switched to|use this)"
    )
    action_pat = re.compile(
        r"(?i)(next step|todo|action|plan|need to|should|must|try this)"
    )
    goal_pat = re.compile(r"(?i)(goal|want to|trying to|working on|build|implement)")

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) < 4:
            continue
        if (
            error_pat.search(stripped)
            or decision_pat.search(stripped)
            or action_pat.search(stripped)
            or goal_pat.search(stripped)
            or file_pat.search(stripped)
        ) and len(stripped) <= 220:
            bullets.append(stripped)
        elif stripped.startswith(("-", "*", "•", "·")) or re.match(
            r"^\d+[.)]\s", stripped
        ):
            bullets.append(stripped[:220])

    return bullets[:40]


def compact_text_deterministic(text: str, max_chars: int) -> str:
    """Deterministic compaction: head/tail + important bullet extraction."""
    if not text or max_chars <= 0:
        return text[:max_chars] if max_chars > 0 else text
    if len(text) <= max_chars:
        return text

    bullets = _extract_bullets(text)
    bullet_section = ""
    if bullets:
        bullet_section = (
            "\nKey points:\n" + "\n".join(f"  - {b}" for b in bullets[:10]) + "\n"
        )

    available = max_chars - len(bullet_section) - 20
    if available < 80:
        return (bullet_section + text)[:max_chars]

    truncated = _truncate_head_tail(text, available)
    return truncated + bullet_section


def compact_tool_output(tool_name: str, raw_output: str) -> Dict[str, str]:
    """
    Compact a tool output.

    Returns:
        {"raw": truncated raw (for storage), "compact": aggressive compact summary (for prompt)}
    """
    raw_truncated = _truncate_head_tail(raw_output, _TOOL_RAW_MAX_CHARS)
    compact = compact_text_deterministic(raw_output, _TOOL_SUMMARY_MAX_CHARS)
    compact = f"[{tool_name}] {compact.strip()}"[:_TOOL_SUMMARY_MAX_CHARS]
    _debug(
        "tool_output_compacted",
        tool=tool_name,
        raw_len=len(raw_output),
        compact_len=len(compact),
    )
    return {"raw": raw_truncated, "compact": compact}


# ---------------------------------------------------------------------------
# Structured summary helpers
# ---------------------------------------------------------------------------


def empty_summary_dict() -> Dict[str, List[str]]:
    return {key: [] for key in _SUMMARY_KEYS}


def _normalize_summary_item(value: Any, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^[\-\*\u2022\·\d\.\)\s]+", "", text)
    return text[:max_chars].strip()


def _dedupe_keep_order(items: List[str], limit: int) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for item in items:
        norm = _normalize_summary_item(item)
        if not norm:
            continue
        key = norm.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(norm)
        if len(result) >= limit:
            break
    return result


def normalize_summary_dict(data: Any) -> Dict[str, List[str]]:
    result = empty_summary_dict()
    if not isinstance(data, dict):
        return result
    for key in _SUMMARY_KEYS:
        raw = data.get(key, [])
        if isinstance(raw, list):
            values = [_normalize_summary_item(v) for v in raw]
        elif raw:
            values = [_normalize_summary_item(raw)]
        else:
            values = []
        result[key] = _dedupe_keep_order(values, _ROLLING_SUMMARY_MAX_ITEMS_PER_SECTION)
    return result


def summary_to_json(summary: Dict[str, List[str]]) -> str:
    normalized = normalize_summary_dict(summary)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def summary_to_pretty_json(summary: Dict[str, List[str]]) -> str:
    normalized = normalize_summary_dict(summary)
    return json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=False)


def parse_summary_text(summary_text: str) -> Dict[str, List[str]]:
    """
    Parse either:
    - JSON summary string
    - older plain-text summary string
    into the structured summary schema.
    """
    if not summary_text or not summary_text.strip():
        return empty_summary_dict()

    text = summary_text.strip()

    try:
        parsed = json.loads(text)
        return normalize_summary_dict(parsed)
    except Exception:
        pass

    # Legacy / plain-text fallback: infer buckets from bullet lines.
    result = empty_summary_dict()
    file_pat = re.compile(r"\b[\w./\\-]+\.\w{1,8}\b")
    error_pat = re.compile(r"(?i)(error|exception|fail|traceback|raise)")
    decision_pat = re.compile(
        r"(?i)(decided|choose|choosing|will use|approach|strategy|switched)"
    )
    action_pat = re.compile(r"(?i)(next step|todo|action|plan|need to|should|must)")
    goal_pat = re.compile(r"(?i)(goal|trying to|working on|want to|build|implement)")

    for line in _extract_bullets(text):
        item = _normalize_summary_item(line)
        if not item:
            continue
        if file_pat.search(item):
            result["files_changed"].append(item)
        elif error_pat.search(item):
            result["errors"].append(item)
        elif decision_pat.search(item):
            result["decisions"].append(item)
        elif action_pat.search(item):
            result["next_steps"].append(item)
        elif goal_pat.search(item):
            result["goals"].append(item)
        else:
            result["notes"].append(item)

    return normalize_summary_dict(result)


def format_summary_for_prompt(summary_text: str) -> str:
    """
    Render summary JSON as compact human-readable text for prompt injection.
    Safe to use on both JSON and old plain-text summaries.
    """
    summary = parse_summary_text(summary_text)
    sections: List[str] = []

    labels = {
        "goals": "Goals",
        "decisions": "Decisions",
        "errors": "Errors",
        "files_changed": "Files changed",
        "next_steps": "Next steps",
        "notes": "Notes",
    }
    for key in _SUMMARY_KEYS:
        items = summary.get(key, [])
        if not items:
            continue
        block = "\n".join(f"- {item}" for item in items)
        sections.append(f"{labels[key]}:\n{block}")

    return "\n\n".join(sections).strip()


def extract_summary_facts(
    summary_text: str, max_items_per_section: int = 5
) -> List[Dict[str, str]]:
    """
    Flatten structured summary into fact items for durable storage.
    """
    summary = parse_summary_text(summary_text)
    facts: List[Dict[str, str]] = []
    for key in _SUMMARY_KEYS:
        for item in summary.get(key, [])[:max_items_per_section]:
            facts.append({"fact_type": key, "content": item})
    return facts


def _merge_summary_dicts(
    previous_summary: Dict[str, List[str]],
    new_summary: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    merged = empty_summary_dict()
    for key in _SUMMARY_KEYS:
        merged[key] = _dedupe_keep_order(
            list(previous_summary.get(key, [])) + list(new_summary.get(key, [])),
            _ROLLING_SUMMARY_MAX_ITEMS_PER_SECTION,
        )
    return merged


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text or not text.strip():
        return None

    # First try raw parse.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Then try the first {...} block.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    candidate = match.group(0)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Rolling summary helpers
# ---------------------------------------------------------------------------


def _format_relevant_memories(relevant_memories: Optional[List[Dict[str, Any]]]) -> str:
    if not relevant_memories:
        return "[]"

    lines: List[str] = []
    for item in relevant_memories[:8]:
        text = _normalize_summary_item(item.get("compact_text", ""), max_chars=220)
        ref_type = str(item.get("ref_type", "memory"))
        score = item.get("rrf_score")
        score_text = f" score={score:.4f}" if isinstance(score, (int, float)) else ""
        if text:
            lines.append(f"- [{ref_type}{score_text}] {text}")
    return "\n".join(lines) if lines else "[]"


def _build_rolling_summary_prompt(
    previous_summary: str,
    new_turns: List[Dict[str, Any]],
    relevant_memories: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build the prompt for rolling summary update via LLM."""
    prev_structured = parse_summary_text(previous_summary)

    turns_text_parts: List[str] = []
    for t in new_turns:
        role = t.get("role", "?")
        content = compact_text_deterministic(str(t.get("content", "")), 700)
        turns_text_parts.append(f"[{role}] {content}")
    turns_text = "\n".join(turns_text_parts)

    relevant_text = _format_relevant_memories(relevant_memories)

    return (
        "You are a memory compression system for an AI coding/chat proxy.\n"
        "Update the structured rolling memory using the previous summary, relevant past memory, and the new turns.\n"
        "Keep only high-signal information. Deduplicate aggressively. Preserve goals, decisions, errors, files changed, and next steps.\n"
        f"Keep the result compact, under approximately {_ROLLING_SUMMARY_MAX_WORDS} words total.\n"
        "Do not include commentary. Return JSON only.\n\n"
        "Required JSON schema:\n"
        "{\n"
        '  "goals": [],\n'
        '  "decisions": [],\n'
        '  "errors": [],\n'
        '  "files_changed": [],\n'
        '  "next_steps": [],\n'
        '  "notes": []\n'
        "}\n\n"
        f"Previous structured summary:\n{summary_to_pretty_json(prev_structured)}\n\n"
        f"Relevant past memory:\n{relevant_text}\n\n"
        f"New turns:\n{turns_text}\n"
    )


async def _call_summarizer_llm(prompt: str) -> Optional[str]:
    """Call LM Studio/OpenAI-compatible endpoint to generate a summary."""
    if not _SUMMARIZER_MODEL:
        return None
    try:
        payload = {
            "model": _SUMMARIZER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 450,
            "temperature": 0.1,
            "stream": False,
        }
        timeout = httpx.Timeout(30.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{_SUMMARIZER_BASE_URL}/v1/chat/completions", json=payload
            )
            if r.status_code >= 400:
                _debug("summarizer_llm_error", status=r.status_code, body=r.text[:300])
                return None
            data = r.json()
            choices = data.get("choices", [])
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message", {})
                return str(msg.get("content", "")).strip() or None
    except Exception as exc:
        _debug("summarizer_llm_exception", error=str(exc))
    return None


def _classify_line(item: str) -> str:
    file_pat = re.compile(r"\b[\w./\\-]+\.\w{1,8}\b")
    error_pat = re.compile(r"(?i)(error|exception|fail|traceback|raise)")
    decision_pat = re.compile(
        r"(?i)(decided|choose|choosing|will use|approach|strategy|switched)"
    )
    action_pat = re.compile(r"(?i)(next step|todo|action|plan|need to|should|must)")
    goal_pat = re.compile(r"(?i)(goal|trying to|working on|want to|build|implement)")

    if file_pat.search(item):
        return "files_changed"
    if error_pat.search(item):
        return "errors"
    if decision_pat.search(item):
        return "decisions"
    if action_pat.search(item):
        return "next_steps"
    if goal_pat.search(item):
        return "goals"
    return "notes"


def _summarize_deterministic_structured(
    previous_summary: str,
    new_turns: List[Dict[str, Any]],
    relevant_memories: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, List[str]]:
    """
    Deterministic structured summary:
    - start from previous structured summary
    - extract bullet-like key points from new turns
    - optionally fold in a few retrieved memory snippets
    """
    summary = parse_summary_text(previous_summary)

    for mem in (relevant_memories or [])[:5]:
        mem_text = _normalize_summary_item(mem.get("compact_text", ""), max_chars=220)
        if mem_text:
            bucket = _classify_line(mem_text)
            summary[bucket].append(mem_text)

    for turn in new_turns:
        role = str(turn.get("role", "?"))
        content = str(turn.get("content", ""))
        compact = compact_text_deterministic(content, 360)
        bullets = _extract_bullets(compact)
        if not bullets and compact.strip():
            bullets = [compact[:220]]

        for bullet in bullets[:8]:
            item = _normalize_summary_item(f"[{role}] {bullet}", max_chars=220)
            bucket = _classify_line(item)
            summary[bucket].append(item)

    return normalize_summary_dict(summary)


async def update_rolling_summary(
    previous_summary: str,
    new_turns: List[Dict[str, Any]],
    relevant_memories: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Update the rolling summary with new turns.
    Tries LLM first, falls back to deterministic structured compaction.

    Returns a JSON string.
    """
    previous_structured = parse_summary_text(previous_summary)

    if _SUMMARIZER_MODEL:
        prompt = _build_rolling_summary_prompt(
            previous_summary=previous_summary,
            new_turns=new_turns,
            relevant_memories=relevant_memories,
        )
        result = await _call_summarizer_llm(prompt)
        if result:
            parsed = _extract_json_object(result)
            if parsed is not None:
                merged = _merge_summary_dicts(
                    previous_structured, normalize_summary_dict(parsed)
                )
                final_json = summary_to_json(merged)
                _debug("rolling_summary_updated_via_llm", length=len(final_json))
                return final_json
            _debug("rolling_summary_llm_invalid_json", preview=result[:200])

    deterministic = _summarize_deterministic_structured(
        previous_summary=previous_summary,
        new_turns=new_turns,
        relevant_memories=relevant_memories,
    )
    merged = _merge_summary_dicts(previous_structured, deterministic)
    final_json = summary_to_json(merged)
    _debug("rolling_summary_updated_deterministic", length=len(final_json))
    return final_json


# ---------------------------------------------------------------------------
# Compact a single conversation turn for storage
# ---------------------------------------------------------------------------


def compact_turn_content(role: str, content: str, max_chars: int = 600) -> str:
    """Compact a single turn's content for durable storage and retrieval."""
    if role == "tool":
        return compact_text_deterministic(
            content, min(max_chars, _TOOL_SUMMARY_MAX_CHARS)
        )
    return compact_text_deterministic(content, max_chars)

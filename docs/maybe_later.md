


# Planner / Coder Architecture (Safe Integration)

## Overview

Do NOT modify `proxy.py` core routing.  
Implement planner/coder at the client or agent layer.

Flow:

1. Planner call (lightweight, structured)
2. Coder call (executes plan)
3. Optional retry/critic loop

---

## Planner Prompt

```
You are a planning agent for a coding task.

Your job is to decide how to fix the problem with minimal exploration.

Rules:
- Do NOT solve the problem.
- Do NOT write code.
- Identify only what is necessary to act.
- Avoid broad searching.
- Prefer minimal patch strategy.

If a symbol/type is missing:
- Do NOT search repeatedly
- Plan to CREATE it from usage

Output JSON ONLY:

{
  "problem": "",
  "files_to_read": [],
  "search_terms": [],
  "likely_root_cause": "",
  "patch_strategy": "",
  "stop_condition": ""
}

Constraints:
- Max 3 files_to_read
- Max 2 search_terms
- Keep patch_strategy short and actionable
```

---

## Coder Prompt

```
You are a coding agent responsible for fixing the problem.

You are given a plan. Execute it.

Rules:
- Follow the plan strictly
- Prefer the smallest working fix
- Do not re-explore the repo unless necessary
- Do not repeat search cycles
- Do not reread the same files

If a type or file is missing:
- Create a minimal valid implementation
- Infer from usage

Behavior:
- Write code or patches immediately
- Do not explain unless necessary
- Do not ask questions if fix is clear

If previous attempt failed:
- Modify the previous patch
- Do NOT restart exploration

Goal:
Fix the issue quickly with minimal changes.
```

---

## Minimal Wiring Example

```python
plan = call_proxy(
    system=PLANNER_PROMPT,
    messages=user_messages
)

result = call_proxy(
    system=CODER_PROMPT,
    messages=[
        *user_messages,
        {"role": "system", "content": f"PLAN:\n{plan}"}
    ]
)
```

---

# Edge Fixes (Safe Improvements)

These do NOT break proxy core behavior.

---

## 1. Search Budget Hint

Inject early system message:

```python
messages.insert(0, {
  "role": "system",
  "content": "Search budget is limited. Do not repeat searches."
})
```

---

## 2. Repeated Tool Call Detection

Track last tool call:

```python
if tool_call == last_tool_call:
    debug_log("REPEATED TOOL CALL DETECTED")
```

Optional: short-circuit repeated calls.

---

## 3. Aggressive Tool Output Truncation

- Cap large file reads harder
- Drop repeated file reads
- Prefer summaries over raw dumps

---

## 4. Lightweight Metrics

Log:

```python
debug_log(f"tool_calls={count}")
debug_log(f"repeated_reads={count}")
```

Track:
- tool calls per request
- repeated file reads
- planner → coder success rate

---

## 5. Planner Context Reduction (Optional)

Planner does NOT need full memory:

- disable memory injection for planner calls
- keep full memory for coder calls

---

# Notes

- Do NOT merge planner logic into proxy
- Do NOT modify stateful routing
- Do NOT touch STATE or streaming logic

This layer sits ABOVE proxy and uses it as-is.

---

# Future Upgrades

- Add critic agent (3rd pass)
- Add patch diff retry loop
- Add tool-call gating / rate limiting
- Add embedding-aware planning
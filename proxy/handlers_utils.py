"""proxy/handlers_utils.py — shared handler utilities."""

import hashlib
import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from proxy.logging import debug_log, stable_json


def is_insufficient_resource_error_text(detail_text: str) -> bool:
    text = detail_text.lower()
    return (
        "insufficient system resources" in text
        or "would likely overload your system" in text
        or "model loading was stopped" in text
    )


def history_key(messages: List[Dict[str, Any]]) -> str:
    return hashlib.sha256(stable_json(messages).encode("utf-8")).hexdigest()


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    parts.append(str(item["text"]))
                elif item.get("type") == "input_text" and "text" in item:
                    parts.append(str(item["text"]))
                elif "text" in item:
                    parts.append(str(item["text"]))
                else:
                    parts.append(stable_json(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return stable_json(content)


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head - 13
    if tail < 0:
        tail = 0
    return f"{text[:head]}\n\n[...snip...]\n\n{text[-tail:] if tail else ''}"


def _detect_and_break_tool_loop(
    messages: List[Dict[str, Any]],
    threshold: int = 3,
) -> List[Dict[str, Any]]:
    """Detect consecutive duplicate tool calls and inject a loop-breaking message.

    Scans the assistant messages in *messages* for a run of identical
    (tool_name, arguments) pairs at the tail of the conversation.  When the
    run length meets or exceeds *threshold*, appends a synthetic 'user'
    message that explains the loop and tells the model to change approach.

    Returns the original list unchanged when no loop is detected, or a new
    list with the warning appended.
    """
    if not messages or threshold < 2:
        return messages

    # Collect (tool_name, arguments_str) for every assistant tool-call message.
    call_records: List[tuple] = []  # (message_index, (name, args))
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        tcs = msg.get("tool_calls")
        if not isinstance(tcs, list) or not tcs:
            continue
        tc = tcs[0]  # check first (usually only) tool call in the turn
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function", {})
        key = (fn.get("name", ""), fn.get("arguments", ""))
        call_records.append((i, key))

    if len(call_records) < threshold:
        return messages

    # Count how long the run of identical keys is at the tail.
    last_key = call_records[-1][1]
    run = 1
    for _, key in reversed(call_records[:-1]):
        if key == last_key:
            run += 1
        else:
            break

    if run < threshold:
        return messages

    # Build a human-readable summary of the repeated call.
    tool_name, tool_args = last_key
    try:
        args_dict = json.loads(tool_args) if tool_args else {}
        args_summary = ", ".join(f"{k}={v!r}" for k, v in list(args_dict.items())[:5])
    except Exception:
        args_summary = tool_args[:120] if tool_args else ""

    print(
        f"[lm-proxy] tool_loop_detected tool={tool_name!r} run={run} args={args_summary[:80]!r}",
        flush=True,
    )

    warning = (
        f"[PROXY LOOP ALERT] You have called `{tool_name}({args_summary})` "
        f"{run} consecutive times with identical arguments and received the same "
        f"result every time. You MUST change your approach:\n"
        f"  - If you are reading a file, increment the offset to the value shown "
        f"in the last result (e.g., if the result says 'Use offset=2763', call "
        f"read with offset=2763 — NOT {args_summary}).\n"
        f"  - If no further reading is needed, synthesise what you have learned "
        f"and respond to the user directly.\n"
        f"Do NOT repeat the same call again."
    )

    new_messages = list(messages)
    new_messages.append({"role": "user", "content": warning})
    return new_messages


def stats_to_usage(stats: Any) -> Dict[str, Any]:
    if not isinstance(stats, dict):
        return {}

    prompt_tokens = stats.get("input_tokens", 0)
    completion_tokens = stats.get("total_output_tokens", 0)
    if not isinstance(prompt_tokens, int):
        prompt_tokens = int(prompt_tokens or 0)
    if not isinstance(completion_tokens, int):
        completion_tokens = int(completion_tokens or 0)

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def extract_system_prompt(messages: List[Dict[str, Any]]) -> Optional[str]:
    parts: List[str] = []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            text = content_to_text(message.get("content", ""))
            if text:
                parts.append(text)
    if not parts:
        return None
    return "\n\n".join(parts)


def get_last_non_system_message(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") != "system":
            return message
    raise HTTPException(
        status_code=400, detail="No non-system message found in request"
    )


def extract_tool_calls_from_api_chat_output(output: Any) -> List[Dict[str, Any]]:
    if not isinstance(output, list):
        return []

    tool_calls: List[Dict[str, Any]] = []
    for index, item in enumerate(output):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "invalid_tool_call":
            debug_log(
                "invalid_tool_call_output",
                reason=item.get("reason"),
                metadata=item.get("metadata"),
            )
            continue
        if item.get("type") != "tool_call":
            continue

        tool_name = item.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            continue

        arguments = item.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = stable_json(arguments)

        tool_calls.append(
            {
                "id": f"call_{index}_{tool_name}",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }
        )

    return tool_calls


def extract_text_from_api_chat_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    if not isinstance(output, list):
        return ""

    parts: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue

        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for sub in content:
                if isinstance(sub, dict):
                    text = sub.get("text") or sub.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(sub, str):
                    parts.append(sub)
    return "".join(parts)


def build_chat_completion_response(
    model: str,
    resp: Dict[str, Any],
    assistant_text: str,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": assistant_text if assistant_text else "",
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": f"chatcmpl_proxy_{resp.get('response_id', resp.get('id', 'unknown'))}",
        "object": "chat.completion",
        "created": resp.get("created_at", resp.get("created", 0)),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": stats_to_usage(resp.get("stats", {})),
    }

"""proxy/handlers_filtering.py — message filtering and compaction."""

import asyncio
import json
import os
from typing import Any, Dict, List

from proxy.config import ENABLE_PROXY_FILTERING
from proxy.logging import debug_log
from proxy.handlers_utils import content_to_text, _truncate_text
from proxy.handlers_memory import _extract_and_cache_skeleton_bg


def request_uses_openai_tools(
    body: Dict[str, Any], messages: List[Dict[str, Any]]
) -> bool:
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        return True

    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            return True
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return True

    return False


# ---------------------------------------------------------------------------
# Tool definition compaction
# ---------------------------------------------------------------------------

# Set to "0" to disable tool description compaction.
_COMPACT_TOOL_DEFINITIONS = os.getenv(
    "LM_PROXY_COMPACT_TOOL_DEFINITIONS", "1"
).strip().lower() not in {"0", "false", "no", "off"}

# Max chars to keep from each tool description (first N chars ≈ first sentence).
_TOOL_DESC_MAX_CHARS = int(os.getenv("LM_PROXY_TOOL_DESC_MAX_CHARS", "120"))
# Parameter-level description max chars (per param).
_PARAM_DESC_MAX_CHARS = int(os.getenv("LM_PROXY_PARAM_DESC_MAX_CHARS", "80"))
# Schema keys that add noise without helping the model pick arguments.
_SCHEMA_NOISE_KEYS = frozenset({"$schema", "additionalProperties"})


def _compact_tool_definitions(tools: Any) -> Any:
    """Strip verbose text from tool definitions before forwarding to LM Studio.

    Reduces token cost by:
    - Truncating long tool descriptions to the first sentence / first N chars.
    - Truncating per-parameter descriptions similarly.
    - Removing schema noise keys ($schema, additionalProperties) that LM Studio
      doesn't need and that waste tokens.

    Returns the original value unchanged if compaction is disabled or the input
    is not a list.
    """
    if not _COMPACT_TOOL_DEFINITIONS or not isinstance(tools, list):
        return tools

    compacted: List[Any] = []
    for tool in tools:
        if not isinstance(tool, dict):
            compacted.append(tool)
            continue
        tool_copy = dict(tool)
        fn = tool_copy.get("function")
        if not isinstance(fn, dict):
            compacted.append(tool_copy)
            continue
        fn_copy = dict(fn)

        # Truncate top-level description to first sentence / max chars.
        desc = fn_copy.get("description", "")
        if isinstance(desc, str) and len(desc) > _TOOL_DESC_MAX_CHARS:
            # Try to cut at first sentence boundary.
            for sep in ("\n", ". ", "! ", "? "):
                idx = desc.find(sep)
                if 0 < idx <= _TOOL_DESC_MAX_CHARS:
                    desc = desc[: idx + 1].rstrip()
                    break
            else:
                desc = desc[:_TOOL_DESC_MAX_CHARS].rstrip() + "…"
            fn_copy["description"] = desc

        # Compact parameters schema.
        params = fn_copy.get("parameters")
        if isinstance(params, dict):
            params_copy = {
                k: v for k, v in params.items() if k not in _SCHEMA_NOISE_KEYS
            }
            props = params_copy.get("properties")
            if isinstance(props, dict):
                new_props: Dict[str, Any] = {}
                for pname, pval in props.items():
                    if not isinstance(pval, dict):
                        new_props[pname] = pval
                        continue
                    p_copy = dict(pval)
                    pdesc = p_copy.get("description", "")
                    if isinstance(pdesc, str) and len(pdesc) > _PARAM_DESC_MAX_CHARS:
                        for sep in ("\n", ". ", "! ", "? "):
                            idx = pdesc.find(sep)
                            if 0 < idx <= _PARAM_DESC_MAX_CHARS:
                                pdesc = pdesc[: idx + 1].rstrip()
                                break
                        else:
                            pdesc = pdesc[:_PARAM_DESC_MAX_CHARS].rstrip() + "…"
                        p_copy["description"] = pdesc
                    new_props[pname] = p_copy
                params_copy["properties"] = new_props
            fn_copy["parameters"] = params_copy

        tool_copy["function"] = fn_copy
        compacted.append(tool_copy)

    original_chars = sum(len(json.dumps(t)) for t in tools)
    compacted_chars = sum(len(json.dumps(t)) for t in compacted)
    if original_chars != compacted_chars:
        print(
            f"[lm-proxy] tool_defs_compacted tools={len(compacted)}"
            f" before={original_chars} after={compacted_chars}"
            f" saved={original_chars - compacted_chars}",
            flush=True,
        )
    return compacted


def compact_message_content(content: Any, max_chars: int) -> Any:
    if isinstance(content, str):
        return _truncate_text(content, max_chars)

    if isinstance(content, list):
        compacted: List[Any] = []
        for item in content:
            if isinstance(item, dict):
                item_copy = dict(item)

                if isinstance(item_copy.get("text"), str):
                    item_copy["text"] = _truncate_text(item_copy["text"], max_chars)

                if isinstance(item_copy.get("content"), str):
                    item_copy["content"] = _truncate_text(
                        item_copy["content"], max_chars
                    )

                image_url = item_copy.get("image_url")
                if (
                    isinstance(image_url, dict)
                    and isinstance(image_url.get("url"), str)
                    and image_url["url"].startswith("data:")
                ):
                    image_url = dict(image_url)
                    image_url["url"] = _truncate_text(
                        image_url["url"], min(len(image_url["url"]), max_chars)
                    )
                    item_copy["image_url"] = image_url

                compacted.append(item_copy)
            elif isinstance(item, str):
                compacted.append(_truncate_text(item, max_chars))
            else:
                compacted.append(item)
        return compacted

    return content


def _is_filter_bypassed(messages: List[Dict[str, Any]]) -> bool:
    if not messages:
        return True
    return False


def filter_messages_for_proxy(
    session_id: str, messages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if _is_filter_bypassed(messages):
        debug_log("filter_bypassed_empty_messages")
        return messages

    if not ENABLE_PROXY_FILTERING:
        debug_log("filter_bypassed_disabled", message_count=len(messages))
        return messages

    max_recent_messages = int(os.getenv("LM_PROXY_MAX_RECENT_MESSAGES", "40"))
    max_old_message_chars = int(os.getenv("LM_PROXY_MAX_OLD_MESSAGE_CHARS", "4000"))
    max_recent_message_chars = int(
        os.getenv("LM_PROXY_MAX_RECENT_MESSAGE_CHARS", "12000")
    )
    max_system_prompt_chars = int(
        os.getenv("LM_PROXY_MAX_SYSTEM_PROMPT_CHARS", "10000")
    )
    max_tool_message_chars = int(os.getenv("LM_PROXY_MAX_TOOL_MESSAGE_CHARS", "200000"))

    filtered: List[Dict[str, Any]] = []
    non_system_indices = [
        i
        for i, msg in enumerate(messages)
        if isinstance(msg, dict) and msg.get("role") != "system"
    ]
    recent_non_system = set(non_system_indices[-max_recent_messages:])

    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            continue

        role = message.get("role")
        msg_copy = dict(message)

        if role == "system":
            # Always keep system messages, but truncate if they are too long.
            msg_copy["content"] = _truncate_text(
                content_to_text(message.get("content", "")), max_system_prompt_chars
            )
            filtered.append(msg_copy)
            continue

        if role == "tool":
            raw = message.get("content", "")
            if isinstance(raw, str):
                # Trigger skeleton extraction in the background if it's a file read response
                if len(raw) > 50 and "\n" in raw:
                    asyncio.create_task(_extract_and_cache_skeleton_bg(session_id, raw))

            if isinstance(raw, str) and len(raw) > max_tool_message_chars:
                # Head-only: never split code in the middle with [...snip...].
                # The model should use the offset parameter to read further.
                raw = raw[:max_tool_message_chars].rstrip() + (
                    "\n[PROXY: output truncated at"
                    f" {max_tool_message_chars} chars."
                    " Use a smaller limit or a higher offset to read further.]"
                )
            elif not isinstance(raw, str):
                raw = compact_message_content(raw, max_tool_message_chars)
            msg_copy["content"] = raw
            filtered.append(msg_copy)
            continue

        max_chars = (
            max_recent_message_chars
            if i in recent_non_system
            else max_old_message_chars
        )
        msg_copy["content"] = compact_message_content(
            message.get("content", ""), max_chars
        )
        filtered.append(msg_copy)

    debug_log(
        "filter_applied",
        original_count=len(messages),
        filtered_count=len(filtered),
        max_recent_messages=max_recent_messages,
        max_old_message_chars=max_old_message_chars,
        max_recent_message_chars=max_recent_message_chars,
        max_system_prompt_chars=max_system_prompt_chars,
        max_tool_message_chars=max_tool_message_chars,
    )
    return filtered


def normalize_input_content(content: Any) -> str | List[Dict[str, Any]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content_to_text(content)

    normalized: List[Dict[str, Any]] = []
    text_parts: List[str] = []

    def flush_text_parts() -> None:
        nonlocal text_parts, normalized
        if text_parts:
            normalized.append({"type": "message", "content": "\n".join(text_parts)})
            text_parts = []

    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
            continue

        if not isinstance(item, dict):
            text_parts.append(str(item))
            continue

        item_type = item.get("type")

        if item_type in {"text", "input_text"}:
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                text_parts.append(text)
            else:
                text_parts.append(content_to_text(item))
            continue

        if item_type == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url")
            else:
                url = image_url
            if isinstance(url, str) and url.startswith("data:"):
                flush_text_parts()
                normalized.append({"type": "image", "data_url": url})
                continue
            text_parts.append(content_to_text(item))
            continue

        if item_type == "image":
            data_url = item.get("data_url")
            if isinstance(data_url, str):
                flush_text_parts()
                normalized.append({"type": "image", "data_url": data_url})
                continue
            text_parts.append(content_to_text(item))
            continue

        text_parts.append(content_to_text(item))

    flush_text_parts()

    if not normalized:
        return ""
    if len(normalized) == 1 and normalized[0].get("type") == "message":
        return normalized[0]["content"]
    return normalized

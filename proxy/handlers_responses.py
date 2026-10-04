"""proxy/handlers_responses.py — responses API forwarding."""

import asyncio
from typing import Any, Dict, List, Optional

import httpx
from fastapi.responses import JSONResponse

from proxy.config import OPENAI_BASE, ENABLE_DEBUG_LOGGING
from proxy.logging import debug_log
from proxy.handlers_memory import _derive_session_id, _persist_memory_best_effort
from proxy.state import STATE, save_state
from proxy.handlers_utils import history_key


def _extract_new_messages_for_responses_api(
    messages: List[Dict[str, Any]],
) -> tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Split messages into system_prompt and the new input items since the last assistant turn.

    Returns (system_prompt, input_items) where input_items are formatted for /v1/responses:
    - user text        -> {"role": "user", "content": [...]}
    - tool result      -> {"type": "function_call_output", "call_id": "...", "output": "..."}
    """
    system_prompt: Optional[str] = None
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            system_prompt = m.get("content", "") or system_prompt

    # Find the index of the last assistant message (with tool_calls = end of last model turn)
    last_asst_idx = -1
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "assistant":
            last_asst_idx = i

    # New messages: everything after the last assistant turn (tool results + new user msg)
    raw_new = (
        messages[last_asst_idx + 1 :]
        if last_asst_idx >= 0
        else [
            m
            for m in messages
            if isinstance(m, dict) and m.get("role") not in ("system", "assistant")
        ]
    )

    input_items: List[Dict[str, Any]] = []
    for m in raw_new:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "tool":
            # Convert tool result to function_call_output
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": m.get("tool_call_id", "unknown"),
                    "output": str(m.get("content", "")),
                }
            )
        elif role == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                input_items.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # Pass through content array as-is (images etc.)
                input_items.append({"role": "user", "content": content})

    return system_prompt, input_items


def _responses_output_to_chat_completion(
    resp: Dict[str, Any], model: str
) -> Dict[str, Any]:
    """Translate a /v1/responses response body to OpenAI chat.completion format."""
    output = resp.get("output", [])
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text_parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    text_parts.append(part)
        elif item_type == "function_call":
            tool_calls.append(
                {
                    "id": item.get("call_id")
                    or item.get("id")
                    or f"call_{len(tool_calls)}",
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", ""),
                    },
                }
            )
        elif item_type == "reasoning":
            pass  # reasoning is internal; don't expose to client

    text = "".join(text_parts)
    message: Dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    # Map usage fields
    raw_usage = resp.get("usage", {}) or {}
    usage = {
        "prompt_tokens": raw_usage.get("input_tokens", 0),
        "completion_tokens": raw_usage.get("output_tokens", 0),
        "total_tokens": raw_usage.get("total_tokens", 0),
    }

    return {
        "id": resp.get("id", "chatcmpl_responses"),
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": usage,
        # Pass the response_id through so callers can inspect it.
        "x_lmstudio_response_id": resp.get("id"),
    }


async def forward_responses_api_completion(
    body: Dict[str, Any], history_messages: Optional[List[Dict[str, Any]]] = None
) -> Any:
    """
    Forward a chat/completions request to LM Studio's /v1/responses endpoint.
    This endpoint is stateful (via previous_response_id) and OpenAI-compatible (messages/tools).
    """
    messages: List[Dict[str, Any]] = body.get("messages", [])

    debug_log(
        "dumping_raw_messages",
        first_message=messages[0] if messages else None,
        last_message=messages[-1] if messages else None,
    )

    # Use history_messages if provided, otherwise default to the current messages array.
    # This prevents mutations (like tool-calling blocks) from breaking the history_key.
    tracking_messages = history_messages if history_messages is not None else messages

    model: str = body.get("model", "")
    stream = bool(body.get("stream", False))
    timeout = httpx.Timeout(900.0, connect=30.0)

    # Look up stored response_id for this session to maintain continuity
    # We MUST use the base history_messages (original raw) if provided for lookup,
    # because that's what we used to SAVE the key in the previous Turn.
    prev_messages = tracking_messages[:-1] if len(tracking_messages) > 1 else []
    hk = history_key(prev_messages)
    prev_response_id = STATE.get(hk)

    # If the previous turn fell back to stateless, it has a chatcmpl_ ID. We must ignore it.
    if prev_response_id and not prev_response_id.startswith("resp_"):
        prev_response_id = None

    # If we have a prev_response_id, we only need to send the latest message.
    # Otherwise (e.g., Turn 1 or dropped session), we send the full history to initialize a new state.
    if prev_response_id:
        last_msg = messages[-1] if messages else {}
        payload_input = [last_msg] if last_msg else []
    else:
        payload_input = messages

    instructions = (
        messages[0].get("content", "")
        if messages and messages[0].get("role") == "system"
        else ""
    )

    payload = {
        "model": model,
        "input": payload_input,
        "stream": stream,
    }

    if "tools" in body:
        payload["tools"] = body["tools"]
    if "tool_choice" in body:
        tc = body["tool_choice"]
        if isinstance(tc, dict):
            # LM Studio only supports string tool_choice like "auto", "required", "none"
            payload["tool_choice"] = "required" if tc else "auto"
        else:
            payload["tool_choice"] = tc

    # Only send instructions if non-empty and only on the FIRST turn (no previous ID)
    if instructions and not prev_response_id:
        payload["instructions"] = instructions

    if prev_response_id:
        payload["previous_response_id"] = prev_response_id

    if ENABLE_DEBUG_LOGGING:
        debug_log(
            "responses_api_request",
            model=model,
            stream=stream,
            has_previous_response_id=bool(prev_response_id),
            message_count=len(tracking_messages),
        )

    if stream:
        from proxy.handlers_streaming import stream_openai_compatible_response
        from proxy.handlers_chat import forward_openai_chat_completion

        # Use existing streaming helper but target the /v1/responses endpoint
        return await stream_openai_compatible_response(
            url=f"{OPENAI_BASE}/responses",
            payload=payload,
            messages=messages,
            original_body=body,
            fallback_func=forward_openai_chat_completion,
        )

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.post(f"{OPENAI_BASE}/responses", json=payload)
            r.raise_for_status()
            resp = r.json()
        except httpx.HTTPStatusError as exc:
            try:
                err_json = exc.response.json()
                if "error" not in err_json or not isinstance(err_json["error"], dict):
                    err_json = {
                        "error": {"message": exc.response.text, "type": "proxy_error"}
                    }
            except Exception:
                err_json = {
                    "error": {"message": exc.response.text, "type": "proxy_error"}
                }

            debug_log("responses_api_failed", error=err_json)
            # Fall back to stateless chat/completions if responses API rejects the payload
            from proxy.handlers_chat import forward_openai_chat_completion

            return await forward_openai_chat_completion(body)
        except Exception as exc:
            debug_log("responses_api_failed", error=str(exc))
            # Fall back to stateless chat/completions on other errors
            from proxy.handlers_chat import forward_openai_chat_completion

            return await forward_openai_chat_completion(body)

    # Extract the assistant message and new response_id for state tracking
    new_response_id = resp.get("id")

    # Handle both OpenAI 'choices' and LM Studio 'output' formats
    choices = resp.get("choices", [])
    output = resp.get("output", [])

    asst_msg = {}
    finish_reason = None

    if choices:
        asst_msg = choices[0].get("message", {})
        finish_reason = choices[0].get("finish_reason")
    elif output:
        # LM Studio v1 /v1/responses format
        last_out = output[-1] if output else {}
        finish_reason = last_out.get("status")
        # Extract text from the content array if present
        content_items = last_out.get("content", [])
        text_content = "".join(
            [i.get("text", "") for i in content_items if i.get("type") == "output_text"]
        )
        asst_msg = {"role": "assistant", "content": text_content}

    if new_response_id and asst_msg:
        hk_save = history_key(tracking_messages + [asst_msg])
        STATE[hk_save] = new_response_id
        save_state()

    debug_log(
        "responses_api_success",
        new_response_id=new_response_id,
        finish_reason=finish_reason,
    )

    try:
        session_id = _derive_session_id(body, tracking_messages)
        asyncio.create_task(
            _persist_memory_best_effort(
                session_id=session_id,
                model=model,
                messages=tracking_messages,
                assistant_text=asst_msg.get("content") or "",
                tool_calls=asst_msg.get("tool_calls"),
            )
        )
    except Exception as exc:
        debug_log("memory_persist_schedule_failed", error=str(exc))

    # Always return standard OpenAI format even if backend is stateful Responses API
    if (output or new_response_id) and not choices:
        # Map back to OpenAI choices format for the client
        resp["choices"] = [
            {
                "index": 0,
                "message": asst_msg,
                "finish_reason": finish_reason,
            }
        ]

    return JSONResponse(resp)

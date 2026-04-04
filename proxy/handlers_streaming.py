"""proxy/handlers_streaming.py — streaming helpers."""

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from proxy.logging import debug_log
from proxy.state import STATE, save_state
from proxy.handlers_utils import history_key


async def stream_openai_compatible_response(
    url: str,
    payload: Dict[str, Any],
    messages: List[Dict[str, Any]] | None = None,
    original_body: Dict[str, Any] = None,
    fallback_func=None,
) -> Any:
    """
    Generic streaming helper that pipes an OpenAI-compatible stream from an upstream URL.
    Optionally updates the global STATE with the resulting response_id for continuity.
    """
    timeout = httpx.Timeout(900.0, connect=30.0)
    client = httpx.AsyncClient(timeout=timeout)
    req = client.build_request("POST", url, json=payload)

    try:
        r = await client.send(req, stream=True)
    except Exception as exc:
        await client.aclose()
        if fallback_func and original_body is not None:
            debug_log("streaming_failed_fallback", error=str(exc))
            return await fallback_func(original_body)
        raise HTTPException(status_code=500, detail=str(exc))

    if r.status_code >= 400:
        detail = await r.aread()
        err_text = detail.decode("utf-8", errors="replace")
        await r.aclose()
        await client.aclose()
        if fallback_func and original_body is not None:
            debug_log(
                "streaming_rejected_fallback", status=r.status_code, error=err_text
            )
            return await fallback_func(original_body)
        raise HTTPException(r.status_code, err_text)

    async def event_generator():
        last_id = None
        accumulated_content = ""
        current_tool_call_index = -1
        try:
            is_responses_api = url.endswith("/responses")
            async for line in r.aiter_lines():
                if not line:
                    continue

                if not is_responses_api:
                    # Standard OpenAI stream pass-through
                    if messages and line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str and data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                if not last_id:
                                    last_id = data.get("id") or data.get("response_id")

                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    if "content" in delta:
                                        accumulated_content += delta["content"]
                                    if choices[0].get("finish_reason"):
                                        debug_log(
                                            "stream_finished",
                                            reason=choices[0].get("finish_reason"),
                                        )
                            except (json.JSONDecodeError, KeyError):
                                pass

                    yield f"{line}\n\n"
                else:
                    # Translate /v1/responses SSE stream to OpenAI chat completions SSE chunk
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str and data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                evt_type = data.get("type")

                                if evt_type == "response.created":
                                    if not last_id:
                                        last_id = data.get("response", {}).get(
                                            "id", "resp_unk"
                                        )
                                    # yield initial role block
                                    chunk = {
                                        "id": last_id,
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": payload.get("model", "unknown"),
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {
                                                    "role": "assistant",
                                                    "content": "",
                                                },
                                                "finish_reason": None,
                                            }
                                        ],
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"

                                elif evt_type == "response.output_text.delta":
                                    text_delta = data.get("delta", "")
                                    accumulated_content += text_delta
                                    chunk = {
                                        "id": last_id or "resp_unk",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": payload.get("model", "unknown"),
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {"content": text_delta},
                                                "finish_reason": None,
                                            }
                                        ],
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"

                                elif evt_type == "tool_call.start":
                                    current_tool_call_index += 1
                                    call_id = f"call_{uuid.uuid4().hex[:12]}"
                                    tool_name = data.get("tool", "unknown")
                                    chunk = {
                                        "id": last_id or "resp_unk",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": payload.get("model", "unknown"),
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {
                                                    "tool_calls": [
                                                        {
                                                            "index": current_tool_call_index,
                                                            "id": call_id,
                                                            "type": "function",
                                                            "function": {
                                                                "name": tool_name,
                                                                "arguments": "",
                                                            },
                                                        }
                                                    ]
                                                },
                                                "finish_reason": None,
                                            }
                                        ],
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"

                                elif evt_type == "tool_call.arguments":
                                    args_obj = data.get("arguments", {})
                                    args_str = (
                                        json.dumps(args_obj)
                                        if isinstance(args_obj, dict)
                                        else str(args_obj)
                                    )
                                    chunk = {
                                        "id": last_id or "resp_unk",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": payload.get("model", "unknown"),
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {
                                                    "tool_calls": [
                                                        {
                                                            "index": max(
                                                                0,
                                                                current_tool_call_index,
                                                            ),
                                                            "function": {
                                                                "arguments": args_str
                                                            },
                                                        }
                                                    ]
                                                },
                                                "finish_reason": None,
                                            }
                                        ],
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"

                                elif evt_type == "response.completed":
                                    chunk = {
                                        "id": last_id or "resp_unk",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": payload.get("model", "unknown"),
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {},
                                                "finish_reason": "stop",
                                            }
                                        ],
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"
                                    yield "data: [DONE]\n\n"
                            except (json.JSONDecodeError, KeyError):
                                pass
        except Exception as exc:
            debug_log("streaming_failed", error=str(exc))
            err_dict = {"error": {"message": str(exc), "type": "proxy_stream_error"}}
            yield f"data: {json.dumps(err_dict)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            await r.aclose()
            await client.aclose()

        # Save state if we captured an ID and we have the history context
        if last_id and messages:
            asst_msg = {"role": "assistant", "content": accumulated_content}
            STATE[history_key(messages + [asst_msg])] = last_id
            # Fire-and-forget save
            asyncio.create_task(asyncio.to_thread(save_state))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

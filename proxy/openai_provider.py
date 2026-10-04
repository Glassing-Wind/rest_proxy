"""OpenAI Responses transport with a Chat Completions compatibility boundary.

Local LM Studio routes are independent. Full-history replay works after restarts;
a bounded cache retains typed output (including encrypted reasoning) between turns.
"""

import asyncio
import copy
import json
import os
import time
from collections import OrderedDict
from typing import Any, Dict, List

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from proxy import config
from proxy.handlers_memory import _derive_session_id, _persist_memory_best_effort
from proxy.handlers_utils import _truncate_text, history_key
from proxy.logging import debug_log, stable_json
from proxy.models import parse_model_aliases

_TIMEOUT = httpx.Timeout(900.0, connect=30.0)
# No response IDs are shared with LM Studio or persisted to disk.
_REPLAY: OrderedDict[str, tuple[float, List[Dict[str, Any]]]] = OrderedDict()
_CACHE_ENTRIES = 64
_CACHE_ITEM_BYTES = 256_000
_CACHE_TTL = 1800
_BACKGROUND_TASKS: set[asyncio.Task] = set()
_SEARCH_TOOL = {
    "type": "function",
    "name": "codebase_search",
    "strict": False,
    "description": "Search the indexed project for relevant code. Returns bounded source excerpts.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}


def _headers() -> Dict[str, str]:
    if not config.OPENAI_API_KEY:
        raise HTTPException(503, "Set OPENAI_API_KEY to enable the OpenAI provider")
    return {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}


def _bad_request(message: str) -> None:
    raise HTTPException(400, message)


def _content(content: Any, role: str) -> Any:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if not isinstance(content, list):
        _bad_request("Message content must be text or a content array")
    result = []
    for part in content:
        if not isinstance(part, dict):
            _bad_request("Invalid content part")
        kind = part.get("type")
        if kind == "text":
            result.append({"type": "output_text" if role == "assistant" else "input_text", "text": part["text"]})
        elif kind == "image_url" and role == "user":
            image = part["image_url"]
            result.append({"type": "input_image", "image_url": image["url"], "detail": image.get("detail", "auto")})
        elif kind in {"input_text", "input_image", "input_file", "output_text", "refusal"}:
            result.append(copy.deepcopy(part))
        else:
            _bad_request(f"Unsupported content type: {kind}")
    return result


def _message_items(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    role = message.get("role")
    if role == "tool":
        if not message.get("tool_call_id"):
            _bad_request("Tool results require tool_call_id")
        content = message.get("content", "")
        return [
            {
                "type": "function_call_output",
                "call_id": message["tool_call_id"],
                "output": content if isinstance(content, str) else stable_json(content),
            }
        ]
    if role not in {"system", "developer", "user", "assistant"}:
        _bad_request(f"Unsupported message role: {role}")
    items = []
    content = _content(message.get("content"), role)
    if content or role != "assistant":
        items.append({"role": role, "content": content})
    if message.get("refusal"):
        items.append({"role": "assistant", "content": [{"type": "refusal", "refusal": message["refusal"]}]})
    for call in message.get("tool_calls") or []:
        if call.get("type") != "function" or not call.get("id"):
            _bad_request("Only function tool calls with IDs are supported")
        fn = call["function"]
        items.append({"type": "function_call", "call_id": call["id"], "name": fn["name"], "arguments": fn["arguments"]})
    return items


def _cache_key(body: Dict[str, Any], messages: List[Dict[str, Any]]) -> str:
    # Normalize assistant null/empty text and ignore client-added metadata.
    normalized = [_message_items(message) for message in messages]
    return history_key(
        [
            {
                "provider": config.OPENAI_API_BASE,
                "model": body["model"],
                "session": body.get("session_id") or body.get("x_session_id"),
                "messages": normalized,
            }
        ]
    )


def _input_items(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages = body["messages"]
    items = []
    now = time.monotonic()
    for key, (created, _) in list(_REPLAY.items()):
        if now - created > _CACHE_TTL:
            _REPLAY.pop(key, None)
    for index, message in enumerate(messages):
        cached = _REPLAY.get(_cache_key(body, messages[: index + 1])) if message.get("role") == "assistant" else None
        if cached:
            items.extend(copy.deepcopy(cached[1]))
        else:
            items.extend(_message_items(message))
    return items


def build_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a caller's contract without LM Studio-specific schema compaction."""
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
        _bad_request("messages must be a nonempty list")
    if any(not isinstance(m, dict) for m in body["messages"]):
        _bad_request("Each message must be an object")
    if body.get("n", 1) != 1:
        _bad_request("Responses supports n=1 only")
    for name in ("previous_response_id", "functions", "function_call", "audio"):
        if body.get(name) is not None:
            _bad_request(
                f"{name} is unsupported on this Chat Completions adapter; send full messages and function tools"
            )
    effort = body.get("reasoning_effort", config.OPENAI_REASONING_EFFORT)
    if effort == "minimal":
        effort = "low"
    if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        _bad_request("Unsupported reasoning_effort")
    if body["model"].startswith("gpt-6-astra") and effort == "none":
        effort = "low"
    payload = {
        "model": body["model"],
        "input": _input_items(body),
        "reasoning": {"effort": effort},
        "store": body.get("store", False),
        "stream": bool(body.get("stream")),
        "include": ["reasoning.encrypted_content"],
    }
    for name in ("max_completion_tokens", "max_tokens", "max_output_tokens"):
        if name in body:
            payload["max_output_tokens"] = body[name]
            break
    for name in ("parallel_tool_calls", "metadata", "service_tier", "safety_identifier", "prompt_cache_key"):
        if name in body:
            payload[name] = copy.deepcopy(body[name])
    if effort == "none":
        for name in ("temperature", "top_p", "top_logprobs"):
            if name in body:
                payload[name] = body[name]
    if "response_format" in body:
        fmt = copy.deepcopy(body["response_format"])
        if fmt.get("type") == "json_schema":
            fmt = {"type": "json_schema", **fmt["json_schema"]}
        payload["text"] = {"format": fmt}
    if "verbosity" in body:
        payload.setdefault("text", {})["verbosity"] = body["verbosity"]
    # Compact tool outputs without dropping call IDs or altering function schemas.
    limit = int(os.getenv("LM_PROXY_MAX_TOOL_MESSAGE_CHARS", "200000"))
    for item in payload["input"]:
        if item.get("type") == "function_call_output" and isinstance(item.get("output"), str):
            item["output"] = _truncate_text(item["output"], limit)
    tools = body.get("tools") or []
    if not isinstance(tools, list):
        _bad_request("tools must be a list")
    if tools:
        payload["tools"] = []
        for tool in tools:
            if (
                not isinstance(tool, dict)
                or tool.get("type") != "function"
                or not isinstance(tool.get("function"), dict)
            ):
                _bad_request("Only Chat Completions function tools are supported")
            fn = copy.deepcopy(tool["function"])
            fn.setdefault("strict", False)  # Preserve Chat's non-strict default.
            payload["tools"].append({"type": "function", **fn})
    if "tool_choice" in body:
        choice = body["tool_choice"]
        if isinstance(choice, dict):
            if choice.get("type") != "function" or not isinstance(choice.get("function"), dict):
                _bad_request("Invalid named function tool_choice")
            choice = {"type": "function", "name": choice["function"]["name"]}
        payload["tool_choice"] = choice
    return payload


def chat_response(response: Dict[str, Any], model: str) -> Dict[str, Any]:
    """Map all typed output items and usage to the public Chat completion schema."""
    text, refusals, calls = [], [], []
    for item in response.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    text.append(part["text"])
                elif part.get("type") == "refusal":
                    refusals.append(part["refusal"])
        elif item.get("type") == "function_call":
            calls.append(
                {
                    "id": item["call_id"],
                    "type": "function",
                    "function": {"name": item["name"], "arguments": item["arguments"]},
                }
            )
    message = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        message["tool_calls"] = calls
    if refusals:
        message["refusal"] = "".join(refusals)
    reason = "tool_calls" if calls else "stop"
    if response.get("status") == "incomplete":
        reason = (
            "length"
            if response.get("incomplete_details", {}).get("reason") == "max_output_tokens"
            else "content_filter"
        )
    raw = response.get("usage") or {}
    usage = {
        "prompt_tokens": raw.get("input_tokens", 0),
        "completion_tokens": raw.get("output_tokens", 0),
        "total_tokens": raw.get("total_tokens", 0),
        "prompt_tokens_details": raw.get("input_tokens_details", {}),
        "completion_tokens_details": raw.get("output_tokens_details", {}),
    }
    return {
        "id": response.get("id", ""),
        "object": "chat.completion",
        "created": int(response.get("created_at") or time.time()),
        "model": response.get("model", model),
        "choices": [{"index": 0, "message": message, "finish_reason": reason}],
        "usage": usage,
    }


async def _remember(body: Dict[str, Any], response: Dict[str, Any], output: List[Dict[str, Any]]) -> None:
    completion = chat_response(response, body["model"])
    message = completion["choices"][0]["message"]
    if len(stable_json(output).encode()) <= _CACHE_ITEM_BYTES:
        key = _cache_key(body, body["messages"] + [message])
        _REPLAY[key] = (time.monotonic(), copy.deepcopy(output))
        _REPLAY.move_to_end(key)
        while len(_REPLAY) > _CACHE_ENTRIES:
            _REPLAY.popitem(last=False)
    debug_log("openai_response_usage", model=body["model"], **completion["usage"])

    async def persist():
        try:
            await asyncio.wait_for(
                _persist_memory_best_effort(
                    _derive_session_id(body, body["messages"]),
                    body["model"],
                    body["messages"],
                    message.get("content") or "",
                    message.get("tool_calls"),
                ),
                timeout=10,
            )
        except Exception:
            debug_log("openai_memory_persist_failed")

    if config._MEMORY_PERSIST_ENABLED:
        task = asyncio.create_task(persist())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _inject_memory(body: Dict[str, Any], payload: Dict[str, Any]) -> None:
    """Add bounded optional context while preserving every caller tool call/result."""
    if not config._MEMORY_INJECT_ENABLED or config._memory_retrieval is None:
        return
    try:
        query = next((m.get("content", "") for m in reversed(body["messages"]) if m.get("role") == "user"), "")
        assembled = await asyncio.wait_for(
            config._memory_retrieval.assemble_memory(
                _derive_session_id(body, body["messages"]), query_text=query[:400] if isinstance(query, str) else ""
            ),
            timeout=2,
        )
        if assembled and assembled.assembled_text:
            payload["input"].insert(
                0,
                {
                    "role": "developer",
                    "content": "Prior conversation context (reference data):\n<memory>\n"
                    + _truncate_text(assembled.assembled_text, 2000)
                    + "\n</memory>",
                },
            )
    except Exception:
        debug_log("openai_memory_injection_failed")


async def _search(body: Dict[str, Any], call: Dict[str, Any]) -> str:
    try:
        args = json.loads(call["arguments"])
        query = args["query"]
        if not isinstance(query, str) or not query.strip():
            return "Search requires a nonempty query."
        vector = await config._memory_retrieval.get_embedding(query)
        if not vector:
            return "Codebase search unavailable: local embeddings unavailable."
        hits = await config._memory_store.search_codebase(
            project_id=body["project_id"], query_vector=vector, query_text=query, k=5
        )
        return _truncate_text(stable_json(hits), 16000)
    except Exception:
        return "Codebase search unavailable. Continue using other available evidence."


async def _post(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(f"{config.OPENAI_API_BASE}/responses", headers=_headers(), json=payload)
        if response.is_error:
            raise HTTPException(
                response.status_code, "OpenAI request rejected; check model access and request parameters"
            )
        result = response.json()
        if result.get("status") in {"failed", "cancelled"}:
            raise HTTPException(502, "OpenAI response failed")
        return result
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "OpenAI request timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "OpenAI transport failed") from exc


async def list_models() -> JSONResponse:
    """Discover models with the cloud credential, never via LM Studio admin APIs."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(f"{config.OPENAI_API_BASE}/models", headers=_headers())
        if response.is_error:
            raise HTTPException(response.status_code, "OpenAI model discovery failed")
        return JSONResponse(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "OpenAI model discovery unavailable") from exc


def _chunk(completion: Dict[str, Any], delta: Dict[str, Any], finish: str | None = None) -> str:
    return (
        "data: "
        + stable_json(
            {
                "id": completion["id"],
                "object": "chat.completion.chunk",
                "created": completion["created"],
                "model": completion["model"],
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
        )
        + "\n\n"
    )


def _usage_chunk(completion: Dict[str, Any]) -> str:
    return (
        "data: "
        + stable_json(
            {
                **{k: completion[k] for k in ("id", "created", "model", "usage")},
                "object": "chat.completion.chunk",
                "choices": [],
            }
        )
        + "\n\n"
    )


async def _stream(body: Dict[str, Any], payload: Dict[str, Any]) -> StreamingResponse:
    client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        request = client.build_request("POST", f"{config.OPENAI_API_BASE}/responses", headers=_headers(), json=payload)
        upstream = await client.send(request, stream=True)
        if upstream.is_error:
            await upstream.aclose()
            raise HTTPException(upstream.status_code, "OpenAI streaming request rejected")
    except BaseException as exc:
        await client.aclose()
        if isinstance(exc, httpx.HTTPError):
            raise HTTPException(502, "OpenAI streaming transport failed") from exc
        raise

    async def events():
        meta = {"id": "", "created": int(time.time()), "model": body["model"]}
        indexes = {}
        completed = False
        try:
            async for line in upstream.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    continue
                event = json.loads(data)
                kind = event.get("type")
                if kind == "response.created":
                    response = event["response"]
                    meta.update(
                        id=response["id"],
                        created=int(response.get("created_at") or time.time()),
                        model=response.get("model", body["model"]),
                    )
                    yield _chunk(meta, {"role": "assistant", "content": ""})
                elif kind in {"response.output_text.delta", "response.refusal.delta"}:
                    field = "content" if kind == "response.output_text.delta" else "refusal"
                    yield _chunk(meta, {field: event["delta"]})
                elif kind == "response.output_item.added" and event["item"].get("type") == "function_call":
                    item = event["item"]
                    index = len(indexes)
                    indexes[event["output_index"]] = index
                    yield _chunk(
                        meta,
                        {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "id": item["call_id"],
                                    "type": "function",
                                    "function": {"name": item["name"], "arguments": ""},
                                }
                            ]
                        },
                    )
                elif kind == "response.function_call_arguments.delta":
                    yield _chunk(
                        meta,
                        {
                            "tool_calls": [
                                {"index": indexes[event["output_index"]], "function": {"arguments": event["delta"]}}
                            ]
                        },
                    )
                elif kind in {"response.completed", "response.incomplete"}:
                    response = event["response"]
                    completion = chat_response(response, body["model"])
                    await _remember(body, response, response.get("output", []))
                    yield _chunk(completion, {}, completion["choices"][0]["finish_reason"])
                    if (body.get("stream_options") or {}).get("include_usage"):
                        yield _usage_chunk(completion)
                    completed = True
                    break
                elif kind in {"error", "response.failed", "response.cancelled"}:
                    raise RuntimeError("upstream response failed")
            if not completed:
                raise RuntimeError("upstream stream ended before completion")
        except Exception:
            yield 'data: {"error":{"message":"OpenAI stream failed before completion","type":"proxy_stream_error"}}\n\n'
        finally:
            await upstream.aclose()
            await client.aclose()
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


async def forward_completion(body: Dict[str, Any]) -> Any:
    """Forward cloud inference; optional proxy-owned search uses a bounded tool loop."""
    _headers()
    if not isinstance(body, dict):
        _bad_request("Request must be an object")
    body = copy.deepcopy(body)
    requested = body.get("model") or config.OPENAI_MODEL
    if not isinstance(requested, str):
        _bad_request("model must be a string")
    body["model"] = parse_model_aliases().get(requested, requested)
    try:
        payload = build_payload(body)
    except (KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(400, "Malformed Chat Completions request") from exc
    await _inject_memory(body, payload)
    # Never hijack a caller-owned tool with the same name. Explicit project scope
    # avoids guessing which repository the graph search should query.
    internal_search = bool(
        config.OPENAI_CODEBASE_SEARCH
        and body.get("project_id")
        and config._memory_retrieval
        and config._memory_store
        and not any(t["name"] == "codebase_search" for t in payload.get("tools", []))
    )
    if body.get("stream") and not internal_search:
        return await _stream(body, payload)
    if internal_search:
        payload.setdefault("tools", []).append(copy.deepcopy(_SEARCH_TOOL))
    payload["stream"] = False
    replay = []
    total_usage: Dict[str, Any] = {}
    # Internal search requests are buffered so private tool calls never leak to clients.
    for turn in range(5):
        response = await _post(payload)
        for key, value in (response.get("usage") or {}).items():
            if isinstance(value, int):
                total_usage[key] = total_usage.get(key, 0) + value
            elif isinstance(value, dict):
                details = total_usage.setdefault(key, {})
                for name, count in value.items():
                    if isinstance(count, int):
                        details[name] = details.get(name, 0) + count
        output = response.get("output", [])
        private_calls = [
            item
            for item in output
            if internal_search and item.get("type") == "function_call" and item.get("name") == "codebase_search"
        ]
        if not private_calls:
            replay.extend(output)
            break
        if turn == 4:
            raise HTTPException(502, "Codebase search exceeded the tool round limit")
        results = []
        for call in private_calls:
            try:
                result = await asyncio.wait_for(_search(body, call), timeout=15)
            except asyncio.TimeoutError:
                result = "Codebase search timed out. Continue using other available evidence."
            results.append({"type": "function_call_output", "call_id": call["call_id"], "output": result})
        replay.extend(output + results)
        public_calls = [item for item in output if item.get("type") == "function_call" and item not in private_calls]
        if public_calls:
            response = {**response, "output": [item for item in output if item not in private_calls]}
            break
        payload["input"].extend(output + results)
        # A forced search must not force every subsequent turn to repeat it.
        payload["tool_choice"] = "auto"
    response = {**response, "usage": total_usage}
    await _remember(body, response, replay)
    completion = chat_response(response, body["model"])
    if not body.get("stream"):
        return JSONResponse(completion)

    async def buffered_events():
        message = dict(completion["choices"][0]["message"])
        if "tool_calls" in message:
            message["tool_calls"] = [{"index": index, **call} for index, call in enumerate(message["tool_calls"])]
        yield _chunk(completion, message)
        yield _chunk(completion, {}, completion["choices"][0]["finish_reason"])
        if (body.get("stream_options") or {}).get("include_usage"):
            yield _usage_chunk(completion)
        yield "data: [DONE]\n\n"

    return StreamingResponse(buffered_events(), media_type="text/event-stream")

"""proxy/handlers_chat.py — chat completion forwarding."""

import asyncio
import json
import os
from typing import Any, Dict, List

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from proxy.config import (
    _ENABLE_EMBEDDINGS,
    _memory_retrieval,
    _memory_store,
    ENABLE_DEBUG_LOGGING,
    OPENAI_BASE,
)
from proxy.logging import debug_log, stable_json
from proxy.state import STATE, save_state
from proxy.handlers_memory import _derive_session_id, _persist_memory_best_effort
from proxy.handlers_utils import history_key


_TOOL_CALL_HISTORY: Dict[str, List[str]] = {}


async def forward_openai_chat_completion(body: Dict[str, Any]) -> Any:
    timeout = httpx.Timeout(900.0, connect=30.0)

    # Flatten structured tool_choice because LM Studio universally rejects objects natively.
    # We use "auto" instead of "required" to give the model breathing room to output plain text
    # if it needs to, preventing "toxic stops" mid-sentence caused by grammar enforcers.
    if "tool_choice" in body:
        tc = body["tool_choice"]
        if isinstance(tc, dict):
            body["tool_choice"] = "auto"

    # Stop stripping moved to chat_completions for global coverage

    stream = bool(body.get("stream", False))

    # stream_options stripping moved to top-level route

    debug_log(
        "route_openai_chat_completions",
        stream=stream,
        has_tools=isinstance(body.get("tools"), list) and bool(body.get("tools")),
        message_count=len(body.get("messages", []))
        if isinstance(body.get("messages"), list)
        else None,
        model=body.get("model"),
    )

    if stream:
        from proxy.handlers_streaming import stream_openai_compatible_response

        return await stream_openai_compatible_response(
            f"{OPENAI_BASE}/chat/completions", body, body.get("messages", [])
        )

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{OPENAI_BASE}/chat/completions", json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        response_json = r.json()

    # --- Stateful tracking for the stateless route ---
    # This allows the *next* turn to use the stateful Responses API
    sid = response_json.get("id")
    choices = response_json.get("choices", [])
    if sid and choices:
        m = choices[0].get("message", {})
        STATE[history_key(body.get("messages", []) + [m])] = sid
        save_state()
        try:
            session_id = _derive_session_id(body, body.get("messages", []))
            asyncio.create_task(
                _persist_memory_best_effort(
                    session_id=session_id,
                    model=body.get("model", ""),
                    messages=body.get("messages", []),
                    assistant_text=m.get("content") or "",
                    tool_calls=m.get("tool_calls"),
                )
            )
        except Exception as exc:
            debug_log("memory_persist_schedule_failed", error=str(exc))

    # --- Always-on: log usage stats for monitoring ---
    usage = response_json.get("usage", {})
    if isinstance(usage, dict):
        debug_log(
            "openai_completion_stats",
            model=body.get("model"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
        # Log a warning when context is getting full (>80 % of a common 32k window).
        # Adjust threshold via LM_PROXY_CONTEXT_WARN_TOKENS (default 25600 = 80% of 32k).
        warn_threshold = int(os.getenv("LM_PROXY_CONTEXT_WARN_TOKENS", "25600"))
        pt = usage.get("prompt_tokens") or 0
        if isinstance(pt, int) and pt > warn_threshold:
            print(
                f"[lm-proxy] context_pressure model={body.get('model')} prompt_tokens={pt}",
                flush=True,
            )

    # --- Item 1: Detect and recover from invalid / malformed tool calls ---
    # When LM Studio produces incomplete JSON for tool arguments (context overflow),
    # OpenCode sees `undefined` for content/filePath and loops forever.
    # Instead, return a synthetic assistant message asking the model to retry.
    choices = response_json.get("choices", [])
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            msg = first_choice.get("message", {})
            tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else None

            # --- NEW: Intercept Codebase Search Tool ---
            if isinstance(tool_calls, list) and len(tool_calls) == 1:
                tc = tool_calls[0]
                fn = tc.get("function", {})
                if fn.get("name") == "codebase_search":
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                        query = args.get("query", "")

                        debug_log("intercepting_codebase_search", query=query)

                        # 1. Get embedding
                        query_vec = None
                        if _memory_retrieval and query:
                            query_vec = await _memory_retrieval.get_embedding(query)

                        # 2. Run search
                        results_text = "No results found."
                        if query_vec and _memory_store:
                            # We need a project ID. In OpenCode, the first system prompt usually has the workspace path.
                            # We'll try to infer it from the first few messages, or just use a hash of the session ID.
                            session_id = _derive_session_id(
                                body, body.get("messages", [])
                            )
                            project_id_guess = (
                                session_id.split("-")[0]
                                if "-" in session_id
                                else session_id
                            )

                            hits = await _memory_store.search_codebase(
                                project_id=project_id_guess,
                                query_vector=query_vec,
                                query_text=query,
                                k=5,
                            )

                            if hits:
                                parts = [
                                    "Here are the results from the codebase search. Please review them carefully to answer the user's request. DO NOT truncate your response. List all relevant findings:\n"
                                ]
                                for hit in hits:
                                    parts.append(
                                        f"```python\n// File: {hit['file_path']}\n{hit['content']}\n```"
                                    )
                                results_text = (
                                    "\n\n".join(parts)
                                    + "\n\nSearch complete. Please output your analysis now."
                                )

                        # 3. Append the tool call and response to the messages array
                        new_body = dict(body)
                        new_messages = list(new_body.get("messages", []))

                        # Add the assistant's tool call message
                        new_messages.append(
                            {
                                "role": "assistant",
                                "content": "I need to search the codebase to answer this.",
                                "tool_calls": [tc],
                            }
                        )

                        # Add the tool's response message
                        new_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id"),
                                "name": "codebase_search",
                                "content": results_text,
                            }
                        )

                        new_body["messages"] = new_messages

                        # Preserve token limits so the model doesn't truncate the post-search explanation
                        if "max_tokens" in body:
                            new_body["max_tokens"] = body["max_tokens"]
                        if "max_output_tokens" in body:
                            new_body["max_output_tokens"] = body["max_output_tokens"]

                        # 4. Recursively call the LLM to get the *actual* answer
                        debug_log(
                            "codebase_search_returning_to_llm",
                            hits=len(hits) if "hits" in locals() and hits else 0,
                        )
                        return await forward_openai_chat_completion(new_body)

                    except Exception as _search_exc:
                        debug_log("codebase_search_failed", error=str(_search_exc))

            # --- NEW: loop guard (per-session) ---
            session_key = _derive_session_id(body, body.get("messages", []))

            history = _TOOL_CALL_HISTORY.setdefault(session_key, [])

            if isinstance(tool_calls, list) and tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    sig = str(fn.get("name")) + str(fn.get("arguments"))

                    history.append(sig)
                    # keep last 6
                    if len(history) > 6:
                        del history[:-6]

                    if history.count(sig) >= 3:
                        debug_log("loop_guard_triggered", tool=fn.get("name"))

                        return JSONResponse(
                            {
                                "id": "chatcmpl_loop_guard",
                                "object": "chat.completion",
                                "created": 0,
                                "model": body.get("model", ""),
                                "choices": [
                                    {
                                        "index": 0,
                                        "message": {
                                            "role": "assistant",
                                            "content": "Repeated identical tool calls detected. Try a different approach.",
                                        },
                                        "finish_reason": "stop",
                                    }
                                ],
                            }
                        )
            if isinstance(tool_calls, list) and tool_calls:
                bad_calls = []
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function", {})
                    args = fn.get("arguments") if isinstance(fn, dict) else None
                    if args is None:
                        bad_calls.append(tc.get("id", "?"))
                        continue
                    try:
                        json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        bad_calls.append(tc.get("id", "?"))

                if bad_calls:
                    print(
                        f"[lm-proxy] invalid_tool_call_args model={body.get('model')} bad_ids={bad_calls}",
                        flush=True,
                    )
                    # Replace the malformed tool_calls response with a plain text
                    # recovery message so the client can retry cleanly.
                    recovery_msg = (
                        "I attempted a tool call but produced invalid arguments "
                        "(likely due to context length pressure). "
                        "Please retry the operation with explicit string values for all required parameters."
                    )
                    recovery_response = {
                        "id": response_json.get("id", "chatcmpl_recovery"),
                        "object": "chat.completion",
                        "created": response_json.get("created", 0),
                        "model": response_json.get("model", body.get("model", "")),
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": recovery_msg,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": usage,
                    }
                    return JSONResponse(recovery_response)

    return JSONResponse(response_json)

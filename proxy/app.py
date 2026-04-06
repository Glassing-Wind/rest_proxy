import sys
from typing import Any, Dict, List

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from proxy.config import (
    _MEMORY_ENABLED,
    _ENABLE_PERSISTENCE,
    _MEMORY_ENABLE_INJECT,
    _memory_store,
    _memory_retrieval,
    LM_BASE,
    OPENAI_BASE,
    STATE_FILE,
    ENABLE_PROXY_FILTERING,
    ENABLE_DEBUG_LOGGING,
    MODEL_ALIASES_ENV,
    FALLBACK_MODEL,
    ENABLE_MODEL_VALIDATION,
    USE_LOCAL_MODELS_FOR_V1,
    LOOP_DETECT_THRESHOLD,
)

import os
from pathlib import Path

_RUNTIME_DIR = Path(__file__).resolve().parents[1] / ".runtime"
_PROXY_PID_FILE = _RUNTIME_DIR / "proxy.pid"

app = FastAPI(title="LM Studio Stateful Chat Proxy")


@app.on_event("startup")
async def _startup_event() -> None:
    """Run idempotent schema bootstrap and open the Postgres connection pool on startup."""
    # Write PID for the 'Hot Reload' supervisor
    try:
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        _PROXY_PID_FILE.write_text(str(os.getpid()))
    except OSError:
        pass

    if _MEMORY_ENABLED and _memory_store is not None and _ENABLE_PERSISTENCE:
        # Open Neo4j connection / bootstrap schema
        try:
            await _memory_store.open_pool()
        except Exception as _pool_exc:
            print(
                f"[lm-proxy] memory_init_error error={_pool_exc}",
                file=sys.stderr,
                flush=True,
            )

@app.on_event("shutdown")
async def _shutdown_event() -> None:
    """Clean up the proxy PID file on exit."""
    try:
        if _PROXY_PID_FILE.exists():
            _PROXY_PID_FILE.unlink()
    except OSError:
        pass


from proxy.logging import debug_log
from proxy.models import (
    extract_model_keys,
    fetch_lmstudio_models,
    resolve_model_name,
    build_local_llm_models,
)
from proxy.handlers import (
    _derive_session_id,
    _inject_memory_into_messages,
    request_uses_openai_tools,
    _detect_and_break_tool_loop,
    _compact_tool_definitions,
    filter_messages_for_proxy,
    forward_responses_api_completion,
)
from proxy.state import STATE, load_state

load_state()


@app.get("/v1/models")
async def list_models() -> Any:
    if USE_LOCAL_MODELS_FOR_V1:
        payload = await fetch_lmstudio_models()
        local_models = build_local_llm_models(payload)
        data = [
            {
                "id": item["id"],
                "object": "model",
                "created": 0,
                "owned_by": item["publisher"] or "lmstudio",
                "x_display_name": item["name"],
                "x_params": item["params"],
                "x_format": item["format"],
                "x_quantization": item["quantization"],
                "x_context_length": item["context_length"],
                "x_vision": item["vision"],
                "x_tool_use": item["tool_use"],
                "x_loaded": item["loaded"],
            }
            for item in local_models
        ]
        return JSONResponse({"object": "list", "data": data})

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{OPENAI_BASE}/models")
        r.raise_for_status()
        return JSONResponse(r.json())


@app.get("/api/v1/models")
async def list_stateful_models() -> Any:
    return JSONResponse(await fetch_lmstudio_models())


@app.get("/v1/local-models")
async def list_local_llm_models() -> Any:
    payload = await fetch_lmstudio_models()
    return JSONResponse({"models": build_local_llm_models(payload)})


# --- Lightweight debug endpoints
@app.get("/health")
async def health() -> Any:
    return JSONResponse(
        {
            "ok": True,
            "lm_base": LM_BASE,
            "openai_base": OPENAI_BASE,
            "filtering_enabled": ENABLE_PROXY_FILTERING,
            "debug_logging_enabled": ENABLE_DEBUG_LOGGING,
            "v1_models_local_enabled": USE_LOCAL_MODELS_FOR_V1,
            "state_entries": len(STATE),
            "state_file": str(STATE_FILE),
        }
    )


@app.get("/debug/state")
async def debug_state() -> Any:
    if not ENABLE_DEBUG_LOGGING:
        raise HTTPException(
            status_code=404,
            detail="Debug state endpoint is disabled. Set LM_PROXY_DEBUG=true to enable it.",
        )

    sample_keys = list(STATE.keys())[-10:]
    return JSONResponse(
        {
            "entries": len(STATE),
            "state_file": str(STATE_FILE),
            "sample_keys": sample_keys,
            "state_file": str(STATE_FILE),
        }
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body = await request.json()

    # OpenAI clients often send stream_options even for non-streaming calls.
    # We strip it here so the messages hash remains consistent for history_key.
    if "stream_options" in body:
        body = dict(body)
        body.pop("stream_options", None)

    # Log and strip `stop` tokens to see what OpenCode is sending
    if "stop" in body:
        debug_log("intercepted_stop_tokens", stop=body["stop"])
        body = dict(body)
        body.pop("stop", None)

    requested_model = body["model"]
    raw_messages = body["messages"]
    requested_stream = bool(body.get("stream", False))

    debug_log(
        "dumping_raw_messages",
        first_message=raw_messages[0] if raw_messages else None,
        last_message=raw_messages[-1] if raw_messages else None,
    )

    available_model_keys: List[str] = []
    if ENABLE_MODEL_VALIDATION or MODEL_ALIASES_ENV or FALLBACK_MODEL:
        try:
            models_payload = await fetch_lmstudio_models()
            available_model_keys = extract_model_keys(models_payload)
        except Exception as exc:
            debug_log("model_list_fetch_failed", error=str(exc))

    model = resolve_model_name(requested_model, available_model_keys)
    if model != requested_model:
        body = dict(body)
        body["model"] = model

    debug_log(
        "request_received",
        model=model,
        requested_model=requested_model,
        stream=requested_stream,
        raw_message_count=len(raw_messages) if isinstance(raw_messages, list) else None,
        has_tools=isinstance(body.get("tools"), list) and bool(body.get("tools")),
        has_store_override="store" in body,
    )

    # --- GLOBAL INJECTION: Ensure codebase_search is always available ---
    if isinstance(body.get("tools"), list) or body.get("tools") is None:
        body = dict(body)
        if "tools" not in body or body["tools"] is None:
            body["tools"] = []

        existing_tool_names = [
            t.get("function", {}).get("name")
            for t in body["tools"]
            if t.get("type") == "function"
        ]
        if "codebase_search" not in existing_tool_names:
            body["tools"].append(
                {
                    "type": "function",
                    "function": {
                        "name": "codebase_search",
                        "description": "Searches the entire codebase for specific logic, implementations, or usages based on natural language. Use this to find where a variable/class is defined or used, or to understand the project architecture.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "What you are looking for. E.g. 'Where is the API URL configured?'",
                                }
                            },
                            "required": ["query"],
                        },
                    },
                }
            )
            body["tools"] = _compact_tool_definitions(body["tools"])
            debug_log("global_tool_injection", tool="codebase_search")

    uses_openai_tools = request_uses_openai_tools(body, raw_messages)
    stream = requested_stream and not uses_openai_tools
    debug_log(
        "route_decision",
        uses_openai_tools=uses_openai_tools,
        requested_stream=requested_stream,
        effective_stream=stream,
    )
    if uses_openai_tools:
        body = dict(body)

        # Apply filtering/injection for tools Turn
        _tool_session_id = _derive_session_id(body, raw_messages)
        filtered_for_tools = filter_messages_for_proxy(_tool_session_id, raw_messages)

        if (
            _MEMORY_ENABLED
            and _MEMORY_ENABLE_INJECT
            and _memory_retrieval is not None
            and not body.get("previous_response_id")
        ):
            try:
                filtered_for_tools = await _inject_memory_into_messages(
                    _tool_session_id, filtered_for_tools
                )
            except Exception as _inj_exc:
                debug_log("memory_inject_failed", error=str(_inj_exc))

        if LOOP_DETECT_THRESHOLD > 0:
            filtered_for_tools = _detect_and_break_tool_loop(
                filtered_for_tools, threshold=LOOP_DETECT_THRESHOLD
            )

        body["messages"] = filtered_for_tools
        debug_log(
            "openai_tool_route_messages_ready",
            raw_message_count=len(raw_messages),
            filtered_message_count=len(filtered_for_tools),
            filtering_applied=True,
        )

        if not body.get("stream") and "stream_options" in body:
            body.pop("stream_options", None)

        return await forward_responses_api_completion(
            body, history_messages=raw_messages
        )

    # Use the refactored stateful Response API route for all conversation turns
    return await forward_responses_api_completion(body, history_messages=raw_messages)


@app.post("/v1/embeddings")
async def embeddings(body: Dict[str, Any]) -> Any:
    # Pass through embeddings request to LM Studio's /v1/embeddings.
    timeout = httpx.Timeout(60.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{OPENAI_BASE}/embeddings", json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return JSONResponse(r.json())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

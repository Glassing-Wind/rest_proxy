"""Offline OpenAI adapter contracts. No API key or external services required.

Run with the project's Python: python test_openai_provider.py.
"""

import asyncio
import copy
import importlib
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["LM_PROXY_MEMORY_ENABLED"] = "0"
os.environ["LM_PROXY_PROVIDER"] = "openai"
os.environ["LM_PROXY_DEBUG"] = "false"

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from proxy import config
from proxy import openai_provider as provider

app_module = importlib.import_module("proxy.app")
_REAL_CLIENT = httpx.AsyncClient
TOOL = {
    "type": "function",
    "function": {
        "name": "lookup",
        "parameters": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
    },
}
CALL = {"type": "function_call", "id": "fc_1", "call_id": "call_real", "name": "lookup", "arguments": '{"q":"x"}'}
TEXT = {
    "type": "message",
    "role": "assistant",
    "content": [{"type": "output_text", "text": "answer", "annotations": []}],
}


def response(output=None, **updates):
    return {
        "id": "resp_1",
        "model": "gpt-6-sol",
        "created_at": 123,
        "status": "completed",
        "output": output if output is not None else [TEXT],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "output_tokens_details": {"reasoning_tokens": 2},
            "input_tokens_details": {"cached_tokens": 3},
        },
        **updates,
    }


def body(**updates):
    return {
        "model": "gpt-6-sol",
        "messages": [{"role": "system", "content": "Be precise"}, {"role": "user", "content": "Find x"}],
        **updates,
    }


class OpenAIContracts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        provider._REPLAY.clear()
        self.patches = [
            patch.object(config, "OPENAI_API_KEY", "test-key"),
            patch.object(config, "OPENAI_CODEBASE_SEARCH", False),
            patch.object(config, "OPENAI_REASONING_EFFORT", "low"),
            patch.object(config, "MODEL_ALIASES_ENV", ""),
            patch.object(provider, "_persist_memory_best_effort", new_callable=AsyncMock),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def transport(self, handler):
        return patch.object(
            provider.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
        )

    def test_parameters_schema_and_no_mutation(self):
        source = body(
            tools=[TOOL],
            reasoning_effort="high",
            max_completion_tokens=900,
            temperature=0.5,
            top_p=0.9,
            logprobs=True,
            tool_choice={"type": "function", "function": {"name": "lookup"}},
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": {"type": "object"}},
            },
        )
        saved = copy.deepcopy(source)
        payload = provider.build_payload(source)
        self.assertEqual(source, saved)
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertEqual(payload["max_output_tokens"], 900)
        self.assertNotIn("temperature", payload)
        self.assertNotIn("top_p", payload)
        self.assertNotIn("logprobs", payload)
        self.assertEqual(payload["tools"][0]["parameters"]["additionalProperties"], False)
        self.assertFalse(payload["tools"][0]["strict"])
        self.assertEqual(payload["tool_choice"], {"type": "function", "name": "lookup"})
        self.assertEqual(payload["text"]["format"]["name"], "result")
        self.assertFalse(payload["store"])

    def test_reasoning_none_and_minimal(self):
        self.assertEqual(provider.build_payload(body(reasoning_effort="none", temperature=0.2))["temperature"], 0.2)
        self.assertEqual(provider.build_payload(body(reasoning_effort="minimal"))["reasoning"]["effort"], "low")
        self.assertEqual(
            provider.build_payload(body(model="gpt-6-astra", reasoning_effort="none"))["reasoning"]["effort"], "low"
        )

    async def test_full_history_and_encrypted_replay_multiple_results(self):
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
        second = {**CALL, "id": "fc_2", "call_id": "call_2"}
        resp = response([reasoning, CALL, second])
        initial = body()
        await provider._remember(initial, resp, resp["output"])
        assistant = provider.chat_response(resp, initial["model"])["choices"][0]["message"]
        follow = body(
            messages=initial["messages"]
            + [
                assistant,
                {"role": "tool", "tool_call_id": "call_real", "content": "one"},
                {"role": "tool", "tool_call_id": "call_2", "content": "two"},
            ]
        )
        items = provider.build_payload(follow)["input"]
        self.assertIn(reasoning, items)
        self.assertEqual(
            [x["call_id"] for x in items if x.get("type") == "function_call_output"], ["call_real", "call_2"]
        )
        self.assertEqual(items[0], {"role": "system", "content": "Be precise"})
        for change in ({"model": "gpt-6-luna"}, {"session_id": "another"}):
            self.assertNotIn(reasoning, provider.build_payload({**follow, **change})["input"])
        provider._REPLAY.clear()
        items = provider.build_payload(follow)["input"]
        self.assertEqual(len([x for x in items if x.get("type") == "function_call"]), 2)

    def test_multimodal_and_refusal(self):
        payload = provider.build_payload(
            body(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Inspect"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA", "detail": "low"}},
                        ],
                    }
                ]
            )
        )
        self.assertEqual(payload["input"][0]["content"][1]["type"], "input_image")
        completion = provider.chat_response(
            response([{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]), "gpt-6-sol"
        )
        self.assertEqual(completion["choices"][0]["message"]["refusal"], "No")

    async def test_auth_defaults_and_response_contract(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=response([CALL]))

        with self.transport(handler):
            result = await provider.forward_completion({"messages": body()["messages"]})
        self.assertEqual(seen[0].headers["authorization"], "Bearer test-key")
        sent = json.loads(seen[0].content)
        self.assertEqual(sent["model"], "gpt-6-sol")
        self.assertTrue(str(seen[0].url).endswith("/v1/responses"))
        completion = json.loads(result.body)
        self.assertEqual(completion["object"], "chat.completion")
        self.assertEqual(completion["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(completion["choices"][0]["message"]["tool_calls"][0]["id"], "call_real")
        self.assertEqual(completion["usage"]["completion_tokens_details"]["reasoning_tokens"], 2)

    async def test_error_never_falls_back(self):
        for status in (400, 401, 429, 500):
            seen = []

            def handler(request):
                seen.append(request)
                return httpx.Response(status, json={"error": {"message": "rejected"}})

            with self.transport(handler), self.assertRaises(HTTPException) as ctx:
                await provider.forward_completion(body())
            self.assertEqual(ctx.exception.status_code, status)
            self.assertEqual(len(seen), 1)

    async def test_stream_tool_ids_usage_and_finish(self):
        events = [
            {"type": "response.created", "response": response([])},
            {"type": "response.output_item.added", "output_index": 1, "item": {**CALL, "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"q":'},
            {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '"x"}'},
            {
                "type": "response.output_item.added",
                "output_index": 3,
                "item": {**CALL, "call_id": "call_second", "arguments": ""},
            },
            {"type": "response.function_call_arguments.delta", "output_index": 3, "delta": "{}"},
            {
                "type": "response.completed",
                "response": response([CALL, {**CALL, "call_id": "call_second", "arguments": "{}"}]),
            },
        ]
        wire = "".join("data: " + json.dumps(e) + "\n\n" for e in events)

        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer test-key")
            self.assertTrue(json.loads(request.content)["stream"])
            return httpx.Response(200, text=wire)

        with self.transport(handler):
            result = await provider.forward_completion(body(stream=True, stream_options={"include_usage": True}))
            chunks = [part async for part in result.body_iterator]
        data = [json.loads(c[6:]) for c in chunks if c != "data: [DONE]\n\n"]
        self.assertEqual(data[1]["choices"][0]["delta"]["tool_calls"][0]["id"], "call_real")
        self.assertEqual(data[4]["choices"][0]["delta"]["tool_calls"][0]["index"], 1)
        self.assertEqual(data[-2]["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(data[-1]["usage"]["total_tokens"], 15)
        self.assertEqual(chunks[-1], "data: [DONE]\n\n")

    async def test_stream_incomplete_and_truncation(self):
        for terminal, expected in (
            (
                {
                    "type": "response.incomplete",
                    "response": response(status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
                },
                '"finish_reason":"length"',
            ),
            ({"type": "response.failed"}, "proxy_stream_error"),
            (None, "proxy_stream_error"),
        ):
            wire = "data: " + json.dumps({"type": "response.created", "response": response([])}) + "\n\n"
            if terminal:
                wire += "data: " + json.dumps(terminal) + "\n\n"
            with self.transport(lambda _: httpx.Response(200, text=wire)):
                result = await provider.forward_completion(body(stream=True))
                output = "".join([part async for part in result.body_iterator])
            self.assertIn(expected, output.replace(" ", ""))

    async def test_internal_search_round_trip_and_client_ownership(self):
        search_call = {**CALL, "name": "codebase_search", "arguments": '{"query":"x"}'}
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json=response([search_call] if len(seen) == 1 else [TEXT]))

        with (
            patch.object(config, "OPENAI_CODEBASE_SEARCH", True),
            patch.object(config, "_memory_store", object()),
            patch.object(config, "_memory_retrieval", object()),
            patch.object(provider, "_search", new_callable=AsyncMock, return_value="source excerpt") as search,
            self.transport(handler),
        ):
            result = await provider.forward_completion(body(project_id="project-1", stream=True))
            output = "".join([part async for part in result.body_iterator])
            self.assertIn("answer", output)
            self.assertNotIn("call_real", output)
            self.assertEqual(search.await_count, 1)
            self.assertEqual(seen[1]["input"][-1]["output"], "source excerpt")
            self.assertFalse(seen[0]["stream"])
        seen.clear()
        tool = copy.deepcopy(TOOL)
        tool["function"]["name"] = "codebase_search"
        with patch.object(config, "OPENAI_CODEBASE_SEARCH", True), self.transport(handler):
            result = await provider.forward_completion(body(tools=[tool], project_id="project-1"))
        self.assertEqual(json.loads(result.body)["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(len(seen), 1)

    async def test_optional_memory_failure_is_nonfatal(self):
        with (
            patch.object(
                provider, "_persist_memory_best_effort", new_callable=AsyncMock, side_effect=RuntimeError("offline")
            ),
            self.transport(lambda _: httpx.Response(200, json=response())),
        ):
            result = await provider.forward_completion(body())
        self.assertEqual(result.status_code, 200)

    async def test_missing_key_and_invalid_request(self):
        with patch.object(config, "OPENAI_API_KEY", ""), self.assertRaises(HTTPException) as ctx:
            await provider.forward_completion(body())
        self.assertEqual(ctx.exception.status_code, 503)
        for invalid in (body(n=2), body(messages=[]), body(tools=[{}]), body(reasoning_effort="ultra")):
            with self.assertRaises(HTTPException) as ctx:
                await provider.forward_completion(invalid)
            self.assertEqual(ctx.exception.status_code, 400)

    async def test_background_persistence_does_not_block_response(self):
        gate = asyncio.Event()

        async def persist(*args):
            await gate.wait()
            raise RuntimeError("database offline")

        with (
            patch.object(config, "_MEMORY_PERSIST_ENABLED", True),
            patch.object(provider, "_persist_memory_best_effort", side_effect=persist),
            self.transport(lambda _: httpx.Response(200, json=response())),
        ):
            result = await asyncio.wait_for(provider.forward_completion(body()), timeout=0.5)
            self.assertEqual(result.status_code, 200)
            self.assertTrue(provider._BACKGROUND_TASKS)
            gate.set()
            await asyncio.gather(*list(provider._BACKGROUND_TASKS))

    async def test_memory_injection_failure_preserves_tool_history(self):
        retrieval = type("Retrieval", (), {"assemble_memory": AsyncMock(side_effect=RuntimeError("offline"))})()
        request = body(
            messages=body()["messages"]
            + [
                provider.chat_response(response([CALL]), "gpt-6-sol")["choices"][0]["message"],
                {"role": "tool", "tool_call_id": "call_real", "content": "result"},
            ]
        )
        payload = provider.build_payload(request)
        before = copy.deepcopy(payload)
        with patch.object(config, "_MEMORY_INJECT_ENABLED", True), patch.object(config, "_memory_retrieval", retrieval):
            await provider._inject_memory(request, payload)
        self.assertEqual(payload, before)

    async def test_internal_mixed_calls_retained_for_next_turn(self):
        private = {**CALL, "name": "codebase_search", "call_id": "call_private"}
        original = body(project_id="project-1")
        with (
            patch.object(config, "OPENAI_CODEBASE_SEARCH", True),
            patch.object(config, "_memory_store", object()),
            patch.object(config, "_memory_retrieval", object()),
            patch.object(provider, "_search", new_callable=AsyncMock, return_value="private result"),
            self.transport(lambda _: httpx.Response(200, json=response([private, CALL]))),
        ):
            result = await provider.forward_completion(original)
        assistant = json.loads(result.body)["choices"][0]["message"]
        self.assertEqual([c["id"] for c in assistant["tool_calls"]], ["call_real"])
        followup = {
            **original,
            "messages": original["messages"]
            + [assistant, {"role": "tool", "tool_call_id": "call_real", "content": "public result"}],
        }
        items = provider.build_payload(followup)["input"]
        self.assertEqual(
            [i["output"] for i in items if i.get("type") == "function_call_output"], ["private result", "public result"]
        )

    async def test_internal_loop_limit(self):
        private = {**CALL, "name": "codebase_search"}
        with (
            patch.object(config, "OPENAI_CODEBASE_SEARCH", True),
            patch.object(config, "_memory_store", object()),
            patch.object(config, "_memory_retrieval", object()),
            patch.object(provider, "_search", new_callable=AsyncMock, return_value="no result"),
            patch.object(provider, "_post", new_callable=AsyncMock, return_value=response([private])) as post,
            self.assertRaises(HTTPException) as ctx,
        ):
            await provider.forward_completion(body(project_id="project-1"))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(post.await_count, 5)

    async def test_model_discovery_auth(self):
        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer test-key")
            self.assertTrue(str(request.url).endswith("/v1/models"))
            return httpx.Response(200, json={"object": "list", "data": [{"id": "gpt-6-sol"}]})

        with self.transport(handler):
            result = await provider.list_models()
        self.assertEqual(json.loads(result.body)["data"][0]["id"], "gpt-6-sol")

    async def test_cache_bounds_and_expiry(self):
        with patch.object(provider, "_CACHE_ENTRIES", 1):
            await provider._remember(body(session_id="one"), response(), [TEXT])
            await provider._remember(body(session_id="two"), response(), [TEXT])
        self.assertEqual(len(provider._REPLAY), 1)
        with patch.object(provider.time, "monotonic", return_value=10**12):
            provider.build_payload(body())
        self.assertFalse(provider._REPLAY)
        with patch.object(provider, "_CACHE_ITEM_BYTES", 1):
            await provider._remember(body(), response(), [TEXT])
        self.assertFalse(provider._REPLAY)

    def test_tool_output_compaction_keeps_call_id(self):
        source = body(messages=[{"role": "tool", "tool_call_id": "call_1", "content": "a" * 1000}])
        with patch.dict(os.environ, {"LM_PROXY_MAX_TOOL_MESSAGE_CHARS": "80"}):
            result = provider.build_payload(source)["input"][0]
        self.assertEqual(result["call_id"], "call_1")
        self.assertLess(len(result["output"]), 100)
        self.assertEqual(len(source["messages"][0]["content"]), 1000)


class RouteContracts(unittest.TestCase):
    def test_openai_route_and_embedding_isolation(self):
        async def forward(_):
            return provider.JSONResponse({"cloud": True})

        with (
            patch.object(app_module, "INFERENCE_PROVIDER", "openai"),
            patch.object(provider, "forward_completion", side_effect=forward),
            patch.object(app_module, "fetch_lmstudio_models", side_effect=AssertionError("local discovery called")),
        ):
            client = TestClient(app_module.app)
            self.assertEqual(client.post("/v1/chat/completions", json=body()).json(), {"cloud": True})
            seen = []

            def handler(request):
                seen.append(request)
                return httpx.Response(200, json={"data": []})

            with patch.object(
                app_module.httpx,
                "AsyncClient",
                side_effect=lambda **kwargs: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
            ):
                self.assertEqual(client.post("/v1/embeddings", json={"model": "local", "input": "x"}).status_code, 200)
            self.assertTrue(str(seen[0].url).startswith(config.LM_BASE))
            self.assertNotIn("authorization", seen[0].headers)

    def test_local_route_unchanged(self):
        with (
            patch.object(app_module, "INFERENCE_PROVIDER", "lmstudio"),
            patch.object(app_module, "fetch_lmstudio_models", new_callable=AsyncMock, return_value={"models": []}),
            patch.object(
                app_module,
                "forward_responses_api_completion",
                new_callable=AsyncMock,
                return_value=provider.JSONResponse({"local": True}),
            ),
        ):
            result = TestClient(app_module.app).post("/v1/chat/completions", json=body(model="local-model"))
            self.assertEqual(result.json(), {"local": True})


if __name__ == "__main__":
    unittest.main()

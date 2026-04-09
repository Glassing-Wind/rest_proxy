import asyncio
import importlib.util
import sys
import types
import unittest
from unittest import mock


RESPONSES_PATH = "/Users/michaelmarler/Projects/rest_proxy/proxy/handlers_responses.py"
CHAT_PATH = "/Users/michaelmarler/Projects/rest_proxy/proxy/handlers_chat.py"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return _FakeResponse(self._payload)


class _FakeJSONResponse:
    def __init__(self, payload):
        self.payload = payload


def _load_module(module_name: str, path: str, extra_modules: dict[str, object]):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with mock.patch.dict(sys.modules, extra_modules):
        spec.loader.exec_module(module)
    return module


class HandlerPersistenceTests(unittest.TestCase):
    def test_responses_handler_schedules_memory_persist(self):
        captured = {}

        async def _persist(**kwargs):
            captured.update(kwargs)

        httpx_mod = types.ModuleType("httpx")
        httpx_mod.Timeout = lambda *args, **kwargs: None
        httpx_mod.AsyncClient = lambda *args, **kwargs: _FakeClient(
            {
                "id": "resp_123",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
            }
        )
        httpx_mod.HTTPStatusError = Exception

        fastapi_mod = types.ModuleType("fastapi")
        fastapi_mod.HTTPException = Exception
        fastapi_responses_mod = types.ModuleType("fastapi.responses")
        fastapi_responses_mod.JSONResponse = _FakeJSONResponse

        extra = {
            "httpx": httpx_mod,
            "fastapi": fastapi_mod,
            "fastapi.responses": fastapi_responses_mod,
            "proxy.config": types.SimpleNamespace(OPENAI_BASE="http://x/v1", ENABLE_DEBUG_LOGGING=False),
            "proxy.logging": types.SimpleNamespace(debug_log=lambda *a, **k: None),
            "proxy.handlers_memory": types.SimpleNamespace(
                _derive_session_id=lambda body, messages: "sess1",
                _persist_memory_best_effort=_persist,
            ),
            "proxy.state": types.SimpleNamespace(STATE={}, save_state=lambda: None),
            "proxy.handlers_utils": types.SimpleNamespace(history_key=lambda msgs: "hk"),
        }

        module = _load_module("handlers_responses_under_test", RESPONSES_PATH, extra)
        original_create_task = module.asyncio.create_task

        async def _run():
            with mock.patch.object(module.asyncio, "create_task", side_effect=original_create_task):
                await module.forward_responses_api_completion(
                    {"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]},
                    history_messages=[{"role": "user", "content": "hi"}],
                )
                await asyncio.sleep(0)

        asyncio.run(_run())

        self.assertEqual(captured["session_id"], "sess1")
        self.assertEqual(captured["model"], "gpt-test")
        self.assertEqual(captured["assistant_text"], "ok")

    def test_chat_handler_schedules_memory_persist(self):
        captured = {}

        async def _persist(**kwargs):
            captured.update(kwargs)

        httpx_mod = types.ModuleType("httpx")
        httpx_mod.Timeout = lambda *args, **kwargs: None
        httpx_mod.AsyncClient = lambda *args, **kwargs: _FakeClient(
            {
                "id": "chatcmpl_123",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
            }
        )

        fastapi_mod = types.ModuleType("fastapi")
        fastapi_mod.HTTPException = Exception
        fastapi_responses_mod = types.ModuleType("fastapi.responses")
        fastapi_responses_mod.JSONResponse = _FakeJSONResponse

        extra = {
            "httpx": httpx_mod,
            "fastapi": fastapi_mod,
            "fastapi.responses": fastapi_responses_mod,
            "proxy.config": types.SimpleNamespace(
                _ENABLE_EMBEDDINGS=False,
                _memory_retrieval=None,
                _memory_store=None,
                ENABLE_DEBUG_LOGGING=False,
                OPENAI_BASE="http://x/v1",
            ),
            "proxy.logging": types.SimpleNamespace(debug_log=lambda *a, **k: None, stable_json=lambda v: str(v)),
            "proxy.state": types.SimpleNamespace(STATE={}, save_state=lambda: None),
            "proxy.handlers_memory": types.SimpleNamespace(
                _derive_session_id=lambda body, messages: "sess2",
                _persist_memory_best_effort=_persist,
            ),
            "proxy.handlers_utils": types.SimpleNamespace(history_key=lambda msgs: "hk"),
        }

        module = _load_module("handlers_chat_under_test", CHAT_PATH, extra)
        original_create_task = module.asyncio.create_task

        async def _run():
            with mock.patch.object(module.asyncio, "create_task", side_effect=original_create_task):
                await module.forward_openai_chat_completion(
                    {"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}], "stream": False}
                )
                await asyncio.sleep(0)

        asyncio.run(_run())

        self.assertEqual(captured["session_id"], "sess2")
        self.assertEqual(captured["model"], "gpt-test")
        self.assertEqual(captured["assistant_text"], "ok")


if __name__ == "__main__":
    unittest.main()

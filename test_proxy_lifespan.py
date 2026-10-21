"""Offline HTTP pooling and resource lifecycle contracts; no services or keys required."""
# ruff: noqa: E402

import asyncio
import importlib
import os
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["LM_PROXY_MEMORY_ENABLED"] = "0"
os.environ["LM_PROXY_DEBUG"] = "false"

import httpx
import anyio
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from proxy import http_clients
from proxy import openai_provider
from proxy.handlers_streaming import stream_openai_compatible_response

app_module = importlib.import_module("proxy.app")
_REAL_CLIENT = httpx.AsyncClient


class ClientLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def client_factory(self, handler):
        clients = []

        def create(**kwargs):
            client = _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(client)
            return client

        return clients, patch.object(http_clients.httpx, "AsyncClient", side_effect=create)

    async def test_reuse_isolated_providers_and_timeouts(self):
        clients, factory = self.client_factory(lambda request: httpx.Response(200, json={"ok": True}))
        with factory:
            async with http_clients.http_client_lifespan():
                async with http_clients.http_client("lmstudio", timeout=60) as first:
                    await first.get("http://test/models")
                self.assertFalse(first.is_closed)
                async with http_clients.http_client("lmstudio", timeout=httpx.Timeout(60)) as second:
                    self.assertIs(first, second)
                async with http_clients.http_client("openai", timeout=60) as cloud:
                    self.assertIsNot(first, cloud)
                async with http_clients.http_client("lmstudio", timeout=900) as inference:
                    self.assertIsNot(first, inference)
            self.assertTrue(all(client.is_closed for client in clients))
            async with http_clients.http_client_lifespan():
                async with http_clients.http_client("lmstudio", timeout=60) as restarted:
                    self.assertIsNot(first, restarted)
        self.assertTrue(restarted.is_closed)

    async def test_standalone_closes_on_failure_and_cancellation(self):
        for error in (ValueError("failed"), asyncio.CancelledError()):
            with self.assertRaises(type(error)):
                async with http_clients.http_client("lmstudio", timeout=60) as client:
                    raise error
            self.assertTrue(client.is_closed)

    async def test_lmstudio_stream_closes_response_without_closing_pool(self):
        responses = []

        def handler(request):
            response = httpx.Response(200, content=b"data: [DONE]\n\n")
            responses.append(response)
            return response

        clients, factory = self.client_factory(handler)
        with factory:
            async with http_clients.http_client_lifespan():
                for _ in range(2):
                    response = await stream_openai_compatible_response("http://test/chat/completions", {"model": "test"})
                    self.assertIn("[DONE]", "".join([chunk async for chunk in response.body_iterator]))
                self.assertEqual(len(clients), 1)
                self.assertFalse(clients[0].is_closed)
                self.assertTrue(all(response.is_closed for response in responses))
        self.assertTrue(clients[0].is_closed)

    async def test_stream_rejection_preserves_shared_client(self):
        for stream in ("lmstudio", "openai"):
            responses = []

            def handler(request):
                response = httpx.Response(429, content=b"busy")
                responses.append(response)
                return response

            clients, factory = self.client_factory(handler)
            with factory, patch.object(openai_provider.config, "OPENAI_API_KEY", "offline-test"):
                async with http_clients.http_client_lifespan():
                    with self.assertRaises(HTTPException) as caught:
                        if stream == "lmstudio":
                            await stream_openai_compatible_response("http://test/chat/completions", {})
                        else:
                            await openai_provider._stream({"model": "test"}, {})
                    self.assertEqual(caught.exception.status_code, 429)
                    self.assertTrue(responses[0].is_closed)
                    self.assertFalse(clients[0].is_closed)
            self.assertTrue(clients[0].is_closed)

    async def test_openai_stream_early_close_releases_response_only(self):
        responses = []

        def handler(request):
            event = {"type": "response.created", "response": {"id": "offline", "model": "test"}}
            response = httpx.Response(200, content=("data: " + json.dumps(event) + "\n\n").encode())
            responses.append(response)
            return response

        clients, factory = self.client_factory(handler)
        with factory, patch.object(openai_provider.config, "OPENAI_API_KEY", "offline-test"):
            async with http_clients.http_client_lifespan():
                response = await openai_provider._stream({"model": "test"}, {})
                await response.body_iterator.__anext__()
                await response.body_iterator.aclose()
                self.assertTrue(responses[0].is_closed)
                self.assertFalse(clients[0].is_closed)
        self.assertTrue(clients[0].is_closed)

    async def test_other_event_loop_does_not_borrow_active_pool(self):
        async with http_clients.http_client_lifespan():
            shared, owned = http_clients.acquire_http_client("lmstudio", timeout=60)
            self.assertFalse(owned)

            async def other_loop():
                client, local_owned = http_clients.acquire_http_client("lmstudio", timeout=60)
                try:
                    return local_owned, client is shared
                finally:
                    await client.aclose()

            self.assertEqual(await asyncio.to_thread(lambda: asyncio.run(other_loop())), (True, False))
        self.assertTrue(shared.is_closed)

    async def test_disconnect_completes_lazy_stream_cleanup(self):
        class LazyStream(httpx.AsyncByteStream):
            def __init__(self, backend):
                self.backend = backend
                self.close_completed = False

            async def __aiter__(self):
                if self.backend == "openai":
                    event = {"type": "response.created", "response": {"id": "offline", "model": "test"}}
                    yield ("data: " + json.dumps(event) + "\n\n").encode()
                else:
                    yield b"data: first\n\n"
                await anyio.sleep_forever()

            async def aclose(self):
                await anyio.sleep(0)
                self.close_completed = True

        for backend in ("lmstudio", "openai"):
            stream = LazyStream(backend)
            clients, factory = self.client_factory(lambda request: httpx.Response(200, stream=stream))
            with factory, patch.object(openai_provider.config, "OPENAI_API_KEY", "offline-test"):
                async with http_clients.http_client_lifespan():
                    if backend == "openai":
                        response = await openai_provider._stream({"model": "test"}, {})
                    else:
                        response = await stream_openai_compatible_response("http://test/chat/completions", {})
                    with anyio.CancelScope() as disconnected:
                        await response.body_iterator.__anext__()
                        disconnected.cancel()
                        await response.body_iterator.__anext__()
                    self.assertTrue(stream.close_completed)
                    self.assertFalse(clients[0].is_closed)
                    stream.close_completed = False
                    if backend == "openai":
                        response = await openai_provider._stream({"model": "test"}, {})
                    else:
                        response = await stream_openai_compatible_response("http://test/chat/completions", {})

                    async def send(message):
                        if message["type"] == "http.response.body":
                            raise OSError("disconnected")

                    with self.assertRaises(ClientDisconnect):
                        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, AsyncMock(), send)
                    self.assertTrue(stream.close_completed)
                    self.assertFalse(clients[0].is_closed)
            self.assertTrue(clients[0].is_closed)

    async def test_proxy_pool_failure_is_optional_and_shutdown_cleans_up(self):
        for failing in (False, True):
            store = SimpleNamespace(open_pool=AsyncMock(), close_pool=AsyncMock())
            if failing:
                store.open_pool.side_effect = RuntimeError("private connection information")
                store.close_pool.side_effect = RuntimeError("private connection information")
            with tempfile.TemporaryDirectory() as temporary, \
                    patch.object(app_module, "_RUNTIME_DIR", Path(temporary)), \
                    patch.object(app_module, "_PROXY_PID_FILE", Path(temporary) / "proxy.pid"), \
                    patch.object(app_module, "_MEMORY_ENABLED", True), \
                    patch.object(app_module, "_MEMORY_PERSIST_ENABLED", True), \
                    patch.object(app_module, "_memory_store", store), \
                    patch("sys.stderr") as stderr:
                async with app_module.lifespan(app_module.app):
                    self.assertTrue(app_module._PROXY_PID_FILE.exists())
                    async with http_clients.http_client("lmstudio", timeout=60) as client:
                        self.assertFalse(client.is_closed)
                self.assertTrue(client.is_closed)
                self.assertFalse(app_module._PROXY_PID_FILE.exists())
                store.open_pool.assert_awaited_once()
                store.close_pool.assert_awaited_once()
                self.assertNotIn("private connection information", str(stderr.write.call_args_list))

    async def test_proxy_shutdown_cancellation_finishes_memory_close(self):
        completed = []

        async def close_pool():
            await anyio.sleep(0)
            completed.append(True)

        store = SimpleNamespace(open_pool=AsyncMock(), close_pool=close_pool)
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(app_module, "_RUNTIME_DIR", Path(temporary)), \
                patch.object(app_module, "_PROXY_PID_FILE", Path(temporary) / "proxy.pid"), \
                patch.object(app_module, "_MEMORY_ENABLED", True), \
                patch.object(app_module, "_MEMORY_PERSIST_ENABLED", True), \
                patch.object(app_module, "_memory_store", store):
            with anyio.CancelScope() as cancelled:
                async with app_module.lifespan(app_module.app):
                    cancelled.cancel()
                    await anyio.sleep(0)
            self.assertEqual(completed, [True])
            self.assertFalse(app_module._PROXY_PID_FILE.exists())


class ApplicationLifecycleTests(unittest.TestCase):
    def test_model_routes_reuse_client_with_real_asgi_lifespan(self):
        clients = []
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"models": []})

        def create(**kwargs):
            client = _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(client)
            return client

        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(app_module, "_RUNTIME_DIR", Path(temporary)), \
                patch.object(app_module, "_PROXY_PID_FILE", Path(temporary) / "proxy.pid"), \
                patch.object(app_module, "_MEMORY_ENABLED", False), \
                patch.object(http_clients.httpx, "AsyncClient", side_effect=create):
            with TestClient(app_module.app) as client:
                self.assertEqual(client.get("/api/v1/models").status_code, 200)
                self.assertEqual(client.get("/v1/local-models").status_code, 200)
                self.assertEqual(len(clients), 1)
                self.assertFalse(clients[0].is_closed)
            self.assertEqual(len(requests), 2)
            self.assertTrue(clients[0].is_closed)
            self.assertFalse(app_module._PROXY_PID_FILE.exists())


if __name__ == "__main__":
    unittest.main()

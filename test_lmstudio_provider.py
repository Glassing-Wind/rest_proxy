import importlib
import os
import unittest
from unittest import mock


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.reason_phrase = text or "error"
        self.content = b"" if payload is None else b"x"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            req = httpx.Request("GET", "http://localhost")
            raise httpx.HTTPStatusError(
                "error",
                request=req,
                response=httpx.Response(self.status_code, request=req, text=self.text),
            )


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def request(self, method, path, json=None):
        self.calls.append((method, path, json))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self):
        return None


class LMStudioProviderTests(unittest.IsolatedAsyncioTestCase):
    def _load_module(self):
        mod = importlib.import_module("local_embeddings.lmstudio")
        return importlib.reload(mod)

    async def test_auto_load_then_embed_uses_native_and_openai_endpoints(self):
        with mock.patch.dict(
            os.environ,
            {
                "LMSTUDIO_BASE_URL": "http://127.0.0.1:1234",
                "LMSTUDIO_EMBED_MODEL": "embed-model",
                "LMSTUDIO_CONTEXT_LENGTH": "2048",
                "LMSTUDIO_EVAL_BATCH_SIZE": "512",
                "LMSTUDIO_MAX_BATCH_SIZE": "2",
                "LMSTUDIO_AUTO_LOAD": "true",
            },
            clear=False,
        ):
            mod = self._load_module()
            provider = mod.LMStudioEmbeddingProvider()
            fake = _FakeClient(
                [
                    _FakeResponse(
                        payload=[
                            {
                                "id": "embed-model",
                                "type": "embedding",
                                "status": "available",
                                "loaded": False,
                            }
                        ]
                    ),
                    _FakeResponse(
                        payload=[
                            {
                                "id": "embed-model",
                                "type": "embedding",
                                "status": "available",
                                "loaded": False,
                            }
                        ]
                    ),
                    _FakeResponse(payload={"id": "embed-model", "type": "embedding", "status": "loaded"}),
                    _FakeResponse(
                        payload={
                            "data": [
                                {"index": 0, "embedding": [0.1, 0.2]},
                                {"index": 1, "embedding": [0.3, 0.4]},
                            ]
                        }
                    ),
                ]
            )
            provider._client = fake
            vectors = await provider.embed_texts(["alpha", "beta"])
            self.assertEqual(vectors, [[0.1, 0.2], [0.3, 0.4]])
            self.assertEqual(fake.calls[0][1], "/api/v1/models")
            self.assertEqual(fake.calls[1][1], "/api/v1/models")
            self.assertEqual(fake.calls[2][1], "/api/v1/models/load")
            self.assertEqual(fake.calls[3][1], "/v1/embeddings")
            self.assertEqual(fake.calls[3][2]["input"], ["alpha", "beta"])

    async def test_health_check_reports_server_unavailable(self):
        with mock.patch.dict(os.environ, {"LMSTUDIO_BASE_URL": "http://127.0.0.1:1234"}, clear=False):
            mod = self._load_module()
            provider = mod.LMStudioEmbeddingProvider()
            import httpx

            provider._client = _FakeClient([httpx.ConnectError("boom"), httpx.ConnectError("boom")])
            health = await provider.health_check()
            self.assertFalse(health.ok)
            self.assertIn("LM Studio server is unavailable", health.detail)

    async def test_auto_load_reuses_already_loaded_model(self):
        with mock.patch.dict(
            os.environ,
            {
                "LMSTUDIO_EMBED_MODEL": "embed-model",
                "LMSTUDIO_AUTO_LOAD": "true",
            },
            clear=False,
        ):
            mod = self._load_module()
            provider = mod.LMStudioEmbeddingProvider()
            fake = _FakeClient(
                [
                    _FakeResponse(
                        payload=[
                            {
                                "id": "embed-model",
                                "type": "embedding",
                                "status": "loaded",
                                "loaded": True,
                            }
                        ]
                    ),
                    _FakeResponse(payload={"data": [{"index": 0, "embedding": [0.1, 0.2]}]}),
                ]
            )
            provider._client = fake
            vectors = await provider.embed_texts(["alpha"], batch_size=1)
            self.assertEqual(vectors, [[0.1, 0.2]])
            self.assertEqual(fake.calls[0][1], "/api/v1/models")
            self.assertEqual(fake.calls[1][1], "/v1/embeddings")
            self.assertEqual(len(fake.calls), 2)

    async def test_non_embedding_model_raises_type_error(self):
        with mock.patch.dict(
            os.environ,
            {
                "LMSTUDIO_EMBED_MODEL": "chat-model",
                "LMSTUDIO_AUTO_LOAD": "true",
            },
            clear=False,
        ):
            mod = self._load_module()
            provider = mod.LMStudioEmbeddingProvider()
            provider._client = _FakeClient(
                [
                    _FakeResponse(
                        payload=[
                            {
                                "id": "chat-model",
                                "type": "llm",
                                "status": "loaded",
                                "loaded": True,
                            }
                        ]
                    )
                ]
            )
            with self.assertRaises(mod.ModelTypeError):
                await provider.load_model()

    async def test_load_model_reuses_already_loaded_instance(self):
        with mock.patch.dict(
            os.environ,
            {
                "LMSTUDIO_EMBED_MODEL": "embed-model",
            },
            clear=False,
        ):
            mod = self._load_module()
            provider = mod.LMStudioEmbeddingProvider()
            fake = _FakeClient(
                [
                    _FakeResponse(
                        payload=[
                            {
                                "id": "embed-model",
                                "type": "embedding",
                                "status": "loaded",
                                "loaded": True,
                            }
                        ]
                    )
                ]
            )
            provider._client = fake
            info = await provider.load_model()
            self.assertEqual(info.id, "embed-model")
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(fake.calls[0][1], "/api/v1/models")

    async def test_load_model_retries_without_eval_batch_size_when_unsupported(self):
        with mock.patch.dict(
            os.environ,
            {
                "LMSTUDIO_EMBED_MODEL": "embed-model",
                "LMSTUDIO_EVAL_BATCH_SIZE": "512",
            },
            clear=False,
        ):
            mod = self._load_module()
            provider = mod.LMStudioEmbeddingProvider()
            bad = _FakeResponse(
                status_code=400,
                text='{"error":{"type":"invalid_request","message":"The following configuration values are not supported for embedding models: eval_batch_size"}}',
            )
            good = _FakeResponse(payload={"id": "embed-model", "type": "embedding", "status": "loaded"})
            fake = _FakeClient(
                [
                    _FakeResponse(
                        payload=[
                            {
                                "id": "embed-model",
                                "type": "embedding",
                                "status": "available",
                                "loaded": False,
                            }
                        ]
                    ),
                    bad,
                    good,
                ]
            )
            provider._client = fake
            info = await provider.load_model()
            self.assertEqual(info.id, "embed-model")
            self.assertEqual(fake.calls[0][1], "/api/v1/models")
            self.assertIn("eval_batch_size", fake.calls[1][2])
            self.assertNotIn("eval_batch_size", fake.calls[2][2])

    async def test_token_guard_rejects_overlong_text(self):
        with mock.patch.dict(
            os.environ,
            {
                "LMSTUDIO_EMBED_MODEL": "embed-model",
                "LMSTUDIO_AUTO_LOAD": "false",
                "LMSTUDIO_CONTEXT_LENGTH": "4",
            },
            clear=False,
        ):
            mod = self._load_module()
            provider = mod.LMStudioEmbeddingProvider()
            provider._client = _FakeClient(
                [_FakeResponse(payload=[{"id": "embed-model", "type": "embedding", "status": "loaded"}])]
            )
            with self.assertRaises(ValueError):
                await provider.embed_texts(["x" * 40], batch_size=1)


if __name__ == "__main__":
    unittest.main()

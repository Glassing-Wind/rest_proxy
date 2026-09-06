import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "embedding_service.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("embedding_service_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class EmbeddingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_embeddings_are_deterministic_and_dimensioned(self):
        with mock.patch.dict(
            os.environ,
            {
                "LM_PROXY_FAKE_EMBEDDINGS": "1",
                "LM_PROXY_MEMORY_EMBEDDING_DIM": "8",
            },
            clear=False,
        ):
            mod = _load_module()
            svc = mod.get_embedding_service()
            first = await svc.embed_batch_async(["alpha", "beta"])
            second = await svc.embed_batch_async(["alpha", "beta"])
            self.assertEqual(first, second)
            self.assertEqual(len(first), 2)
            self.assertEqual(len(first[0]), 8)
            self.assertEqual(svc._device, "fake(8)")
            self.assertNotEqual(first[0], first[1])


if __name__ == "__main__":
    unittest.main()

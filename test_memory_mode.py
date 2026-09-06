import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "proxy" / "config.py"


def _load_config(env_updates: dict[str, str]):
    spec = importlib.util.spec_from_file_location("proxy_config_under_test", CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    base_env = {
        "LM_PROXY_MEMORY_ENABLED": "1",
        "LM_PROXY_MEMORY_ENABLE_PERSISTENCE": "1",
        "LM_PROXY_MEMORY_ENABLE_REDIS": "1",
        "LM_PROXY_MEMORY_ENABLE_INJECT": "1",
        "LM_PROXY_MEMORY_ENABLE_EMBEDDINGS": "1",
    }
    base_env.update(env_updates)
    dotenv_mod = type(sys)("dotenv")
    dotenv_mod.load_dotenv = lambda *args, **kwargs: None
    dotenv_mod.dotenv_values = lambda *args, **kwargs: {}
    with mock.patch.dict(os.environ, base_env, clear=False), mock.patch.dict(
        sys.modules, {"dotenv": dotenv_mod}
    ):
        spec.loader.exec_module(module)
    return module


class MemoryModeTests(unittest.TestCase):
    def test_stateless_default_disables_proxy_memory_behavior(self):
        cfg = _load_config({"LM_PROXY_MEMORY_MODE": "stateless"})

        self.assertEqual(cfg._MEMORY_MODE, "off")
        self.assertFalse(cfg._MEMORY_MODE_ENABLED)
        self.assertFalse(cfg._MEMORY_PERSIST_ENABLED)
        self.assertFalse(cfg._MEMORY_REDIS_ENABLED)
        self.assertFalse(cfg._MEMORY_INJECT_ENABLED)
        self.assertFalse(cfg._MEMORY_EMBEDDINGS_ENABLED)

    def test_assist_mode_enables_bounded_memory_without_embeddings(self):
        cfg = _load_config({"LM_PROXY_MEMORY_MODE": "assist"})

        self.assertEqual(cfg._MEMORY_MODE, "assist")
        self.assertTrue(cfg._MEMORY_MODE_ENABLED)
        self.assertTrue(cfg._MEMORY_PERSIST_ENABLED)
        self.assertTrue(cfg._MEMORY_REDIS_ENABLED)
        self.assertTrue(cfg._MEMORY_INJECT_ENABLED)
        self.assertFalse(cfg._MEMORY_EMBEDDINGS_ENABLED)

    def test_full_mode_preserves_broad_memory_behavior(self):
        cfg = _load_config({"LM_PROXY_MEMORY_MODE": "full"})

        self.assertEqual(cfg._MEMORY_MODE, "full")
        self.assertTrue(cfg._MEMORY_MODE_ENABLED)
        self.assertTrue(cfg._MEMORY_PERSIST_ENABLED)
        self.assertTrue(cfg._MEMORY_REDIS_ENABLED)
        self.assertTrue(cfg._MEMORY_INJECT_ENABLED)
        self.assertTrue(cfg._MEMORY_EMBEDDINGS_ENABLED)

    def test_hybrid_mode_remains_a_compatibility_alias_for_full(self):
        cfg = _load_config({"LM_PROXY_MEMORY_MODE": "hybrid"})

        self.assertEqual(cfg._MEMORY_MODE, "full")
        self.assertTrue(cfg._MEMORY_MODE_ENABLED)
        self.assertTrue(cfg._MEMORY_PERSIST_ENABLED)
        self.assertTrue(cfg._MEMORY_REDIS_ENABLED)
        self.assertTrue(cfg._MEMORY_INJECT_ENABLED)
        self.assertTrue(cfg._MEMORY_EMBEDDINGS_ENABLED)

    def test_memory_disabled_overrides_mode(self):
        cfg = _load_config(
            {
                "LM_PROXY_MEMORY_ENABLED": "0",
                "LM_PROXY_MEMORY_MODE": "full",
            }
        )

        self.assertEqual(cfg._MEMORY_MODE, "full")
        self.assertFalse(cfg._MEMORY_MODE_ENABLED)
        self.assertFalse(cfg._MEMORY_PERSIST_ENABLED)
        self.assertFalse(cfg._MEMORY_REDIS_ENABLED)
        self.assertFalse(cfg._MEMORY_INJECT_ENABLED)
        self.assertFalse(cfg._MEMORY_EMBEDDINGS_ENABLED)


if __name__ == "__main__":
    unittest.main()

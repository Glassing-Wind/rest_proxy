import importlib.util
import inspect
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
CHUNKING_MODULE_PATH = REPO_ROOT / "tools" / "brain" / "docs" / "chunking.py"
CONFIG_MODULE_PATH = REPO_ROOT / "tools" / "brain" / "docs" / "config.py"


def load_config_module():
    spec = importlib.util.spec_from_file_location("tools.brain.docs.config", CONFIG_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_chunking_module(fake_ts_pack=None):
    spec = importlib.util.spec_from_file_location("docs_chunking_under_test", CHUNKING_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    docs_pkg = types.ModuleType("tools.brain.docs")
    docs_pkg.__path__ = []
    config_mod = load_config_module()

    patch_modules = {
        "tools": tools_pkg,
        "tools.brain": brain_pkg,
        "tools.brain.docs": docs_pkg,
        "tools.brain.docs.config": config_mod,
    }
    if fake_ts_pack is not None:
        patch_modules["tree_sitter_language_pack"] = fake_ts_pack

    with mock.patch.dict(sys.modules, patch_modules):
        spec.loader.exec_module(module)
    return module


class FakeTsPack:
    def __init__(self):
        self.configs = []

    def ProcessConfig(self, language, **kwargs):
        cfg = {"language": language, **kwargs}
        self.configs.append(cfg)
        return cfg

    def process(self, content, config):
        return {
            "chunks": [
                {
                    "content": "# Heading\nSome content",
                    "metadata": {"context_path": ["Heading"]},
                }
            ]
        }


class DocsChunkingTests(unittest.TestCase):
    def test_ts_pack_chunking_uses_overlap_bytes(self):
        fake_ts_pack = FakeTsPack()
        module = load_chunking_module()
        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_ts_pack}):
            chunks = module.chunk_content(
                "# Heading\nSome content\nMore content",
                "https://example.com/docs/page",
                "Example Page",
                fmt="markdown",
            )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(fake_ts_pack.configs[0]["chunk_max_size"], module.CHUNK_MAX_BYTES)
        process_config_sig = inspect.signature(fake_ts_pack.ProcessConfig)
        if "chunk_overlap" in process_config_sig.parameters:
            self.assertEqual(fake_ts_pack.configs[0]["chunk_overlap"], module.CHUNK_OVERLAP_BYTES)


if __name__ == "__main__":
    unittest.main()

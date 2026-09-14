import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.tmpdir.name) / "config"
        self.old_env = {
            "LM_PROXY_CONFIG_DIR": os.environ.get("LM_PROXY_CONFIG_DIR"),
            "LM_PROXY_WATCHER_ENABLED": os.environ.get("LM_PROXY_WATCHER_ENABLED"),
            "LM_PROXY_AUTO_WATCH_SESSION_WORKSPACE": os.environ.get(
                "LM_PROXY_AUTO_WATCH_SESSION_WORKSPACE"
            ),
            "LM_PROXY_WATCH_SESSION_TTL_SECONDS": os.environ.get(
                "LM_PROXY_WATCH_SESSION_TTL_SECONDS"
            ),
            "MCP_ID": os.environ.get("MCP_ID"),
            "VSCODE_PID": os.environ.get("VSCODE_PID"),
            "VSCODE_IPC_HOOK": os.environ.get("VSCODE_IPC_HOOK"),
        }
        os.environ["LM_PROXY_CONFIG_DIR"] = str(self.config_dir)
        os.environ["LM_PROXY_WATCHER_ENABLED"] = "1"
        os.environ["LM_PROXY_AUTO_WATCH_SESSION_WORKSPACE"] = "1"
        os.environ["LM_PROXY_WATCH_SESSION_TTL_SECONDS"] = "120"
        os.environ["MCP_ID"] = "codex"
        os.environ["VSCODE_PID"] = "12345"
        os.environ["VSCODE_IPC_HOOK"] = "/tmp/codex-ipc.sock"

        import graphrag_core.indexing.watcher as watcher_module

        self.watcher = importlib.reload(watcher_module)
        self.project = Path(self.tmpdir.name) / "demo"
        self.project.mkdir()

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmpdir.cleanup()

    def test_auto_watch_is_disabled_by_default(self):
        self.assertFalse(self.watcher.maybe_auto_watch(str(self.project), reason="test"))
        self.assertFalse(self.watcher.is_watched(str(self.project)))

        active_path = self.config_dir / "active_sessions.json"
        self.assertFalse(active_path.exists())

    def test_pinned_watch_persists_without_session(self):
        self.assertTrue(self.watcher.add_watch(str(self.project)))
        self.assertTrue(self.watcher.is_watched(str(self.project)))
        self.watcher._sync_runtime_watch_roots()

        self.assertTrue(self.watcher.is_watched(str(self.project)))
        pinned_path = self.config_dir / "pinned_watches.json"
        self.assertEqual(json.loads(pinned_path.read_text(encoding="utf-8")), [str(self.project)])


if __name__ == "__main__":
    unittest.main()

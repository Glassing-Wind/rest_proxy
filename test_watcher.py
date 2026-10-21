"""Offline legacy watcher startup and file-change dispatch tests."""

import asyncio
import contextlib
import importlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


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

    def _restore_watch(self):
        self.config_dir.mkdir(exist_ok=True)
        (self.config_dir / "pinned_watches.json").write_text(
            json.dumps([str(self.project)]), encoding="utf-8"
        )
        asyncio.run(self.watcher.load_watched_config())

    def _poll_once(self, index_fn=None):
        if index_fn is None:
            index_fn = AsyncMock()

        async def run():
            with patch.object(self.watcher.asyncio, "sleep", side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await self.watcher._poll_watcher(index_fn)

        asyncio.run(run())
        return index_fn

    def test_restored_watch_baselines_without_dispatch_then_detects_edit(self):
        source = self.project / "example.py"
        source.write_text("before\n", encoding="utf-8")
        self._restore_watch()
        self.assertIsNone(self.watcher.WATCHED_PATHS[str(self.project)])

        index_fn = self._poll_once()
        index_fn.assert_not_awaited()
        baseline_mtime = self.watcher.WATCHED_PATHS[str(self.project)][str(source)]
        source.write_text("after\n", encoding="utf-8")
        os.utime(source, (baseline_mtime + 2, baseline_mtime + 2))

        self._poll_once(index_fn)
        index_fn.assert_awaited_once_with(str(self.project))
        self._poll_once(index_fn)
        self.assertEqual(index_fn.await_count, 1)

    def test_new_manual_watch_still_dispatches_initial_files(self):
        (self.project / "example.py").write_text("source\n", encoding="utf-8")
        self.assertTrue(self.watcher.add_watch(str(self.project)))
        self.assertEqual(self.watcher.WATCHED_PATHS[str(self.project)], {})
        self._poll_once().assert_awaited_once_with(str(self.project))

    def test_restored_empty_watch_detects_later_addition_and_deletion(self):
        self._restore_watch()
        index_fn = self._poll_once()
        index_fn.assert_not_awaited()
        self.assertEqual(self.watcher.WATCHED_PATHS[str(self.project)], {})

        source = self.project / "example.py"
        source.write_text("source\n", encoding="utf-8")
        self._poll_once(index_fn)
        index_fn.assert_awaited_once_with(str(self.project))

        source.unlink()
        self._poll_once(index_fn)
        self.assertEqual(index_fn.await_count, 2)
        self.assertEqual(self.watcher.WATCHED_PATHS[str(self.project)], {})

    def test_reloading_config_preserves_live_baseline_and_pending_edit(self):
        source = self.project / "example.py"
        source.write_text("before\n", encoding="utf-8")
        self._restore_watch()
        index_fn = self._poll_once()
        baseline = dict(self.watcher.WATCHED_PATHS[str(self.project)])
        source.write_text("after\n", encoding="utf-8")
        os.utime(source, (baseline[str(source)] + 2, baseline[str(source)] + 2))

        asyncio.run(self.watcher.load_watched_config())
        self.assertEqual(self.watcher.WATCHED_PATHS[str(self.project)], baseline)
        self._poll_once(index_fn)
        index_fn.assert_awaited_once_with(str(self.project))

    def test_reloading_config_does_not_prime_new_manual_watch(self):
        (self.project / "example.py").write_text("source\n", encoding="utf-8")
        self.watcher.add_watch(str(self.project))
        asyncio.run(self.watcher.load_watched_config())
        self.assertEqual(self.watcher.WATCHED_PATHS[str(self.project)], {})
        self._poll_once().assert_awaited_once_with(str(self.project))

    def test_failed_restored_scan_remains_unprimed_and_retries(self):
        (self.project / "example.py").write_text("source\n", encoding="utf-8")
        self._restore_watch()
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            with patch.object(self.watcher, "build_manifest", side_effect=OSError("scan failed")):
                index_fn = self._poll_once()
        self.assertIn("scan failed", errors.getvalue())
        index_fn.assert_not_awaited()
        self.assertIsNone(self.watcher.WATCHED_PATHS[str(self.project)])

        self._poll_once(index_fn)
        index_fn.assert_not_awaited()
        self.assertTrue(self.watcher.WATCHED_PATHS[str(self.project)])

    def test_failed_file_stat_keeps_restored_and_live_baselines(self):
        (self.project / "example.py").write_text("source\n", encoding="utf-8")
        self._restore_watch()
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            with patch.object(self.watcher.os.path, "getmtime", side_effect=PermissionError("stat denied")):
                index_fn = self._poll_once()
        self.assertIn("stat denied", errors.getvalue())
        self.assertIsNone(self.watcher.WATCHED_PATHS[str(self.project)])
        self._poll_once(index_fn)
        baseline = dict(self.watcher.WATCHED_PATHS[str(self.project)])

        with contextlib.redirect_stderr(errors):
            with patch.object(self.watcher.os.path, "getmtime", side_effect=PermissionError("stat denied")):
                self._poll_once(index_fn)
        self.assertEqual(self.watcher.WATCHED_PATHS[str(self.project)], baseline)
        index_fn.assert_not_awaited()

    def test_missing_restored_root_retries_without_blocking_manual_watch(self):
        self._restore_watch()
        self.project.rmdir()
        other = Path(self.tmpdir.name) / "other"
        other.mkdir()
        (other / "example.py").write_text("source\n", encoding="utf-8")
        self.watcher.add_watch(str(other))

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            index_fn = self._poll_once()
        self.assertIn("Failed to scan", errors.getvalue())
        self.assertIsNone(self.watcher.WATCHED_PATHS[str(self.project)])
        index_fn.assert_awaited_once_with(str(other))

        self.project.mkdir()
        self._poll_once(index_fn)
        self.assertEqual(self.watcher.WATCHED_PATHS[str(self.project)], {})
        self.assertEqual(index_fn.await_count, 1)

    def _assert_partial_scan_is_rejected(self, failed_scan):
        """A partial scan must not publish deletions or starve a healthy root."""
        self._restore_watch()
        other = Path(self.tmpdir.name) / "other"
        other.mkdir()
        healthy_source = other / "example.py"
        healthy_source.write_text("source\n", encoding="utf-8")
        self.watcher.add_watch(str(other))

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), failed_scan():
            # Default callers still receive the historical best-effort result.
            partial = self.watcher.build_manifest(str(self.project))
            self.assertFalse(any(entry["rel_path"] == "nested/example.py" for entry in partial))
            index_fn = self._poll_once()
        self.assertIn("Failed to scan", errors.getvalue())
        self.assertIsNone(self.watcher.WATCHED_PATHS[str(self.project)])
        index_fn.assert_awaited_once_with(str(other))

        # A subsequent successful scan establishes the complete baseline.
        self._poll_once(index_fn)
        baseline = dict(self.watcher.WATCHED_PATHS[str(self.project)])
        self.assertIn(str(self.project / "nested/example.py"), baseline)
        self.assertEqual(index_fn.await_count, 1)

        # The same failure during an established watch cannot replace its
        # baseline, and another root's real edit must still dispatch.
        healthy_mtime = self.watcher.WATCHED_PATHS[str(other)][str(healthy_source)]
        healthy_source.write_text("changed\n", encoding="utf-8")
        os.utime(healthy_source, (healthy_mtime + 2, healthy_mtime + 2))
        with contextlib.redirect_stderr(errors), failed_scan():
            self._poll_once(index_fn)
        self.assertEqual(self.watcher.WATCHED_PATHS[str(self.project)], baseline)
        self.assertEqual(index_fn.await_count, 2)
        self.assertEqual([call.args[0] for call in index_fn.await_args_list], [str(other), str(other)])

        self._poll_once(index_fn)
        self.assertEqual(self.watcher.WATCHED_PATHS[str(self.project)], baseline)
        self.assertEqual(index_fn.await_count, 2)

    def test_nested_traversal_error_preserves_baselines_and_other_root(self):
        nested = self.project / "nested"
        nested.mkdir()
        (nested / "example.py").write_text("source\n", encoding="utf-8")
        original_scandir = os.scandir

        def scan(path):
            if Path(path) == nested:
                raise PermissionError("nested traversal denied")
            return original_scandir(path)

        self._assert_partial_scan_is_rejected(lambda: patch.object(os, "scandir", side_effect=scan))

    def test_manifest_file_stat_error_preserves_baselines_and_other_root(self):
        nested = self.project / "nested"
        nested.mkdir()
        source = nested / "example.py"
        source.write_text("source\n", encoding="utf-8")
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path == source:
                raise PermissionError("manifest file stat denied")
            return original_stat(path, *args, **kwargs)

        self._assert_partial_scan_is_rejected(lambda: patch.object(Path, "stat", stat))


if __name__ == "__main__":
    unittest.main()

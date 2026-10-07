"""Offline watcher scope and startup routing; no native stores or inference providers."""
import asyncio
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from memory.embedded_watch import changed_paths


class WatchTests(unittest.TestCase):
    def test_content_not_mtime_scope_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / 'a.py'
            path.write_text('original')
            files = [{'path': 'a.py', 'sha256': hashlib.sha256(b'original').hexdigest()}]
            self.assertEqual(changed_paths(str(root), files), (False, ['a.py']))
            path.write_text('changed')
            self.assertEqual(changed_paths(str(root), files), (True, ['a.py']))
            path.unlink()
            self.assertEqual(changed_paths(str(root), files), (True, []))
            path.symlink_to(root / 'other')
            with self.assertRaises(ValueError):
                changed_paths(str(root), files)

    def test_embedded_startup_routes_only_explicit_owner_flag(self):
        from graphrag_core.indexing import watcher
        runtime = mock.AsyncMock()
        async def check():
            with mock.patch('memory.storage_config.embedded_graph_selected', return_value=True), \
                    mock.patch('memory.embedded_runtime.get_embedded_runtime', return_value=runtime), \
                    mock.patch.dict('os.environ', {'LM_PROXY_EMBEDDED_WATCH_ENABLED': '0'}), \
                    mock.patch.object(watcher, '_WATCHER_TASK', None):
                await watcher.load_watched_config()
                self.assertIsNone(await watcher.start_watcher(mock.Mock(side_effect=AssertionError('legacy index'))))
                runtime.start_watch.assert_not_called()
            with mock.patch('memory.storage_config.embedded_graph_selected', return_value=True), \
                    mock.patch('memory.embedded_runtime.get_embedded_runtime', return_value=runtime), \
                    mock.patch.dict('os.environ', {'LM_PROXY_EMBEDDED_WATCH_ENABLED': '1',
                                                 'LM_PROXY_EMBEDDED_WATCH_INTERVAL': '10'}), \
                    mock.patch.object(watcher, '_WATCHER_TASK', None):
                await watcher.start_watcher(mock.Mock(side_effect=AssertionError('legacy index')))
                runtime.start_watch.assert_awaited_once_with(10.0)
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()

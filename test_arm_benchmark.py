"""Offline explicit-corpus boundaries; no native engine, service or model required."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.benchmark_embedded_arm import capture_sources


class FrozenCorpus(unittest.TestCase):
    def test_explicit_manifest_snapshot_does_not_follow_later_edits_or_discover_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'a.py'
            source.write_text('def before():\n    return 1\n')
            (root / 'unlisted.py').write_text('private_unlisted_value = 1\n')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps(['a.py']))
            captured = capture_sources(root, manifest)
            source.write_text('def after():\n    return 2\n')
            self.assertEqual(captured, {'a.py': 'def before():\n    return 1\n'})

    def test_manifest_refuses_traversal_symlinks_binary_and_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'a.py'
            source.write_text('pass\n')
            (root / 'link.py').symlink_to(source)
            manifest = root / 'manifest.json'
            for paths in (['../outside.py'], ['/outside.py'], ['a.py', 'a.py'], ['link.py'],
                          ['secret.env'], [], {'a.py': True}):
                with self.subTest(paths=paths), self.assertRaises(ValueError):
                    manifest.write_text(json.dumps(paths))
                    capture_sources(root, manifest)
            manifest.write_text(json.dumps(['a.py']))
            for data in (b'\0', b'x' * 65537):
                with self.subTest(size=len(data)), self.assertRaises(ValueError):
                    source.write_bytes(data)
                    capture_sources(root, manifest)


if __name__ == '__main__':
    unittest.main()

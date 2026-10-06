"""Offline grammar identity/notice mismatch checks; no native libraries loaded."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from scripts.reconcile_grammar_assets import reconcile
from scripts.reconcile_python_notice import git_blob


class GrammarAssets(unittest.TestCase):
    def fixture(self, root):
        cache = root / 'cache'
        (cache / 'libs').mkdir(parents=True)
        binary = b'fixture binary, never loaded'
        (cache / 'libs/libtree_sitter_python.dylib').write_bytes(binary)
        bundle = root / 'bundle.tar'
        with tarfile.open(bundle, 'w') as archive:
            info = tarfile.TarInfo('./libtree_sitter_python.dylib')
            info.size = len(binary)
            archive.addfile(info, io.BytesIO(binary))
        (cache / 'manifest.json').write_text(json.dumps({'version': 'fixture', 'platforms': {
            'macos-arm64': {'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest()}}}))
        definitions = root / 'definitions.json'
        entry = {'repo': 'https://github.com/example/python', 'rev': 'a' * 40}
        definitions.write_text(json.dumps({'python': entry}))
        notice = root / 'LICENSE'
        notice.write_bytes(b'Fixture notice')
        notices = root / 'notices.json'
        notices.write_text(json.dumps({'release_commit': 'b' * 40,
            'definitions_sha256': hashlib.sha256(definitions.read_bytes()).hexdigest(),
            'grammars': [{'language': 'python', 'repository': entry['repo'], 'declared_revision': entry['rev'],
                         'files': [{'local_file': 'LICENSE', 'sha256': hashlib.sha256(notice.read_bytes()).hexdigest(),
                                    'git_blob_sha1': git_blob(notice.read_bytes())}]}]}))
        return cache, bundle, 'macos-arm64', definitions, notices

    def test_bundle_cache_and_notice_match(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            result = reconcile(*args)
            self.assertEqual(len(result['grammars']), 1)
            self.assertEqual(result['bundle_file_count'], 1)

    def test_changed_bundle_or_cache_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            (args[0] / 'libs/libtree_sitter_python.dylib').write_bytes(b'Changed binary')
            with self.assertRaises(ValueError):
                reconcile(*args)
            args[1].write_bytes(b'Changed bundle')
            with self.assertRaises(ValueError):
                reconcile(*args)

    def test_changed_notice_or_definitions_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            (Path(directory) / 'LICENSE').write_bytes(b'Changed notice')
            with self.assertRaises(ValueError):
                reconcile(*args)
            args[3].write_text('{}')
            with self.assertRaises(ValueError):
                reconcile(*args)


if __name__ == '__main__':
    unittest.main()

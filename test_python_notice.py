"""Exact artifact/source/notice binding regressions; no network access."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.reconcile_python_notice import git_blob, reconcile


class NoticeBinding(unittest.TestCase):
    def fixture(self, root):
        wheel, notice = root / 'fixture.whl', root / 'LICENSE'
        content = b'value = 1\n'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('package/module.py', content)
        notice.write_bytes(b'Fixture license\n')
        tree = {'truncated': False, 'tree': [
            {'path': 'python/package/module.py', 'type': 'blob', 'sha': git_blob(content)},
            {'path': 'LICENSE', 'type': 'blob', 'sha': git_blob(notice.read_bytes())}]}
        return wheel, hashlib.sha256(wheel.read_bytes()).hexdigest(), tree, notice

    def test_exact_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel, sha, tree, notice = self.fixture(Path(directory))
            result = reconcile(wheel, sha, tree, 'python', 'package', notice)
            self.assertEqual(result['package_files_bound_to_source_tree'], 1)

    def test_wrong_artifact_or_source_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel, sha, tree, notice = self.fixture(Path(directory))
            with self.assertRaises(ValueError):
                reconcile(wheel, '0' * 64, tree, 'python', 'package', notice)
            tree['tree'][0]['sha'] = '0' * 40
            with self.assertRaises(ValueError):
                reconcile(wheel, sha, tree, 'python', 'package', notice)

    def test_wrong_notice_or_truncated_tree_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel, sha, tree, notice = self.fixture(Path(directory))
            notice.write_text('Changed notice\n')
            with self.assertRaises(ValueError):
                reconcile(wheel, sha, tree, 'python', 'package', notice)
            tree['truncated'] = True
            with self.assertRaises(ValueError):
                reconcile(wheel, sha, tree, 'python', 'package', notice)


if __name__ == '__main__':
    unittest.main()

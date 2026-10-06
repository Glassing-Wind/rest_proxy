"""Offline inventory contracts; no services, downloads or legal inference."""
from pathlib import Path
import tempfile
import unittest

from scripts.build_release_inventory import generate


class Distribution:
    version = '1.0'

    def __init__(self, root, license_expression=None):
        self.root = root
        self.metadata = {'Name': 'fixture_pkg'}
        if license_expression:
            self.metadata['License-Expression'] = license_expression
        self.files = ['package.py', 'licenses/NOTICE.txt']

    def locate_file(self, path):
        return self.root / path


class InventoryContracts(unittest.TestCase):
    def test_notice_hashes_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'package.py').write_text('value = 1\n')
            (root / 'licenses').mkdir()
            (root / 'licenses/NOTICE.txt').write_text('Fixture notice\n')
            out = root / 'inventory'
            result = generate(out, [root / 'package.py'], [Distribution(root, 'MIT')], root)
            self.assertEqual(result['components'], 1)
            self.assertEqual(result['notices'], 1)
            self.assertEqual(result['gaps'], [])
            self.assertEqual(next((out / 'notices').rglob('*.txt')).read_text(), 'Fixture notice\n')
            self.assertEqual(len(result['artifacts'][0]['sha256']), 64)
            with self.assertRaises(FileExistsError):
                generate(out, [], [], root)

    def test_missing_notice_and_license_are_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = Distribution(root)
            value.files = []
            result = generate(root / 'inventory', [], [value], root)
            self.assertEqual(len(result['gaps']), 3)

    def test_outside_environment_notice_is_not_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'NOTICE.txt').write_text('Outside environment\n')
            prefix = root / 'environment'
            prefix.mkdir()
            value = Distribution(root, 'MIT')
            value.files = ['NOTICE.txt']
            result = generate(root / 'inventory', [], [value], prefix)
            self.assertEqual(result['notices'], 0)
            self.assertEqual(len(result['gaps']), 2)


if __name__ == '__main__':
    unittest.main()

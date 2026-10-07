"""Native cold fixture recovery; skips where optional native engines are absent."""
import asyncio
from importlib.util import find_spec
from pathlib import Path
import tempfile
import unittest

from scripts.check_embedded_restore import check


@unittest.skipUnless(all(find_spec(name) for name in ('ladybug', 'lancedb', 'tree_sitter_language_pack')),
                     'Requires the modified parser and embedded engine profile')
class RestoreContracts(unittest.TestCase):
    def test_cold_publication_and_fire_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(check(Path(directory).resolve()))
        self.assertTrue(result['published_source_and_vectors_equal'])
        self.assertTrue(result['deletion_tombstone_and_expiry_preserved'])


if __name__ == '__main__':
    unittest.main()

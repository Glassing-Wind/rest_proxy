"""Offline checks for source drift, unsafe paths and out-of-bounds citations."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from verify_receipt import verify


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "receipt"
        shutil.copytree(Path(__file__).parent, self.root)

    def change_receipt(self, edit):
        path = self.root / "finding.json"
        data = json.loads(path.read_text())
        edit(data)
        path.write_text(json.dumps(data))

    def test_valid_receipt(self):
        self.assertEqual(verify(self.root)["findings"], 3)

    def test_changed_source_rejected(self):
        with (self.root / "source/application-apply.js").open("a") as stream:
            stream.write("// drift\n")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify(self.root)

    def test_unbounded_citation_rejected(self):
        self.change_receipt(lambda data: data["findings"][0]["citations"][0].update(end_line=99999))
        with self.assertRaisesRegex(ValueError, "outside retained"):
            verify(self.root)

    def test_escaping_source_rejected(self):
        self.change_receipt(lambda data: data["sources"][0].update(snapshot="../outside.js"))
        with self.assertRaisesRegex(ValueError, "escapes receipt"):
            verify(self.root)


if __name__ == "__main__":
    unittest.main()

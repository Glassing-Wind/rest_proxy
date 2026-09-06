import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "code_intel" / "file_describe.py"


def load_module():
    spec = importlib.util.spec_from_file_location("file_describe_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    helpers = types.ModuleType("_helpers")
    helpers.get_memory_modules = lambda: (None, None, None, None, None)
    diagnostics = types.ModuleType("ts_diagnostics")
    diagnostics.normalize_ts_pack_result = lambda *args: {}
    with mock.patch.dict(
        sys.modules,
        {"_helpers": helpers, "ts_diagnostics": diagnostics},
    ):
        spec.loader.exec_module(module)
    return module


class FileDescribeTests(unittest.TestCase):
    def test_format_file_purpose_uses_roles_and_key_symbols(self):
        module = load_module()

        output = module.format_file_purpose(
            "src/api/routes.py",
            ["controller_surface", "request_handler_surface"],
            ["create_order", "get_order"],
        )

        self.assertIn("indexed as controller, request handler", output)
        self.assertIn("`create_order`", output)

    def test_format_file_purpose_handles_support_files(self):
        module = load_module()

        output = module.format_file_purpose("config/settings.json", [], [])

        self.assertIn("support or data file", output)


if __name__ == "__main__":
    unittest.main()

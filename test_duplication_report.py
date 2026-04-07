import importlib.util
import sys
import types
import unittest
from unittest import mock


HELPERS_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/duplication_helpers.py"
REPORT_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/duplication_report.py"


def load_report_module():
    helper_spec = importlib.util.spec_from_file_location(
        "tools.brain.search.duplication_helpers", HELPERS_PATH
    )
    helper_module = importlib.util.module_from_spec(helper_spec)
    assert helper_spec.loader is not None

    report_spec = importlib.util.spec_from_file_location(
        "duplication_report_under_test", REPORT_PATH
    )
    report_module = importlib.util.module_from_spec(report_spec)
    assert report_spec.loader is not None

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    search_pkg = types.ModuleType("tools.brain.search")
    search_pkg.__path__ = []

    with mock.patch.dict(
        sys.modules,
        {
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.search": search_pkg,
            "tools.brain.search.duplication_helpers": helper_module,
        },
    ):
        helper_spec.loader.exec_module(helper_module)
        report_spec.loader.exec_module(report_module)
    return report_module


class DuplicationReportTests(unittest.TestCase):
    def setUp(self):
        self.module = load_report_module()

    def test_append_group_pairs_formats_preview_lines(self):
        lines = []
        groups = [
            [
                {
                    "file_path": "src/a.ts",
                    "content": "alpha()\nline2",
                    "metadata": {"start_line": 10},
                },
                {
                    "file_path": "src/b.ts",
                    "content": "beta()\nline2",
                    "metadata": {"start_line": 20},
                },
            ]
        ]

        self.module.append_group_pairs(
            lines,
            title="Cross-file",
            groups=groups,
            cross_file=True,
            max_pairs=5,
            same_file_allowed=lambda *args, **kwargs: True,
        )

        output = "\n".join(lines)
        self.assertIn("Cross-file (1)", output)
        self.assertIn("src/a.ts:10 ↔ src/b.ts:20", output)
        self.assertIn("A: alpha()", output)
        self.assertIn("B: beta()", output)

    def test_append_duplicate_symbol_names_formats_records(self):
        lines = []
        self.module.append_duplicate_symbol_names(
            lines,
            [
                {"name": "buildRouter", "count": 2, "files": ["src/a.ts", "src/b.ts"]},
            ],
        )
        output = "\n".join(lines)
        self.assertIn("Potential duplicate symbol names", output)
        self.assertIn("buildRouter", output)
        self.assertIn("src/a.ts", output)


if __name__ == "__main__":
    unittest.main()

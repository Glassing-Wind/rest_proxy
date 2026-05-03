import sys
import tempfile
import types
import unittest
from pathlib import Path

from _tool_fingerprint import REPO_ROOT, compute_tool_fingerprint


class _FakeTool:
    def __init__(self, name, fn):
        self.name = name
        self.fn = fn


class _FakeToolManager:
    def __init__(self, tools):
        self._tools = tools

    def list_tools(self):
        return list(self._tools)


class _FakeMCP:
    def __init__(self, tools):
        self._tool_manager = _FakeToolManager(tools)


def _sample_tool():
    return None


class ToolFingerprintTests(unittest.TestCase):
    def test_fingerprint_changes_when_loaded_local_module_changes(self):
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmpdir:
            module_path = Path(tmpdir) / "fingerprint_probe.py"
            module_path.write_text("VALUE = 1\n", encoding="utf-8")

            probe_module = types.ModuleType("fingerprint_probe")
            probe_module.__file__ = str(module_path)
            sys.modules["fingerprint_probe"] = probe_module
            try:
                mcp = _FakeMCP([_FakeTool("sample_tool", _sample_tool)])
                before, tool_count_before = compute_tool_fingerprint(mcp)
                module_path.write_text("VALUE = 2\n", encoding="utf-8")
                after, tool_count_after = compute_tool_fingerprint(mcp)
            finally:
                sys.modules.pop("fingerprint_probe", None)

        self.assertEqual(tool_count_before, 1)
        self.assertEqual(tool_count_after, 1)
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()

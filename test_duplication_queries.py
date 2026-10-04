import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "duplication_queries.py"


spec = importlib.util.spec_from_file_location("duplication_queries_under_test", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class DuplicationQueryTests(unittest.TestCase):
    def test_build_queries_includes_filter_sql(self):
        queries = module.build_queries(" AND file_path LIKE ANY(%(include_paths)s)")
        self.assertIn("include_paths", queries["exact"])
        self.assertIn("LIMIT %(sample_size)s", queries["semantic"])
        self.assertIn("count(*) AS n", queries["winnow_count"])


if __name__ == "__main__":
    unittest.main()

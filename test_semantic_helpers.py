import importlib.util
import unittest


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/semantic_helpers.py"


spec = importlib.util.spec_from_file_location("semantic_helpers_under_test", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class SemanticHelperTests(unittest.TestCase):
    def test_meta_helpers_and_filters(self):
        meta = {
            "language": "python",
            "file_imports": ["os"],
            "file_symbols": ["run"],
            "file_diagnostics": {"count": 1},
            "context_path": ["Service", "run"],
        }
        self.assertGreater(module.meta_score(meta), 0)
        self.assertTrue(
            module.passes_filters(
                meta,
                languages=["python"],
                min_imports=1,
                min_symbols=1,
                require_diagnostics=True,
                require_context=True,
            )
        )
        self.assertTrue(any("lang=python" in line for line in module.format_meta(meta)))

    def test_dedupe_and_caps_are_stable(self):
        rows = [
            {"file_path": "src/a.py", "project_id": "p", "rrf": 1.0, "content": "a"},
            {"file_path": "src/a.py", "project_id": "p", "rrf": 0.9, "content": "b"},
            {"file_path": "tests/x.py", "project_id": "p", "rrf": 0.8, "content": "c"},
        ]
        self.assertEqual(len(module.dedupe_files(rows)), 2)
        self.assertEqual(len(module.cap_per_file(rows, 1)), 2)
        self.assertEqual(len(module.cap_per_dir(rows, 1)), 2)

    def test_render_results_formats_single_project_output(self):
        rows = [
            {
                "file_path": "src/a.py",
                "project_id": "proj",
                "rrf": 1.2345,
                "content": "def run():\n    pass",
                "_meta": {"language": "python"},
            }
        ]
        lines = module.render_results(
            rows,
            query="run",
            k=5,
            multi=False,
            pid_to_name={"proj": "repo"},
            include_metadata=True,
        )
        output = "\n".join(lines)
        self.assertIn("--- src/a.py (Score: 1.2345) ---", output)
        self.assertIn("lang=python", output)
        self.assertIn("def run()", output)

    def test_cargo_helpers_attach_and_filter(self):
        rows = [
            {"file_path": "crates/api/src/lib.rs", "project_id": "proj", "rrf": 1.0, "content": "fn run() {}", "_meta": {}},
            {"file_path": "crates/core/src/lib.rs", "project_id": "proj", "rrf": 0.9, "content": "fn serve() {}", "_meta": {}},
        ]
        crate_rows = [
            {"crate": "api", "crate_name": "api", "manifest_path": "crates/api/Cargo.toml"},
            {"crate": "core", "crate_name": "core_lib", "manifest_path": "crates/core/Cargo.toml"},
        ]
        module.attach_cargo_crate_meta(rows, crate_rows)
        self.assertEqual(rows[0]["_meta"]["cargo_crate"], "api")
        self.assertEqual(rows[1]["_meta"]["cargo_crate_name"], "core_lib")
        filtered = module.filter_by_cargo_crate(rows, "core")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["file_path"], "crates/core/src/lib.rs")
        self.assertTrue(any("crate=api" in line for line in module.format_meta(rows[0]["_meta"])))


if __name__ == "__main__":
    unittest.main()

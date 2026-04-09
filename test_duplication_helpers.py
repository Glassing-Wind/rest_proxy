import importlib.util
import unittest


MODULE_PATH = "/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/duplication_helpers.py"


spec = importlib.util.spec_from_file_location("duplication_helpers_under_test", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class DuplicationHelperTests(unittest.TestCase):
    def test_path_allowed_respects_include_and_exclude_patterns(self):
        self.assertTrue(
            module.path_allowed(
                "src/api/routes.ts",
                include_patterns=["src/**/*.ts", "src/*.ts"],
                exclude_patterns=["**/test_*"],
            )
        )
        self.assertFalse(
            module.path_allowed(
                "src/api/test_routes.ts",
                include_patterns=["src/**/*.ts", "src/*.ts"],
                exclude_patterns=["**/test_*"],
            )
        )

    def test_pick_pair_prefers_cross_file_when_requested(self):
        group = [
            {"file_path": "src/a.ts"},
            {"file_path": "src/a.ts"},
            {"file_path": "src/b.ts"},
        ]
        pair = module.pick_pair(group, cross_file=True)
        self.assertEqual(pair[0]["file_path"], "src/a.ts")
        self.assertEqual(pair[1]["file_path"], "src/b.ts")

    def test_same_file_allowed_enforces_limits_and_token_threshold(self):
        self.assertFalse(
            module.same_file_allowed(
                "src/a.ts",
                "foo bar",
                {"src/a.ts": 1},
                max_same_file_pairs_per_file=1,
                same_file_min_tokens=1,
            )
        )
        self.assertFalse(
            module.same_file_allowed(
                "src/b.ts",
                "foo",
                {},
                max_same_file_pairs_per_file=3,
                same_file_min_tokens=2,
            )
        )

    def test_winnow_fingerprints_and_normalization_are_stable(self):
        tokens = module.normalize_tokens(module.tokenize("count = total + 42"))
        self.assertIn("<id>", tokens)
        self.assertIn("<num>", tokens)
        self.assertTrue(module.winnow_fingerprints(tokens * 3, k=3, window=2))

    def test_default_duplication_exclude_patterns_only_apply_without_include_paths(self):
        self.assertIn(
            "**/docs/node_types/**",
            module.default_duplication_exclude_patterns([]),
        )
        self.assertIn(
            "docs/node_types/**",
            module.default_duplication_exclude_patterns([]),
        )
        self.assertEqual(
            module.default_duplication_exclude_patterns(["src/**/*.ts"]),
            [],
        )

    def test_root_level_generated_docs_paths_are_excluded_by_default(self):
        self.assertFalse(
            module.path_allowed(
                "docs/node_types/swift-node-types.json",
                include_patterns=[],
                exclude_patterns=module.default_duplication_exclude_patterns([]),
            )
        )

    def test_keep_default_winnow_pair_suppresses_low_signal_default_pairs(self):
        low_signal = ({}, {}, 0.50, 0.0)
        stronger = ({}, {}, 0.72, 0.0)
        self.assertFalse(
            module.keep_default_winnow_pair(low_signal, include_patterns=[])
        )
        self.assertTrue(
            module.keep_default_winnow_pair(stronger, include_patterns=[])
        )
        self.assertTrue(
            module.keep_default_winnow_pair(low_signal, include_patterns=["src/**/*.py"])
        )

    def test_filter_duplicate_symbol_name_records_skips_common_noise_by_default(self):
        records = [
            {"name": "__init__", "count": 3, "files": ["a.py", "b.py"]},
            {"name": "buildRouter", "count": 2, "files": ["src/a.ts", "src/b.ts"]},
        ]
        filtered = module.filter_duplicate_symbol_name_records(
            records,
            include_patterns=[],
        )
        self.assertEqual([rec["name"] for rec in filtered], ["buildRouter"])


if __name__ == "__main__":
    unittest.main()

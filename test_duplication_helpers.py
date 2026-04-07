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


if __name__ == "__main__":
    unittest.main()

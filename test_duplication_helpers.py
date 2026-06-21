import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "duplication_helpers.py"


spec = importlib.util.spec_from_file_location("duplication_helpers_under_test", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class DuplicationHelperTests(unittest.TestCase):
    def test_directory_include_pattern_normalizes_and_rejects_parent_traversal(self):
        self.assertEqual(module.directory_include_pattern("/src/api/"), "src/api/**")
        self.assertIsNone(module.directory_include_pattern("."))
        with self.assertRaises(ValueError):
            module.directory_include_pattern("../other-repo")

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
            "**/test_*.py",
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
        self.assertFalse(
            module.keep_default_winnow_pair(low_signal, include_patterns=["src/**/*.py"])
        )

    def test_keep_default_winnow_pair_suppresses_structural_false_positive_without_identifier_overlap(self):
        pair = (
            {"file_path": "tools/brain/graph/overview.py", "content": "def summarize_repo_links(project_path: str) -> list[str]:"},
            {"file_path": "memory/summary.py", "content": "def normalize_summary_dict(data: Any) -> Dict[str, List[str]]:"},
            1.0,
            1.0,
        )
        self.assertFalse(module.keep_default_winnow_pair(pair, include_patterns=[]))

    def test_keep_default_winnow_pair_keeps_similar_preview_identifiers(self):
        pair = (
            {"file_path": "src/repo_links.py", "content": "def summarize_repo_links(project_path: str) -> list[str]:"},
            {"file_path": "src/repo_links_copy.py", "content": "def summarize_repo_links(project_path: str) -> list[str]:"},
            1.0,
            1.0,
        )
        self.assertTrue(module.keep_default_winnow_pair(pair, include_patterns=[]))

    def test_keep_default_winnow_pair_suppresses_import_only_pairs(self):
        pair = (
            {"file_path": "test_a.py", "content": "import asyncio\nimport unittest"},
            {"file_path": "test_b.py", "content": "import asyncio\nimport unittest"},
            1.0,
            1.0,
        )
        self.assertFalse(module.keep_default_winnow_pair(pair, include_patterns=[]))

    def test_keep_default_winnow_pair_suppresses_python_main_guards(self):
        pair = (
            {"file_path": "test_a.py", "content": 'if __name__ == "__main__":\n    unittest.main()'},
            {"file_path": "test_b.py", "content": 'if __name__ == "__main__":\n    unittest.main()'},
            1.0,
            1.0,
        )
        self.assertFalse(module.keep_default_winnow_pair(pair, include_patterns=[]))

    def test_filter_duplicate_symbol_name_records_skips_common_noise_by_default(self):
        records = [
            {"name": "__init__", "count": 3, "files": ["a.py", "b.py"]},
            {"name": "_execute_read", "count": 14, "files": ["a.py", "b.py"]},
            {"name": "buildRouter", "count": 2, "files": ["src/a.ts", "src/b.ts"]},
        ]
        filtered = module.filter_duplicate_symbol_name_records(
            records,
            include_patterns=[],
        )
        self.assertEqual([rec["name"] for rec in filtered], ["buildRouter"])

    def test_is_low_signal_preview_flags_file_headers(self):
        self.assertFalse(module.is_low_signal_preview("// File: src/a.ts\nfunction x() {}"))
        self.assertTrue(module.is_low_signal_preview('"""module docstring'))
        self.assertTrue(module.is_low_signal_preview('if __name__ == "__main__":'))
        self.assertFalse(module.is_low_signal_preview("fn real_logic() {"))

    def test_preview_line_skips_synthetic_file_headers(self):
        self.assertEqual(
            module.preview_line("// File: src/a.ts\nfunction x() {}\n"),
            "function x() {}",
        )

    def test_preview_identifiers_and_path_overlap_extract_useful_signal(self):
        self.assertIn("summarize_repo_links", module.preview_identifiers("def summarize_repo_links(project_path):"))
        self.assertIn("client", module.path_token_overlap("src/client_api.py", "tests/client_api_copy.py"))

    def test_has_actionable_duplicate_signal_requires_identifier_or_path_overlap(self):
        actionable = module.has_actionable_duplicate_signal(
            {"file_path": "src/client_api.py", "content": "def normalize_client_name(value):"},
            {"file_path": "src/client_api_copy.py", "content": "def normalize_client_title(value):"},
        )
        structural_only = module.has_actionable_duplicate_signal(
            {"file_path": "a.py", "content": "def summarize_repo_links(project_path: str) -> list[str]:"},
            {"file_path": "b.py", "content": "def normalize_summary_dict(data: Any) -> Dict[str, List[str]]:"},
        )
        self.assertTrue(actionable)
        self.assertFalse(structural_only)

    def test_duplicate_candidate_details_scores_actionable_pairs(self):
        details = module.duplicate_candidate_details(
            {
                "file_path": "src/client_api.py",
                "content": "def normalize_client_name(value):",
            },
            {
                "file_path": "src/client_api_copy.py",
                "content": "def normalize_client_name(value):",
            },
            score=0.96,
            struct_score=1.0,
        )
        self.assertTrue(details["actionable"])
        self.assertGreater(details["candidate_score"], 0.7)
        self.assertIn("same lead statement", details["reasons"])
        self.assertIn("client", details["path_overlap"])

    def test_duplicate_candidate_details_rejects_low_signal_headers(self):
        details = module.duplicate_candidate_details(
            {
                "file_path": "scripts/a.py",
                "content": "#!/usr/bin/env python3",
            },
            {
                "file_path": "scripts/b.py",
                "content": "#!/usr/bin/env python3",
            },
            score=0.99,
            struct_score=1.0,
        )
        self.assertFalse(details["actionable"])
        self.assertNotIn("same lead statement", details["reasons"])

    def test_duplicate_candidate_details_does_not_use_path_overlap_alone_unless_score_is_extreme(self):
        details = module.duplicate_candidate_details(
            {
                "file_path": "tools/brain/search/semantic.py",
                "content": "async def _load_path_hint_rows(query):",
            },
            {
                "file_path": "tools/brain/search/semantic_helpers.py",
                "content": "def implementation_path_hint_hit(file_path, query=None):",
            },
            score=0.88,
            struct_score=1.0,
        )
        self.assertFalse(details["actionable"])

    def test_duplicate_candidate_details_rejects_generic_single_identifier_overlap(self):
        details = module.duplicate_candidate_details(
            {
                "file_path": "tools/brain/graph/flow_summary.py",
                "content": "def _format_backend_flow_empty_message(crate_rows) -> str:",
            },
            {
                "file_path": "tools/brain/search/semantic_helpers.py",
                "content": "def match_cargo_crate(file_path: str | None, crate_rows) -> tuple[str | None, str | None]:",
            },
            score=0.76,
            struct_score=1.0,
        )
        self.assertFalse(details["actionable"])

    def test_duplicate_candidate_details_rejects_generic_session_identifier_overlap(self):
        details = module.duplicate_candidate_details(
            {
                "file_path": "proxy/handlers_memory.py",
                "content": "async def _extract_and_cache_skeleton_bg(session_id: str, raw_content: str) -> None:",
            },
            {
                "file_path": "memory/store_core.py",
                "content": "async def get_recent_turns(session_id: str) -> List[Dict[str, Any]]:",
            },
            score=0.58,
            struct_score=0.33,
        )
        self.assertFalse(details["actionable"])


if __name__ == "__main__":
    unittest.main()

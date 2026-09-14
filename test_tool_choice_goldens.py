import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
GOLDENS_PATH = REPO_ROOT / "benchmarks" / "tool_choice_goldens.json"


class ToolChoiceGoldensTests(unittest.TestCase):
    def test_tool_choice_goldens_are_well_formed(self):
        payload = json.loads(GOLDENS_PATH.read_text())
        cases = payload.get("cases")
        self.assertIsInstance(cases, list)
        self.assertGreater(len(cases), 0)

        seen_ids = set()
        for case in cases:
            self.assertIn("id", case)
            self.assertNotIn(case["id"], seen_ids)
            seen_ids.add(case["id"])

            self.assertTrue(case.get("intent"))
            for key in ("preferred_tools", "acceptable_fallbacks", "avoid_as_primary"):
                self.assertIsInstance(case.get(key), list)
                self.assertGreater(len(case[key]), 0)
                self.assertEqual(len(case[key]), len(set(case[key])))

            self.assertTrue(set(case["preferred_tools"]).isdisjoint(case["avoid_as_primary"]))


if __name__ == "__main__":
    unittest.main()

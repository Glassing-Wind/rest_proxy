"""Offline calibration isolation tests; no model/service or state required."""
import copy
import json
from pathlib import Path
import unittest

from scripts.task_review_calibration import calibration_prompt


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(Path('benchmarks/reports/2026-10-07/'
                                      'advisory-review-calibration-fixture.json').read_text())

    def test_grading_key_isolated_and_fixture_unchanged(self):
        before = copy.deepcopy(self.fixture)
        prompt = calibration_prompt(self.fixture, 'full')
        self.assertEqual(set(prompt), {'instructions', 'finding', 'source'})
        self.assertEqual(len(prompt['source']), 2)
        self.fixture['expected'] = [{'secret_answer': 'DO NOT LEAK'}]
        self.assertEqual(prompt, calibration_prompt(self.fixture, 'full'))
        self.fixture['expected'] = before['expected']
        self.assertEqual(self.fixture, before)

    def test_missing_definition_and_budget(self):
        prompt = calibration_prompt(self.fixture, 'missing-definition')
        self.assertEqual([s['path'] for s in prompt['source']], ['fixture/intake.py'])
        with self.assertRaises(ValueError):
            calibration_prompt(self.fixture, 'unknown')
        self.fixture['finding'] = 'x' * 8192
        with self.assertRaises(ValueError):
            calibration_prompt(self.fixture, 'full')


if __name__ == '__main__':
    unittest.main()

"""Offline bounded review export tests; no cloud transmission."""
import unittest
from scripts.task_review_export import review_bundle


class ReviewExportTests(unittest.TestCase):
    def task(self):
        return dict(id='fixture', revision=4, goal='Explain', workspace='/private',
                    claim={'token': 'private-token'}, submissions=[{'finding': {
                    'answer': 'Fixture', 'retained_evidence': [{'source': 'return 1'}]}}])

    def test_private_metadata_excluded_and_evidence_retained(self):
        task = self.task()
        bundle = review_bundle(task, 1)
        self.assertNotIn('claim', bundle)
        self.assertNotIn('workspace', bundle)
        self.assertEqual(bundle['finding']['retained_evidence'][0]['source'], 'return 1')
        self.assertIn('historical', bundle['instructions'])
        self.assertEqual(task['revision'], 4)

    def test_invalid_and_oversized_export_rejected(self):
        for submission in [True, 0, 2]:
            with self.assertRaises(ValueError):
                review_bundle(self.task(), submission)
        task = self.task()
        task['submissions'][0]['finding']['answer'] = 'x' * 8192
        with self.assertRaises(ValueError):
            review_bundle(task, 1)


if __name__ == '__main__':
    unittest.main()

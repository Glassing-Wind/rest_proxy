"""Offline helper contract checks; no models, task state or services."""
import copy
import unittest
from memory.helper_extraction import FIELDS, validate_extraction


class ExtractionTests(unittest.TestCase):
    def result(self):
        return dict(schema_version=1, records=[dict(field='project_name', value='FIRE',
                    line=1, quote='Project: FIRE')], unresolved=sorted(FIELDS - {'project_name'}))

    def test_detached_quote_match_is_not_semantic_approval(self):
        result = self.result()
        result['records'][0]['value'] = 'Wrong inference'
        validated = validate_extraction(result, 'Project: FIRE')
        self.assertIn('supervisor verification required', validated['validation'])
        validated['extraction']['records'][0]['value'] = 'changed'
        self.assertEqual(result['records'][0]['value'], 'Wrong inference')

    def test_forged_quote_wrong_line_duplicate_and_missing_rejected(self):
        for change in [{'quote': 'invented'}, {'line': True}, {'line': 2}, {'field': 'execute'}]:
            result = self.result()
            result['records'][0].update(change)
            with self.assertRaises(ValueError):
                validate_extraction(result, 'Project: FIRE')
        for unresolved in [[], sorted(FIELDS), ['raw_byte_limit'] * 4]:
            result = self.result()
            result['unresolved'] = unresolved
            with self.assertRaises(ValueError):
                validate_extraction(result, 'Project: FIRE')

    def test_source_budget_and_exact_shape(self):
        result = copy.deepcopy(self.result())
        with self.assertRaises(ValueError):
            validate_extraction(result, 'é' * 5000)
        result['approval'] = True
        with self.assertRaises(ValueError):
            validate_extraction(result, 'Project: FIRE')


if __name__ == '__main__':
    unittest.main()

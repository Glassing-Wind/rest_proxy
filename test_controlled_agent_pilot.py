"""Offline benchmark measurement checks; no services or credentials required."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'scripts'))
import run_controlled_agent_pilot as pilot


class MeasurementTests(unittest.TestCase):
    def test_read_only_mcp_approval_is_scoped(self):
        from types import SimpleNamespace
        args = SimpleNamespace(codex='codex', root=Path('/repo'), model='test',
                               effort='low', mcp_url='http://localhost/mcp')
        native = pilot.command(args, 'native', Path('/output'))
        mcp = pilot.command(args, 'mcp', Path('/output'))
        self.assertEqual(native[:3], ['codex', '--no-daemon', 'exec'])
        self.assertFalse(any('mcp_servers' in part for part in native))
        self.assertIn('approval_policy="never"', mcp)
        self.assertIn('mcp_servers.graphrag.startup_readiness="catalog"', mcp)
        self.assertIn('mcp_optional_startup_grace_ms=0', mcp)
        approvals = [part for part in mcp if '.approval_mode=' in part]
        self.assertEqual(len(approvals), len(pilot.TOOLS))
        self.assertNotIn('index_workspace', ' '.join(mcp))

    def test_usage_not_estimated_or_zeroed(self):
        self.assertIsNone(pilot.summarize_events('', 'native')['usage'])
        import json
        events = '\n'.join(json.dumps({'type': 'turn.completed', 'usage': value})
                           for value in ({'input_tokens': 10, 'cached_input_tokens': 4, 'output_tokens': 2},
                                         {'input_tokens': 20, 'cached_input_tokens': 5, 'output_tokens': 3}))
        self.assertEqual(pilot.summarize_events(events, 'native')['usage'],
                         {'input_tokens': 30, 'cached_input_tokens': 9, 'output_tokens': 5})

    def test_native_mcp_contamination(self):
        raw = '{"type":"item.completed","item":{"type":"mcp_tool_call","server":"graphrag","tool":"get_indexing_health","status":"completed","result":{"content":[]}}}'
        self.assertTrue(pilot.summarize_events(raw, 'native')['policy_violations'])
        self.assertFalse(pilot.summarize_events(raw, 'mcp')['policy_violations'])
        self.assertTrue(pilot.summarize_events(raw.replace('get_indexing_health', 'index_workspace'), 'mcp')['policy_violations'])

    def test_mcp_requires_observed_success(self):
        self.assertTrue(pilot.summarize_events('', 'mcp')['policy_violations'])
        raw = '{"type":"item.completed","item":{"type":"mcp_tool_call","server":"graphrag","tool":"get_indexing_health","status":"failed","error":{"message":"unavailable"}}}'
        self.assertTrue(pilot.summarize_events(raw, 'mcp')['policy_violations'])

    def test_mismatch_and_contamination(self):
        self.assertTrue(pilot.summarize_events('{"model":"different"}', 'native', 'requested')['policy_violations'])
        self.assertTrue(pilot.summarize_events('{"text":"benchmarks/reports/old"}', 'native')['policy_violations'])
        self.assertEqual(pilot.summarize_events('', 'native')['model_verification'], 'requested_only_unverified')

    def test_no_answer_hints(self):
        prompt = pilot.prompt_for({'prompt': 'Question', 'expected_evidence': ['SECRET_HINT']}, 'native', '/repo')
        self.assertNotIn('SECRET_HINT', prompt)
        mcp_prompt = pilot.prompt_for({'prompt': 'Question'}, 'mcp', '/repo')
        self.assertIn('tool-search/discovery', mcp_prompt)

    def test_health_missing_and_drift(self):
        with self.assertRaises(ValueError):
            pilot.health_identity('unavailable')
        raw = '\n'.join(f'{field}: `done`' for field in pilot.FIELDS)
        first = pilot.health_identity(raw)
        self.assertNotEqual(first['sha256'], pilot.health_identity(raw.replace('done', 'changed', 1))['sha256'])


if __name__ == '__main__':
    unittest.main()

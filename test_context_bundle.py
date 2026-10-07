"""Deterministic whole-declared-request budgets; no storage or inference services."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route

from memory.context_bundle import build_context_bundle, canonical
from tools.brain import context
from tools.brain.embedded_rest import make_read_endpoint


def request():
    return {'project_id': 'p', 'session_id': 's', 'task_id': 't', 'goal': 'Investigate',
            'instructions': 'Use evidence', 'messages': [{'role': 'user', 'content': 'Question'}],
            'tools': [{'name': 'read', 'inputSchema': {'type': 'object'}}],
            'context_capacity': 4000, 'output_reserve': 500, 'safety_margin': 100,
            'framing_tokens': 100, 'evidence': []}


def evidence(identity, text='source', kind='repository', relevance=1):
    sha = hashlib.sha256(text.encode()).hexdigest()
    return {'id': identity, 'project_id': 'p', 'session_id': 's', 'task_id': 't', 'text': text,
            'kind': kind, 'relevance': relevance, 'current_hash': sha,
            'citation': {'source_id': 'file', 'content_hash': sha, 'run_id': 'run', 'start_line': 1}}


class BundleContracts(unittest.TestCase):
    def test_complete_accounting_selection_and_determinism(self):
        value = request()
        value['evidence'] = [evidence('low', 'l' * 1000, relevance=0), evidence('high', 'h' * 1000, relevance=2)]
        bundle = build_context_bundle(value)
        account = bundle['accounting']
        self.assertEqual(account['assembled_input'], len(canonical(bundle['payload']).encode()))
        self.assertTrue(account['fits_accounted_budget'])
        self.assertEqual(bundle['selected'][0]['id'], 'high')
        self.assertTrue(bundle['selected'][0]['citation']['content_hash'])
        value['evidence'].reverse()
        self.assertEqual(build_context_bundle(value), bundle)
        self.assertEqual(bundle['payload']['messages'][:1], value['messages'])
        self.assertLessEqual(account['assembled_input'] + 700, value['context_capacity'])

    def test_mandatory_overflow_and_unknown_provider_context(self):
        value = request()
        value['instructions'] = 'x' * 4000
        value['evidence'] = [evidence('e')]
        bundle = build_context_bundle(value)
        self.assertEqual(bundle['status'], 'mandatory-over-budget')
        self.assertFalse(bundle['accounting']['fits_accounted_budget'])
        self.assertEqual(bundle['payload']['instructions'], value['instructions'])
        self.assertEqual(bundle['selected'], [])
        value = request()
        value.update(provider_continuation=True, evidence=[evidence('h', kind='history'), evidence('e')])
        bundle = build_context_bundle(value)
        self.assertEqual(bundle['status'], 'provider-context-unknown')
        self.assertFalse(bundle['accounting']['fits_accounted_budget'])
        self.assertEqual(bundle['selected'], [])
        value['provider_context_tokens'] = 500
        self.assertEqual(build_context_bundle(value)['status'], 'assembled')

    def test_scope_freshness_duplicates_and_history_boundaries(self):
        value = request()
        duplicate = evidence('duplicate')
        wrong = evidence('wrong')
        wrong['citation']['project_id'] = 'other'
        stale = evidence('stale')
        stale['current_hash'] = None
        history = evidence('history', 'Question', kind='history')
        value['evidence'] = [evidence('a'), duplicate, wrong, stale, history]
        bundle = build_context_bundle(value)
        self.assertEqual([item['id'] for item in bundle['selected']], ['a'])
        reasons = {item['id']: item['reason'] for item in bundle['omissions']}
        self.assertEqual(reasons['wrong'], 'scope-mismatch')
        self.assertEqual(reasons['stale'], 'source-changed-or-unverified')
        self.assertEqual(reasons['duplicate'], 'duplicate-evidence')
        self.assertEqual(reasons['history'], 'provider-or-message-history-already-present')
        value['existing_bundle_ids'] = [bundle['bundle_id']]
        suppressed = build_context_bundle(value)
        self.assertEqual(suppressed['status'], 'already-present')
        self.assertEqual(suppressed['selected'], [])
        self.assertEqual(suppressed['payload']['messages'], value['messages'])
        value['existing_evidence_ids'] = ['a']
        existing = build_context_bundle(value)
        self.assertIn({'id': 'a', 'reason': 'already-present'}, existing['omissions'])
        self.assertEqual(existing['selected'], [])
        self.assertIn({'id': 'duplicate', 'reason': 'equivalent-evidence-already-present'}, existing['omissions'])

    def test_whole_evidence_omissions_no_truncated_citations(self):
        value = request()
        value['context_capacity'] = 1000
        value['evidence'] = [evidence('large', 'x' * 1000)]
        bundle = build_context_bundle(value)
        self.assertEqual(bundle['selected'], [])
        self.assertEqual(bundle['omissions'], [{'id': 'large', 'reason': 'request-budget'}])
        self.assertTrue(bundle['accounting']['fits_accounted_budget'])

    def test_tool_pairs_and_invalid_contracts(self):
        value = request()
        value['messages'] = [{'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'c'}]},
                             {'role': 'tool', 'tool_call_id': 'c', 'content': 'source'}]
        self.assertEqual(build_context_bundle(value)['payload']['messages'], value['messages'])
        value['messages'].pop()
        with self.assertRaises(ValueError):
            build_context_bundle(value)
        for change in [{'context_capacity': True}, {'messages': [{'role': 'user', 'content': []}]},
                       {'unknown': True}, {'evidence': [evidence('same'), evidence('same')]}]:
            value = request()
            value.update(change)
            with self.assertRaises(ValueError):
                build_context_bundle(value)


class BundleTools(unittest.IsolatedAsyncioTestCase):
    async def test_registration_mcp_rest_parity_and_errors(self):
        mcp = FastMCP('context-test')
        with mock.patch.dict('os.environ', {'LM_PROXY_CONTEXT_ENABLED': '0'}):
            context.register(mcp)
            self.assertIsNone(mcp._tool_manager.get_tool('assemble_context_bundle'))
        with mock.patch.dict('os.environ', {'LM_PROXY_CONTEXT_ENABLED': '1', 'LM_PROXY_CONTEXT_TOKENIZER_FILE': ''}):
            context.register(mcp)
            tool = mcp._tool_manager.get_tool('assemble_context_bundle')
            expected = await tool.run({'request': request()})
            app = Starlette(routes=[Route('/evidence/read', make_read_endpoint(mcp), methods=['POST'])])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/evidence/read', json={'tool': tool.name, 'arguments': {'request': request()}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), expected)
            self.assertEqual((await context.assemble_context_bundle({}))['status'], 'invalid-context-request-or-tokenizer')

    async def test_actual_local_tokenizer_and_no_silent_fallback(self):
        try:
            from tokenizers import Tokenizer
            from tokenizers.models import WordLevel
            from tokenizers.pre_tokenizers import Whitespace
        except ImportError:
            self.skipTest('Optional tokenizers package unavailable')
        with tempfile.TemporaryDirectory() as state:
            path = Path(state) / 'tokenizer.json'
            tokenizer = Tokenizer(WordLevel({'[UNK]': 0}, unk_token='[UNK]'))
            tokenizer.pre_tokenizer = Whitespace()
            tokenizer.enable_truncation(max_length=1)
            tokenizer.save(str(path))
            with mock.patch.dict('os.environ', {'LM_PROXY_CONTEXT_TOKENIZER_FILE': str(path)}):
                result = await context.assemble_context_bundle(request())
            self.assertTrue(result['accounting']['method'].startswith('local-tokenizer-json-sha256:'))
            tokenizer.no_truncation()
            self.assertEqual(result['accounting']['assembled_input'], len(tokenizer.encode(canonical(result['payload'])).ids))
            self.assertGreater(result['accounting']['assembled_input'], 1)
            with mock.patch.dict('os.environ', {'LM_PROXY_CONTEXT_TOKENIZER_FILE': str(path / 'missing')}):
                failed = await context.assemble_context_bundle(request())
            self.assertEqual(failed['status'], 'context-builder-unavailable')


if __name__ == '__main__':
    unittest.main()

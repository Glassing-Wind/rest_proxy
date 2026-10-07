"""Scoped snapshot-to-bundle recovery; private fixtures and no inference service."""
import hashlib
import tempfile
import unittest
from unittest import mock

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route

from memory.fire_store import FireStore
from test_context_bundle import request
from test_fire_store import checkpoint
from tools.brain import context
from tools.brain.embedded_rest import make_read_endpoint


class FireBundles(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FireStore(self.directory.name)
        self.env = mock.patch.dict('os.environ', {'LM_PROXY_FIRE_STATE': self.directory.name,
            'LM_PROXY_CONTEXT_ENABLED': '1', 'LM_PROXY_CONTEXT_TOKENIZER_FILE': ''})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        self.directory.cleanup()

    async def test_current_correction_scope_and_snapshot_originals(self):
        self.store.save(checkpoint('Obsolete conclusion'), {'e': 'original'}, 0)
        self.store.save(checkpoint('Corrected conclusion'), {'e': 'original'}, 1,
                        correction={'origin': 'review', 'reason': 'fix'})
        value = request()
        value['context_capacity'] = 12000
        bundle = await context.assemble_fire_context_bundle(value, expected_revision=2)
        self.assertEqual(bundle['fire_recovery']['revision'], 2)
        self.assertEqual(bundle['fire_recovery']['status'], 'snapshot-adapted')
        texts = [item['text'] for item in bundle['selected']]
        self.assertTrue(any('Corrected conclusion' in text for text in texts))
        self.assertFalse(any('Obsolete conclusion' in text for text in texts))
        self.assertIn('original', texts)
        self.assertEqual(bundle['payload']['goal'], value['goal'])
        self.assertEqual(bundle['payload']['instructions'], value['instructions'])
        self.assertTrue(bundle['accounting']['fits_accounted_budget'])
        conflict = await context.assemble_fire_context_bundle(value, expected_revision=1)
        self.assertEqual(conflict['fire_recovery']['status'], 'revision-conflict')
        self.assertEqual(conflict['selected'], [])
        value['task_id'] = 'other'
        isolated = await context.assemble_fire_context_bundle(value)
        self.assertEqual(isolated['fire_recovery']['status'], 'not_found')
        self.assertEqual(isolated['selected'], [])

    async def test_missing_changed_excerpt_count_and_budget_disclosures(self):
        data = checkpoint()
        data['evidence'].append({'project_id': 'p', 'session_id': 's', 'source_id': 'missing', 'source_kind': 'source'})
        self.store.save(data, {'e': 'original-text'}, 0)
        value = request()
        value['context_capacity'] = 12000
        bundle = await context.assemble_fire_context_bundle(value, max_chars=4)
        original = next(item for item in bundle['selected'] if item['citation']['source_id'] == 'e')
        self.assertEqual(original['text'], 'orig')
        self.assertTrue(original['citation']['excerpt_truncated'])
        self.assertEqual(original['citation']['content_hash'], hashlib.sha256(b'original-text').hexdigest())
        self.assertIn('fire-original-unavailable', [row['reason'] for row in bundle['omissions']])
        changed = await context.assemble_fire_context_bundle(value, current_hashes={'e': None})
        self.assertFalse(any(item['citation']['source_id'] == 'e' for item in changed['selected']))
        self.assertIn('fire-source-changed-or-deleted', [row['reason'] for row in changed['omissions']])
        capped = await context.assemble_fire_context_bundle(value, max_originals=0)
        self.assertIn('fire-original-count-limit', [row['reason'] for row in capped['omissions']])
        value['context_capacity'] = 1000
        tight = await context.assemble_fire_context_bundle(value)
        self.assertTrue(any(row['reason'] == 'request-budget' for row in tight['omissions']))

    async def test_provider_history_and_existing_bundle_not_injected_twice(self):
        self.store.save(checkpoint(), {'e': 'original'}, 0)
        value = request()
        value['context_capacity'] = 12000
        first = await context.assemble_fire_context_bundle(value)
        value['existing_bundle_ids'] = [first['bundle_id']]
        again = await context.assemble_fire_context_bundle(value)
        self.assertEqual(again['selected'], [])
        self.assertEqual(again['payload']['messages'], value['messages'])
        value.update(provider_continuation=True, provider_context_tokens=500)
        continued = await context.assemble_fire_context_bundle(value)
        self.assertEqual(continued['selected'], [])
        self.assertTrue(all(row['reason'] == 'provider-or-message-history-already-present'
                            for row in continued['omissions']))

    async def test_expiry_outage_and_deleted_scope_keep_caller_request(self):
        with mock.patch('memory.fire_store.time.time', return_value=1000):
            self.store.save(checkpoint(), {'e': 'original'}, 0, retention_seconds=60)
        bundle = await context.assemble_fire_context_bundle(request())
        self.assertEqual(bundle['fire_recovery']['status'], 'expired')
        self.assertEqual(bundle['payload']['messages'], request()['messages'])
        with mock.patch('tools.brain.context.FireStore', side_effect=OSError('sensitive')):
            outage = await context.assemble_fire_context_bundle(request())
        self.assertEqual(outage['fire_recovery']['status'], 'storage-unavailable')
        self.assertNotIn('sensitive', str(outage))
        self.store.delete('p', 's', 't', 1)
        deleted = await context.assemble_fire_context_bundle(request())
        self.assertEqual(deleted['fire_recovery']['status'], 'not_found')

    async def test_mcp_rest_parity_and_optional_registration(self):
        self.store.save(checkpoint(), {'e': 'original'}, 0)
        mcp = FastMCP('fire-bundle')
        context.register(mcp)
        tool = mcp._tool_manager.get_tool('assemble_fire_context_bundle')
        expected = await tool.run({'request': request()})
        app = Starlette(routes=[Route('/evidence/read', make_read_endpoint(mcp), methods=['POST'])])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/evidence/read', json={'tool': tool.name, 'arguments': {'request': request()}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        with mock.patch.dict('os.environ', {'LM_PROXY_FIRE_STATE': ''}):
            disabled = FastMCP('disabled-fire-bundle')
            context.register(disabled)
            self.assertIsNone(disabled._tool_manager.get_tool('assemble_fire_context_bundle'))

    async def test_snapshot_is_read_once_and_large_recovery_degrades(self):
        self.store.save(checkpoint(), {'e': 'original'}, 0)
        calls = []
        original_resume = FireStore.resume
        def resumed(store, *args, **kwargs):
            snapshot = original_resume(store, *args, **kwargs)
            calls.append(snapshot['revision'])
            # A concurrent correction after this read must not mix revision-2 originals into it.
            store.save(checkpoint('New revision'), {'e': 'new-original'}, 1,
                       correction={'origin': 'test', 'reason': 'concurrent update'})
            return snapshot
        value = request()
        value['context_capacity'] = 12000
        with mock.patch.object(FireStore, 'resume', resumed):
            bundle = await context.assemble_fire_context_bundle(value, expected_revision=1)
        self.assertEqual(calls, [1])
        self.assertEqual(bundle['fire_recovery']['revision'], 1)
        self.assertTrue(any(item['text'] == 'original' for item in bundle['selected']))
        self.assertFalse(any('New revision' in item['text'] for item in bundle['selected']))
        from test_context_bundle import evidence
        value['evidence'] = [evidence(str(i), 'source-' + str(i)) for i in range(100)]
        degraded = await context.assemble_fire_context_bundle(value)
        self.assertEqual(degraded['fire_recovery']['status'], 'snapshot-does-not-fit-contract')
        self.assertIn('payload', degraded)


if __name__ == '__main__':
    unittest.main()

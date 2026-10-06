"""Durable continuity acceptance; isolated private stores, no conversation/backend service."""
import asyncio
import hashlib
import tempfile
import unittest
from unittest import mock

from memory.fire_store import FireStore
from tools.brain import fire


def checkpoint(decision='Use published evidence'):
    return {'project_id': 'p', 'session_id': 's', 'task_id': 't', 'goal': 'Investigate the index',
            'accepted_constraints': ['Keep optional services optional'],
            'decisions': [{'text': decision, 'origin': 'user-confirmed'}],
            'unresolved_questions': ['Does the source change?'], 'next_actions': ['Read originals'],
            'evidence': [{'project_id': 'p', 'session_id': 's', 'source_kind': 'source', 'source_id': 'e'}]}


class DurableFire(unittest.TestCase):
    def test_conversation_removed_reopen_correction_and_original(self):
        with tempfile.TemporaryDirectory() as state:
            store = FireStore(state)
            original = 'def authenticate(token):\n    return token\n'
            saved = store.save(checkpoint('Wrong conclusion'), {'e': original}, 0)
            self.assertEqual(saved['revision'], 1)
            del store  # No chat history, original file or backend service is used by recovery.
            reopened = FireStore(state)
            resumed = reopened.resume('p', 's', 't')
            self.assertEqual(resumed['checkpoint']['goal'], 'Investigate the index')
            self.assertEqual(resumed['checkpoint']['accepted_constraints'], ['Keep optional services optional'])
            self.assertEqual(resumed['checkpoint']['evidence'][0]['freshness'], 'historical-unvalidated')
            self.assertEqual(reopened.resume('p', 's', 't', source_id='e')['original'], original)
            self.assertEqual(reopened.resume('other', 's', 't')['status'], 'not_found')
            self.assertEqual(reopened.resume('p', 'other', 't')['status'], 'not_found')
            self.assertEqual(reopened.resume('p', 's', 'other')['status'], 'not_found')
            correction = {'origin': 'source-review', 'reason': 'Original contradicts previous conclusion'}
            changed = reopened.save(checkpoint('Corrected conclusion'), {'e': original}, 1, correction=correction)
            self.assertEqual(changed['revision'], 2)
            current = reopened.resume('p', 's', 't', current_hashes={'e': hashlib.sha256(original.encode()).hexdigest()})
            self.assertEqual(current['checkpoint']['decisions'][0]['text'], 'Corrected conclusion')
            self.assertEqual(current['checkpoint']['evidence'][0]['freshness'], 'matches-supplied-current-hash')
            self.assertEqual(reopened.resume('p', 's', 't', current_hashes={'e': None})[
                'checkpoint']['evidence'][0]['freshness'], 'changed-or-deleted')
            self.assertEqual(reopened.save(checkpoint(), {'e': original}, 1, correction=correction)['status'], 'conflict')
            self.assertEqual(reopened.delete('p', 's', 't', 1)['status'], 'conflict')
            self.assertEqual(reopened.delete('p', 's', 't', 2)['revision'], 3)
            self.assertEqual(reopened.resume('p', 's', 't')['status'], 'not_found')
            self.assertEqual(reopened.save(checkpoint(), {}, 0)['status'], 'conflict')
            self.assertEqual(reopened.resume('p', 's', 't', source_id='e')['status'], 'not_found')

    def test_expiry_purge_missing_original_and_validation(self):
        with tempfile.TemporaryDirectory() as state:
            store = FireStore(state)
            with mock.patch('memory.fire_store.time.time', return_value=1000):
                store.save(checkpoint(), {}, 0, retention_seconds=60)
                self.assertEqual(store.resume('p', 's', 't', source_id='e')['status'], 'original_unavailable')
            with mock.patch('memory.fire_store.time.time', return_value=1061):
                self.assertEqual(store.resume('p', 's', 't')['status'], 'expired')
                self.assertEqual(store.purge_expired('p', 's', 't')['versions_deleted'], 1)
                self.assertEqual(store.resume('p', 's', 't')['revision'], 1)
            invalid = checkpoint()
            invalid['evidence'][0]['project_id'] = 'other'
            with self.assertRaises(ValueError):
                store.save(invalid, {}, 0)
            invalid = checkpoint()
            invalid['evidence'][0]['content_hash'] = 'wrong'
            with self.assertRaises(ValueError):
                store.save(invalid, {'e': 'original'}, 0)
            with self.assertRaises(ValueError):
                store.save(checkpoint(), {'e': 'x' * 128000}, 0)
            with self.assertRaises(ValueError):
                store.save(checkpoint(), {}, 1)
            with self.assertRaises(ValueError):
                store.resume('', 's', 't')

    def test_corrupt_original_refused(self):
        import json
        with tempfile.TemporaryDirectory() as state:
            store = FireStore(state)
            store.save(checkpoint(), {'e': 'original'}, 0)
            with store.connect() as db:
                row = db.execute('SELECT payload FROM snapshots').fetchone()
                payload = json.loads(row[0])
                payload['originals']['e'] = 'changed'
                db.execute('UPDATE snapshots SET payload=?', (json.dumps(payload),))
            with self.assertRaisesRegex(RuntimeError, 'hash mismatch'):
                store.resume('p', 's', 't')

    def test_paged_original_and_private_permissions(self):
        import os
        with tempfile.TemporaryDirectory() as state:
            store = FireStore(state)
            original = '😀 evidence\n' * 2000
            store.save(checkpoint(), {'e': original}, 0)
            collected = ''
            offset = 0
            while True:
                page = store.resume('p', 's', 't', source_id='e', offset=offset, max_chars=1000)
                collected += page['original']
                if page['next_offset'] is None:
                    break
                offset = page['next_offset']
            self.assertEqual(collected, original)
            self.assertEqual(os.stat(store.path).st_mode & 0o077, 0)
            os.chmod(state, 0o755)
            with self.assertRaises(ValueError):
                FireStore(state)

    def test_expired_correction_never_revives_older_decision(self):
        with tempfile.TemporaryDirectory() as state:
            store = FireStore(state)
            with mock.patch('memory.fire_store.time.time', return_value=1000):
                store.save(checkpoint('Obsolete'), {'e': 'original'}, 0, retention_seconds=2000)
                store.save(checkpoint('Corrected'), {'e': 'original'}, 1, retention_seconds=60,
                           correction={'origin': 'review', 'reason': 'fix'})
            with mock.patch('memory.fire_store.time.time', return_value=1061):
                self.assertEqual(store.resume('p', 's', 't')['status'], 'expired')
                self.assertEqual(store.purge_expired('p', 's', 't')['versions_deleted'], 1)
                self.assertEqual(store.resume('p', 's', 't')['status'], 'not_found')
                self.assertEqual(store.resume('p', 's', 't')['revision'], 2)


class FireTools(unittest.IsolatedAsyncioTestCase):
    async def test_opt_in_dispatch_and_outage(self):
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP('fire-test')
        with mock.patch.dict('os.environ', {'LM_PROXY_FIRE_STATE': ''}):
            fire.register(mcp)
            self.assertIsNone(mcp._tool_manager.get_tool('resume_fire_checkpoint'))
            self.assertEqual((await fire.resume_fire_checkpoint('p', 's', 't'))['status'], 'disabled')
        with tempfile.TemporaryDirectory() as state, mock.patch.dict('os.environ', {'LM_PROXY_FIRE_STATE': state}):
            fire.register(mcp)
            saved = await mcp._tool_manager.get_tool('save_fire_checkpoint').run({
                'checkpoint': checkpoint(), 'originals': {'e': 'original'}, 'expected_revision': 0})
            self.assertEqual(saved['status'], 'stored')
            self.assertEqual((await fire.resume_fire_checkpoint('p', 's', 't'))['status'], 'resumed')
            with mock.patch('tools.brain.fire.FireStore', side_effect=OSError('sensitive fixture')):
                failed = await fire.resume_fire_checkpoint('p', 's', 't')
            self.assertEqual(failed['status'], 'storage-unavailable')
            self.assertNotIn('sensitive', str(failed))
            # Concurrent writers must not both advance the same revision.
            correction = {'origin': 'test', 'reason': 'update'}
            results = await asyncio.gather(*[fire.save_fire_checkpoint(checkpoint(), {'e': 'original'}, 1,
                correction=correction) for _ in range(2)])
            self.assertEqual(sorted(r['status'] for r in results), ['conflict', 'stored'])


if __name__ == '__main__':
    unittest.main()

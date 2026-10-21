"""Offline task Cockpit acceptance with disposable private SQLite and ASGI.

Run .venv/bin/python -W error::ResourceWarning test_task_cockpit_http.py.
No personal state, listeners, models, external services or shared daemons are used.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx

from graphrag_core.task_cockpit.reads import MAX_PAYLOAD_BYTES, TaskStatusIntegrityError, TaskStatusReader
from graphrag_core.task_cockpit.server import create_app
from memory.task_registry import TaskRegistry


HOST = '127.0.0.1:8783'
BASE = 'http://' + HOST
TOKEN = 'synthetic-cockpit-capability-0123456789abcdef'
AUTHORIZATION = ('authorization', 'Bearer ' + TOKEN)


class TaskCockpitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'state'
        self.registry = TaskRegistry(str(self.state))
        self.task = self.registry.create('fixture', str(self.root), 'private-goal', [], ['read_source'])
        self.task = self.registry.claim('fixture', self.task['id'], 1, 'private-worker')
        self.task = self.registry.checkpoint('fixture', self.task['id'], self.task['revision'],
                                             self.task['claim']['token'], {'source': 'private-source'})
        self.app = create_app(str(self.state), 'fixture', self.task['id'], TOKEN, HOST)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=('127.0.0.1', 54321),
                                         raise_app_exceptions=False), base_url=BASE,
        )
        self.addAsyncCleanup(self.client.aclose)

    async def request(self, path='/api/v1/task-status', *, authorized=True, headers=(), method='GET', **kwargs):
        values = [AUTHORIZATION] if authorized else []
        return await self.client.request(method, path, headers=[*values, *headers], **kwargs)

    def assert_private(self, response):
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        self.assertEqual(response.headers.get('x-content-type-options'), 'nosniff')
        self.assertEqual(response.headers.get('referrer-policy'), 'no-referrer')
        self.assertEqual(response.headers.get('x-frame-options'), 'DENY')
        self.assertIn("connect-src 'self'", response.headers.get('content-security-policy', ''))
        self.assertNotIn('access-control-allow-origin', response.headers)
        for value in (TOKEN, str(self.root), 'private-goal', 'private-worker', 'private-source',
                      self.task['claim']['token'], 'Traceback'):
            self.assertNotIn(value, response.text)

    def write_payload(self, payload):
        with self.registry.connect() as db:
            db.execute('UPDATE tasks SET payload=? WHERE project=? AND id=?',
                       (payload, 'fixture', self.task['id']))

    def snapshot(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.state.iterdir()}

    async def test_status_reuses_inspect_without_writes_or_private_fields(self):
        before = self.snapshot()
        response = await self.request()
        self.assertEqual(response.status_code, 200, response.text)
        expected = {'schema_version': 1, 'read_only': True,
                    **self.registry.inspect('fixture', self.task['id'])}
        expected.setdefault('human_review_required', None)
        expected.setdefault('latest_review_actor_type', 'unspecified')
        self.assertEqual(response.json(), expected)
        self.assertTrue(response.json()['checkpoint_present'])
        self.assertFalse(response.json()['failure_history_complete'])
        self.assertIn('absence is not success', response.json()['note'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.registry.get('fixture', self.task['id']), self.task)
        self.assert_private(response)

    async def test_capability_required_exactly_once_and_never_from_query(self):
        for headers in ([], [('authorization', 'Bearer wrong')], [AUTHORIZATION, AUTHORIZATION]):
            response = await self.request(authorized=False, headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {'error': 'local-capability-required'})
            self.assert_private(response)
        response = await self.request('/api/v1/task-status?token=' + TOKEN, authorized=False)
        self.assertEqual(response.status_code, 401)
        self.assert_private(response)

    async def test_single_server_scope_cannot_be_overridden(self):
        other = self.registry.create('other', str(self.root), 'other-private-goal', [], ['read_source'])
        for suffix in ('?project=other', '?task_id=' + other['id'], '?project=fixture&project=other',
                       '?path=/private', '?', '?x=' + 'a' * 9000):
            if suffix == '?':
                continue  # An empty HTTP query has no query string bytes to reject.
            response = await self.request('/api/v1/task-status' + suffix)
            self.assertEqual(response.status_code, 400)
            self.assert_private(response)
        response = await self.request('/api/v1/tasks/' + other['id'])
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(other['id'], response.text)

    async def test_host_duplicates_and_alternatives_are_rejected(self):
        for headers in ([('host', 'localhost:8783')], [('host', '127.0.0.1')],
                        [('host', HOST), ('host', HOST)], [('host', HOST + '.attacker.invalid')]):
            response = await self.request(headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {'error': 'local-host-required'})
            self.assert_private(response)

    async def test_browser_exact_same_origin_and_native_bearer_reads_work(self):
        for headers in ([], [('origin', BASE)]):
            response = await self.request(headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assert_private(response)
        for origin in ('https://attacker.invalid', 'null', '', 'http://localhost:8783'):
            response = await self.request(headers=[('origin', origin)])
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {'error': 'same-origin-required'})
            self.assert_private(response)
        response = await self.request(headers=[('origin', BASE), ('origin', BASE)])
        self.assertEqual(response.status_code, 403)

    async def test_remote_peer_cannot_spoof_loopback(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=('192.0.2.8', 54321)), base_url=BASE,
        ) as client:
            response = await client.get('/api/v1/task-status', headers=[
                AUTHORIZATION, ('x-forwarded-for', '127.0.0.1'), ('forwarded', 'for=127.0.0.1'),
            ])
        self.assertEqual(response.status_code, 403)
        self.assert_private(response)

    async def test_mutations_and_get_bodies_rejected_without_changes(self):
        before = self.snapshot()
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'):
            response = await self.request(method=method)
            self.assertEqual(response.status_code, 405, method)
        for kwargs in ({'content': b'{}'}, {'headers': [('content-encoding', 'gzip')]}):
            response = await self.request(**kwargs)
            self.assertEqual(response.status_code, 400)
            self.assert_private(response)
        self.assertEqual(self.snapshot(), before)

    async def test_public_live_and_static_shell_have_no_task_metadata(self):
        response = await self.request('/live', authorized=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'surface': 'local-task-cockpit', 'read_only': True})
        self.assert_private(response)
        static = self.root / 'synthetic-static'
        static.mkdir()
        for name in ('index.html', 'app.js', 'style.css'):
            (static / name).write_text('synthetic-public-shell', encoding='utf-8')
        with patch('graphrag_core.task_cockpit.server.STATIC', static):
            for path in ('/', '/app.js', '/style.css'):
                response = await self.request(path, authorized=False)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.text, 'synthetic-public-shell')
                self.assert_private(response)
                self.assertNotIn(self.task['id'], response.text)
                self.assertNotIn('fixture', response.text)

    async def test_expiry_and_failure_metadata_remain_bounded(self):
        task = self.registry.record_failure('fixture', self.task['id'], self.task['revision'],
                                            self.task['claim']['token'], 'timeout')
        with patch('memory.task_registry.time.time', return_value=self.task['claim']['expires_at'] + 1):
            response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['claim_expired'])
        self.assertEqual(response.json()['failures'], task['failures'])
        self.assertLess(len(response.content), 8192)
        self.assert_private(response)

    async def test_preparation_failure_is_visible_without_exposing_source(self):
        task = self.registry.record_failure('fixture', self.task['id'], self.task['revision'],
                                           self.task['claim']['token'], 'preparation_error')
        response = await self.request()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['failures'], task['failures'])
        self.assertEqual(response.json()['failures'][0]['stage'], 'preparation')
        self.assert_private(response)

    async def test_mismatched_failure_stage_is_rejected(self):
        task = self.registry.record_failure('fixture', self.task['id'], self.task['revision'],
                                           self.task['claim']['token'], 'preparation_error')
        task['failures'][0]['stage'] = 'generation'
        self.write_payload(json.dumps(task))
        response = await self.request()
        self.assertEqual(response.status_code, 409)
        self.assert_private(response)

    async def test_writer_update_visible_without_reopening_application(self):
        original = (await self.request()).json()
        updated = self.registry.cancel('fixture', self.task['id'], self.task['revision'], 'private-reason')
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'cancelled')
        self.assertEqual(response.json()['revision'], original['revision'] + 1)
        self.assertEqual(response.json()['revision'], updated['revision'])
        self.assertNotIn('private-reason', response.text)

    async def test_older_inspect_contract_reports_unknown_review_policy(self):
        inspect = TaskRegistry.inspect

        def legacy(registry, project, task_id):
            result = inspect(registry, project, task_id)
            result.pop('human_review_required', None)
            result.pop('latest_review_actor_type', None)
            return result

        with patch.object(TaskRegistry, 'inspect', legacy):
            response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['human_review_required'])
        self.assertEqual(response.json()['latest_review_actor_type'], 'unspecified')

    async def test_missing_task_is_sanitized_and_does_not_enumerate(self):
        app = create_app(str(self.state), 'other', self.task['id'], TOKEN, HOST)
        before = self.snapshot()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 54321)), base_url=BASE,
        ) as client:
            response = await client.get('/api/v1/task-status', headers=[AUTHORIZATION])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'error': 'task-not-found'})
        self.assertEqual(self.snapshot(), before)
        self.assert_private(response)

    async def test_private_extra_payload_fields_are_never_echoed(self):
        task = self.registry.get('fixture', self.task['id'])
        task['private-diagnostic'] = 'private-provider-body'
        task['submissions'] = [{'finding': {'answer': 'private-finding', 'source': 'private-evidence'}}]
        self.write_payload(json.dumps(task))
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['submission_count'], 1)
        for value in ('private-provider-body', 'private-finding', 'private-evidence'):
            self.assertNotIn(value, response.text)
        self.assert_private(response)

    async def test_malformed_metadata_and_classifications_fail_closed(self):
        for field, value in (('status', 'private-malicious-value'), ('status', {}),
                             ('revision', True), ('attempts', 6), ('human_review_required', 'private'),
                             ('claim', {'expires_at': True}), ('claim', 'private-claim')):
            with self.subTest(field=field, value=value):
                task = dict(self.task, **{field: value})
                self.write_payload(json.dumps(task))
                response = await self.request()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {'error': 'task-status-integrity-check-failed'})
                self.assert_private(response)
                self.assertNotIn('private-malicious-value', response.text)
        task = dict(self.task)
        del task['status']
        self.write_payload(json.dumps(task))
        self.assertEqual((await self.request()).status_code, 409)

    async def test_oversize_invalid_json_and_identity_mismatch_are_sanitized(self):
        for payload in ('x' * (MAX_PAYLOAD_BYTES + 1), '{private-invalid-json',
                        json.dumps(dict(self.task, id='private-other-identity')),
                        json.dumps(self.task)[:-1] + ',"status":"queued"}',
                        json.dumps(dict(self.task, private=float('nan')))):
            with self.subTest(kind=len(payload)):
                self.write_payload(payload)
                response = await self.request()
                self.assertEqual(response.status_code, 409)
                self.assert_private(response)
                self.assertNotIn('private-invalid-json', response.text)

    async def test_database_failure_does_not_echo_exception(self):
        with patch.object(self.app.state.reader, 'status', side_effect=sqlite3.OperationalError('private-db-error')):
            response = await self.request()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'error': 'task-status-unavailable'})
        self.assertNotIn('private-db-error', response.text)
        self.assert_private(response)

    async def test_readonly_connection_rejects_writes(self):
        before = self.snapshot()
        with self.app.state.reader.connect() as db:
            self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM tasks')
        self.assertEqual(self.snapshot(), before)

    async def test_missing_state_or_database_never_created(self):
        absent = self.root / 'absent'
        with self.assertRaises(FileNotFoundError):
            create_app(str(absent), 'fixture', self.task['id'], TOKEN, HOST)
        self.assertFalse(absent.exists())
        empty = self.root / 'empty'
        empty.mkdir(mode=0o700)
        with self.assertRaises(FileNotFoundError):
            create_app(str(empty), 'fixture', self.task['id'], TOKEN, HOST)
        self.assertEqual(list(empty.iterdir()), [])

    async def test_configuration_rejects_weak_tokens_and_nonlocal_host(self):
        for token in ('short', 'a' * 257, 'a' * 31 + '\n', 'x' * 31 + 'é'):
            with self.subTest(token_length=len(token)), self.assertRaises(ValueError):
                create_app(str(self.state), 'fixture', self.task['id'], token, HOST)
        for host in ('0.0.0.0:8783', 'example.invalid:8783', '127.0.0.1', '127.0.0.1:0',
                     '127.0.0.1:65536', None):
            with self.subTest(host=host), self.assertRaises(ValueError):
                create_app(str(self.state), 'fixture', self.task['id'], TOKEN, host)

    async def test_private_modes_and_symlink_or_hardlink_state_are_rejected(self):
        os.chmod(self.state, 0o755)
        with self.assertRaises(TaskStatusIntegrityError):
            TaskStatusReader(str(self.state), 'fixture', self.task['id'])
        os.chmod(self.state, 0o700)
        os.chmod(self.registry.path, 0o644)
        with self.assertRaises(TaskStatusIntegrityError):
            TaskStatusReader(str(self.state), 'fixture', self.task['id'])
        os.chmod(self.registry.path, 0o600)
        alias = self.root / 'state-alias'
        alias.symlink_to(self.state, target_is_directory=True)
        with self.assertRaises(TaskStatusIntegrityError):
            TaskStatusReader(str(alias), 'fixture', self.task['id'])
        hardlink = self.root / 'registry-alias'
        os.link(self.registry.path, hardlink)
        with self.assertRaises(TaskStatusIntegrityError):
            TaskStatusReader(str(self.state), 'fixture', self.task['id'])
        hardlink.unlink()
        with patch('graphrag_core.task_cockpit.reads.os.getuid', return_value=os.getuid() + 1):
            with self.assertRaises(TaskStatusIntegrityError):
                TaskStatusReader(str(self.state), 'fixture', self.task['id'])
        backup = self.state / 'saved.sqlite3'
        self.registry.path.rename(backup)
        self.registry.path.symlink_to(backup)
        with self.assertRaises(TaskStatusIntegrityError):
            TaskStatusReader(str(self.state), 'fixture', self.task['id'])

    async def test_registry_replacement_and_wal_state_are_refused_without_creating_files(self):
        backup = self.state / 'saved.sqlite3'
        self.registry.path.rename(backup)
        self.registry.path.write_bytes(backup.read_bytes())
        self.registry.path.chmod(0o600)
        response = await self.request()
        self.assertEqual(response.status_code, 409)
        self.assert_private(response)
        # Restore original inode before explicitly creating a WAL fixture.
        self.registry.path.unlink()
        backup.rename(self.registry.path)
        with closing(sqlite3.connect(self.registry.path)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        before = self.snapshot()
        self.assertFalse((self.state / 'tasks.sqlite3-shm').exists())
        response = await self.request()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.state / 'tasks.sqlite3-shm').exists())
        with self.assertRaises(TaskStatusIntegrityError):
            TaskStatusReader(str(self.state), 'fixture', self.task['id'])

    async def test_existing_wal_sidecars_are_not_opened_or_modified(self):
        sidecar = self.state / 'tasks.sqlite3-wal'
        sidecar.write_bytes(b'private-sidecar')
        before = self.snapshot()
        response = await self.request()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn('private-sidecar', response.text)


if __name__ == '__main__':
    unittest.main()

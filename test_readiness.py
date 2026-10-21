"""Offline readiness contracts and mocked temporary resources; no dotenv/services used."""
import asyncio
import json
import os
import sys
import types
import unittest
from unittest import mock

import anyio

from graphrag_core.readiness import (
    ProbeFailure, ReadinessMonitor, probe_embedded, probe_inference, probe_neo4j,
    probe_postgres, probe_redis,
)


def monitor(required='', **values):
    return ReadinessMonitor(environ=dict({'LM_PROXY_READY_REQUIRED': required,
        'LM_PROXY_READY_TIMEOUT_SECONDS': '0.1', 'LM_PROXY_PG_DSN': 'postgresql://user:private@host/db',
        'LM_PROXY_NEO4J_DB': 'selected', 'LM_PROXY_NEO4J_PASSWORD': 'private'}, **values))


class ReadinessContracts(unittest.IsolatedAsyncioTestCase):
    async def test_transport_lifespan_default_policy_and_explicit_override(self):
        called = mock.AsyncMock(side_effect=AssertionError('unrequired probe called'))
        instance = ReadinessMonitor(environ={}, probes={'inference': called})
        code, body = await instance.check()
        self.assertEqual(code, 503)
        self.assertFalse(body['ready'])
        instance.activate()
        code, body = await instance.check()
        self.assertEqual((code, body['scope'], body['required']), (200, 'transport', []))
        called.assert_not_called()
        await instance.close()
        self.assertEqual((await instance.check())[0], 503)
        proxy_default = ReadinessMonitor(default_required=('inference',), environ={})
        self.assertEqual(proxy_default.required, ('inference',))
        explicit = ReadinessMonitor(default_required=('inference',), environ={'LM_PROXY_READY_REQUIRED': ''})
        self.assertEqual(explicit.required, ())

    async def test_config_errors_sanitize_raw_tokens_and_do_not_probe(self):
        called = mock.AsyncMock(side_effect=AssertionError('invalid policy must not probe'))
        for required, timeout, reason in (
                ('neo4j,private://user:secret@host', '0.1', 'unsupported_dependency'),
                ('neo4j,,redis', '0.1', 'unsupported_dependency'),
                ('neo4j', 'nan', 'invalid_timeout'), ('neo4j', 'inf', 'invalid_timeout'),
                ('neo4j', '0.01', 'invalid_timeout'), ('neo4j', '5.1', 'invalid_timeout'),
                ('neo4j', 'secret', 'invalid_timeout')):
            with self.subTest(required=required, timeout=timeout):
                instance = ReadinessMonitor(environ={'LM_PROXY_READY_REQUIRED': required,
                    'LM_PROXY_READY_TIMEOUT_SECONDS': timeout}, probes={'neo4j': called, 'redis': called})
                instance.activate()
                code, body = await instance.check()
                self.assertEqual(code, 503)
                self.assertEqual(body['config_error'], {'reason': reason})
                self.assertNotIn('secret', json.dumps(body))
                self.assertNotIn('private://', json.dumps(body))
                await instance.close()
        called.assert_not_called()

    async def test_policy_and_connection_settings_are_captured_once(self):
        environment = {'LM_PROXY_READY_REQUIRED': ' neo4j, Redis,neo4j ',
                       'LM_PROXY_READY_TIMEOUT_SECONDS': '5', 'LM_PROXY_NEO4J_DB': 'captured'}
        instance = ReadinessMonitor(environ=environment)
        environment.update(LM_PROXY_READY_REQUIRED='postgres', LM_PROXY_READY_TIMEOUT_SECONDS='0.1',
                           LM_PROXY_NEO4J_DB='changed')
        self.assertEqual(instance.required, ('neo4j', 'redis'))
        self.assertEqual(instance.timeout, 5)
        self.assertEqual(instance._settings.neo4j_database, 'captured')
        self.assertNotIn('captured', repr(instance._settings))
        with mock.patch.dict(os.environ, {'LM_PROXY_READY_REQUIRED': 'neo4j'}):
            captured = ReadinessMonitor()
        self.assertEqual(captured.required, ('neo4j',))

    async def test_inference_configuration_matches_proxy_environment_and_is_frozen(self):
        environment = {'LM_PROXY_PROVIDER': 'lmstudio', 'LM_BASE': 'http://selected-local.invalid/',
                       'LMSTUDIO_BASE_URL': 'http://unused-embedding.invalid',
                       'LMSTUDIO_API_KEY': 'unused-local-key'}
        local = ReadinessMonitor(environ=environment)
        self.assertEqual(local._settings.inference_base, 'http://selected-local.invalid/v1')
        self.assertEqual(local._settings.inference_key, '')
        environment.update(LM_PROXY_PROVIDER='openai',
            LM_PROXY_OPENAI_BASE_URL='https://selected-cloud.invalid/v1/', OPENAI_API_KEY='captured-cloud-key',
            LM_PROXY_OPENAI_API_KEY='unused-alternate-key')
        cloud = ReadinessMonitor(environ=environment)
        environment.update(OPENAI_API_KEY='changed-key', LM_PROXY_OPENAI_BASE_URL='https://changed.invalid')
        self.assertEqual(cloud._settings.inference_base, 'https://selected-cloud.invalid/v1')
        self.assertEqual(cloud._settings.inference_key, 'captured-cloud-key')
        self.assertEqual(local._settings.inference_provider, 'lmstudio')
        self.assertNotIn('cloud-key', repr(cloud._settings))

    async def test_required_probes_start_concurrently_with_per_probe_timeouts(self):
        started = set()
        all_started = asyncio.Event()
        cancelled = set()

        def probe(name):
            async def run():
                started.add(name)
                if len(started) == 3:
                    all_started.set()
                if name == 'redis':
                    return
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.add(name)
            return run

        instance = ReadinessMonitor(environ={'LM_PROXY_READY_REQUIRED': 'neo4j,postgres,redis',
            'LM_PROXY_READY_TIMEOUT_SECONDS': '0.1'}, probes={name: probe(name) for name in (
                'neo4j', 'postgres', 'redis')})
        instance.activate()
        started_at = asyncio.get_running_loop().time()
        task = asyncio.create_task(instance.check())
        await asyncio.wait_for(all_started.wait(), 0.2)
        code, body = await task
        self.assertLess(asyncio.get_running_loop().time() - started_at, 0.25)
        self.assertEqual(code, 503)
        self.assertEqual(body['dependencies']['redis']['status'], 'ready')
        self.assertEqual({body['dependencies'][name]['reason'] for name in cancelled}, {'timeout'})
        self.assertEqual(cancelled, {'neo4j', 'postgres'})
        await instance.close()

    async def test_failures_redact_exception_details_and_cache_copies(self):
        async def failed():
            raise RuntimeError('postgresql://alice:secret@internal/private/source.py')

        failures = mock.AsyncMock(side_effect=failed)
        good = mock.AsyncMock()
        instance = ReadinessMonitor(environ={'LM_PROXY_READY_REQUIRED': 'postgres,redis'},
                                   probes={'postgres': failures, 'redis': good})
        instance.activate()
        code, first = await instance.check()
        self.assertEqual(code, 503)
        self.assertEqual(first['dependencies']['postgres']['reason'], 'probe_failed')
        self.assertNotIn('alice', json.dumps(first))
        self.assertNotIn('secret', json.dumps(first))
        self.assertNotIn('source.py', json.dumps(first))
        first['dependencies']['redis']['status'] = 'mutated'
        self.assertEqual((await instance.check())[1]['dependencies']['redis']['status'], 'ready')
        self.assertEqual(failures.await_count, 1)
        await instance.close()

    async def test_request_cancellation_does_not_cancel_shared_single_flight(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def probe():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()

        instance = ReadinessMonitor(environ={'LM_PROXY_READY_REQUIRED': 'redis'}, probes={'redis': probe})
        instance.activate()
        first = asyncio.create_task(instance.check())
        await started.wait()
        second = asyncio.create_task(instance.check())
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertEqual((await second)[0], 200)
        self.assertEqual(calls, 1)
        await instance.close()

    async def test_shutdown_marks_inactive_and_drains_cancelled_probe(self):
        started, closed = asyncio.Event(), asyncio.Event()

        async def probe():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                closed.set()

        instance = ReadinessMonitor(environ={'LM_PROXY_READY_REQUIRED': 'redis'}, probes={'redis': probe})
        instance.activate()
        waiting = asyncio.create_task(instance.check())
        await started.wait()
        await instance.close()
        self.assertTrue(closed.is_set())
        self.assertFalse(instance.transport_active)
        self.assertEqual((await instance.check())[0], 503)
        with self.assertRaises(asyncio.CancelledError):
            await waiting


class TemporaryResources(unittest.IsolatedAsyncioTestCase):
    async def test_neo4j_selects_database_managed_read_and_closes_on_success(self):
        import neo4j

        transaction = types.SimpleNamespace(run=mock.AsyncMock(return_value=types.SimpleNamespace(
            single=mock.AsyncMock(return_value={'ready': 1}))))
        session = types.SimpleNamespace(close=mock.AsyncMock())

        async def execute_read(callback):
            self.assertEqual(callback.timeout, 0.1)
            await callback(transaction)

        session.execute_read = mock.AsyncMock(side_effect=execute_read)
        driver = types.SimpleNamespace(session=mock.Mock(return_value=session), close=mock.AsyncMock())
        with mock.patch.object(neo4j.AsyncGraphDatabase, 'driver', return_value=driver) as factory:
            await probe_neo4j(monitor()._settings)
        driver.session.assert_called_once_with(database='selected', default_access_mode=neo4j.READ_ACCESS)
        transaction.run.assert_awaited_once_with('RETURN 1 AS ready')
        self.assertEqual(factory.call_args.kwargs['max_transaction_retry_time'], 0)
        session.close.assert_awaited_once()
        driver.close.assert_awaited_once()

    async def test_neo4j_timeout_shields_delayed_session_and_driver_cleanup(self):
        import neo4j

        flags = []
        async def cleanup(name):
            await asyncio.sleep(0.01)
            flags.append(name)
        async def blocked(callback):
            await asyncio.Event().wait()
        session = types.SimpleNamespace(execute_read=blocked, close=lambda: cleanup('session'))
        driver = types.SimpleNamespace(session=mock.Mock(return_value=session), close=lambda: cleanup('driver'))
        instance = monitor('neo4j')
        instance.activate()
        with mock.patch.object(neo4j.AsyncGraphDatabase, 'driver', return_value=driver):
            code, body = await instance.check()
        self.assertEqual(code, 503)
        self.assertEqual(body['dependencies']['neo4j']['reason'], 'timeout')
        self.assertEqual(flags, ['session', 'driver'])
        await instance.close()

    async def test_cancelling_shutdown_still_drains_owned_temporary_resources(self):
        import neo4j

        query_started, close_started, release_close = asyncio.Event(), asyncio.Event(), asyncio.Event()
        flags = []
        async def blocked(callback):
            query_started.set()
            await asyncio.Event().wait()
        async def close_session():
            close_started.set()
            await release_close.wait()
            flags.append('session')
        async def close_driver():
            flags.append('driver')
        session = types.SimpleNamespace(execute_read=blocked, close=close_session)
        driver = types.SimpleNamespace(session=mock.Mock(return_value=session), close=close_driver)
        instance = monitor('neo4j', LM_PROXY_READY_TIMEOUT_SECONDS='2')
        instance.activate()
        with mock.patch.object(neo4j.AsyncGraphDatabase, 'driver', return_value=driver):
            request = asyncio.create_task(instance.check())
            await query_started.wait()
            closing = asyncio.create_task(instance.close())
            await close_started.wait()
            closing.cancel()
            release_close.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            with self.assertRaises(asyncio.CancelledError):
                await request
        self.assertEqual(flags, ['session', 'driver'])
        self.assertEqual((await instance.check())[0], 503)

    async def test_anyio_cancel_scope_does_not_interrupt_resource_cleanup(self):
        import neo4j

        flags = []
        async def blocked(callback):
            await anyio.sleep_forever()
        async def cleanup(name):
            await anyio.sleep(0.01)
            flags.append(name)
        session = types.SimpleNamespace(execute_read=blocked, close=lambda: cleanup('session'))
        driver = types.SimpleNamespace(session=mock.Mock(return_value=session), close=lambda: cleanup('driver'))
        with mock.patch.object(neo4j.AsyncGraphDatabase, 'driver', return_value=driver):
            with anyio.CancelScope() as scope:
                scope.cancel()
                await probe_neo4j(monitor()._settings)
        self.assertEqual(flags, ['session', 'driver'])

    async def test_postgres_autocommit_query_and_cursor_connection_cleanup(self):
        import psycopg

        cursor = types.SimpleNamespace(fetchone=mock.AsyncMock(return_value=(1,)), close=mock.AsyncMock())
        connection = types.SimpleNamespace(execute=mock.AsyncMock(return_value=cursor), close=mock.AsyncMock())
        instance = monitor()
        with mock.patch.object(psycopg.AsyncConnection, 'connect', mock.AsyncMock(return_value=connection)) as connect:
            await probe_postgres(instance._settings)
        connect.assert_awaited_once_with(instance._settings.postgres_dsn, autocommit=True, connect_timeout=1)
        connection.execute.assert_awaited_once_with('SELECT 1 AS ready')
        cursor.close.assert_awaited_once()
        connection.close.assert_awaited_once()
        connection.execute.side_effect = RuntimeError('private credentials')
        with mock.patch.object(psycopg.AsyncConnection, 'connect', mock.AsyncMock(return_value=connection)):
            with self.assertRaises(RuntimeError):
                await probe_postgres(instance._settings)
        self.assertEqual(connection.close.await_count, 2)

    async def test_redis_ping_and_pool_cleanup_even_after_failure(self):
        from redis.asyncio import Redis

        client = types.SimpleNamespace(ping=mock.AsyncMock(return_value=True), aclose=mock.AsyncMock())
        with mock.patch.object(Redis, 'from_url', return_value=client):
            await probe_redis(monitor()._settings)
            client.ping.side_effect = RuntimeError('redis://private-host/password')
            with self.assertRaises(RuntimeError):
                await probe_redis(monitor()._settings)
        self.assertEqual(client.aclose.await_count, 2)
        client.aclose.assert_awaited_with(close_connection_pool=True)

    async def test_cleanup_failure_is_sanitized(self):
        from redis.asyncio import Redis

        client = types.SimpleNamespace(ping=mock.AsyncMock(return_value=True),
            aclose=mock.AsyncMock(side_effect=RuntimeError('secret cleanup details')))
        instance = monitor('redis')
        instance.activate()
        with mock.patch.object(Redis, 'from_url', return_value=client):
            code, body = await instance.check()
        self.assertEqual(code, 503)
        self.assertEqual(body['dependencies']['redis']['reason'], 'cleanup_failed')
        self.assertNotIn('secret', json.dumps(body))
        await instance.close()

    async def test_missing_optional_package_is_dependency_failure(self):
        instance = monitor('postgres')
        instance.activate()
        with mock.patch.dict(sys.modules, {'psycopg': None}):
            code, body = await instance.check()
        self.assertEqual(code, 503)
        self.assertEqual(body['dependencies']['postgres']['reason'], 'missing_package')
        await instance.close()

    async def test_embedded_never_initializes_and_uses_existing_owner_locks(self):
        instance = monitor()
        with mock.patch.dict(sys.modules, {'memory.embedded_runtime': None}):
            with self.assertRaises(ProbeFailure) as error:
                await probe_embedded(instance._settings)
        self.assertEqual(error.exception.reason, 'not_initialized')
        runtime = types.SimpleNamespace(_lock=asyncio.Lock(), _closed=False, _owner=None)
        module = types.SimpleNamespace(_runtime=runtime, get_embedded_runtime=mock.Mock(
            side_effect=AssertionError('initialization forbidden')))
        with mock.patch.dict(sys.modules, {'memory.embedded_runtime': module}):
            with self.assertRaises(ProbeFailure) as error:
                await probe_embedded(instance._settings)
        self.assertEqual(error.exception.reason, 'not_initialized')
        query = mock.AsyncMock(return_value=types.SimpleNamespace(data=mock.AsyncMock(return_value=[{'ready': 1}])))
        session = types.SimpleNamespace(_connection=types.SimpleNamespace(set_query_timeout=mock.Mock()),
            __aenter__=mock.AsyncMock(), __aexit__=mock.AsyncMock())
        owner = types.SimpleNamespace(_lock=asyncio.Lock(), vectors=types.SimpleNamespace(_closed=False))
        graph = types.SimpleNamespace(_closed=False, session=mock.Mock(return_value=session))
        owner.graph = graph
        runtime._owner = owner

        async def execute_read(callback):
            self.assertTrue(runtime._lock.locked())
            self.assertTrue(owner._lock.locked())
            await callback(types.SimpleNamespace(run=query))

        session.execute_read = execute_read
        with mock.patch.dict(sys.modules, {'memory.embedded_runtime': module}):
            await probe_embedded(instance._settings)
        query.assert_awaited_once_with('RETURN 1 AS ready')
        session._connection.set_query_timeout.assert_called_once_with(100)
        session.__aexit__.assert_awaited_once_with(None, None, None)
        module.get_embedded_runtime.assert_not_called()
        self.assertIs(runtime._owner, owner)
        self.assertFalse(runtime._lock.locked())
        self.assertFalse(owner._lock.locked())
        owner.vectors._closed = True
        with mock.patch.dict(sys.modules, {'memory.embedded_runtime': module}):
            with self.assertRaises(ProbeFailure) as error:
                await probe_embedded(instance._settings)
        self.assertEqual(error.exception.reason, 'owner_closed')

    async def test_inference_uses_only_selected_read_catalog_and_does_not_expose_ids(self):
        import httpx

        requests = []
        clients = []
        client_type = httpx.AsyncClient

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={'data': [{'id': '/private/model/file.gguf'}]})

        def client_factory(**kwargs):
            self.assertFalse(kwargs['follow_redirects'])
            self.assertTrue(kwargs['trust_env'])
            client = client_type(transport=httpx.MockTransport(respond), **kwargs)
            clients.append(client)
            return client

        instance = monitor('inference', LM_PROXY_PROVIDER='openai', OPENAI_API_KEY='private-key',
                           LM_PROXY_OPENAI_BASE_URL='https://selected.invalid/v1')
        instance.activate()
        with mock.patch.object(httpx, 'AsyncClient', side_effect=client_factory):
            code, body = await instance.check()
        self.assertEqual(code, 200)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, 'GET')
        self.assertEqual(str(requests[0].url), 'https://selected.invalid/v1/models')
        self.assertEqual(requests[0].headers['Authorization'], 'Bearer private-key')
        self.assertTrue(clients[0].is_closed)
        for secret in ('private-key', 'selected.invalid', 'file.gguf'):
            self.assertNotIn(secret, json.dumps(body))
        self.assertEqual(body['dependencies']['inference']['check'], 'model_catalog_read')
        await instance.close()

    async def test_inference_error_catalog_bounds_and_empty_catalog_semantics(self):
        import httpx

        client_type = httpx.AsyncClient
        for response, expected in (
                (httpx.Response(200, json={'data': []}), 200),
                (httpx.Response(302, headers={'Location': 'https://private.invalid/other'}), 503),
                (httpx.Response(200, json={'data': []}, headers={'Content-Length': '1'}), 200),
                (httpx.Response(200, content=b'x' * 65537), 503),
                (httpx.Response(200, json={'unexpected': []}), 503)):
            with self.subTest(expected=expected):
                instance = monitor('inference')
                instance.activate()
                with mock.patch.object(httpx, 'AsyncClient', side_effect=lambda **kwargs: client_type(
                        transport=httpx.MockTransport(lambda request: response), **kwargs)):
                    code, body = await instance.check()
                self.assertEqual(code, expected)
                self.assertNotIn('private.invalid', json.dumps(body))
                await instance.close()
        configured = monitor(LM_PROXY_PROVIDER='openai')
        with self.assertRaises(ProbeFailure) as error:
            await probe_inference(configured._settings)
        self.assertEqual(error.exception.reason, 'not_configured')


if __name__ == '__main__':
    unittest.main()

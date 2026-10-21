"""Per-lifespan readiness for explicitly declared, read-only dependency checks.

No probe bootstraps schemas, initializes embedded owners, loads models or imports
application storage/configuration modules. Responses contain only fixed status,
scope and reason values; connection settings and exception text remain private.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
import os
import sys
from urllib.parse import urlsplit

import anyio


DEPENDENCIES = ('embedded', 'inference', 'neo4j', 'postgres', 'redis')
REASONS = frozenset({'not_configured', 'not_initialized', 'owner_closed', 'missing_package',
                     'unsupported_provider', 'invalid_endpoint', 'invalid_response', 'http_error',
                     'cleanup_failed', 'probe_failed', 'timeout'})
CHECKS = {'embedded': 'existing_owner_graph_read', 'inference': 'model_catalog_read',
          'neo4j': 'read_query', 'postgres': 'read_query', 'redis': 'ping'}
Probe = Callable[[], Awaitable[None]]


class ProbeFailure(Exception):
    """A fixed public reason; unknown values are reduced to probe_failed."""

    def __init__(self, reason: str):
        self.reason = reason if reason in REASONS else 'probe_failed'
        super().__init__(self.reason)


@dataclass(frozen=True)
class ConnectionSettings:
    """Private settings captured once; repr never includes endpoints or credentials."""

    timeout: float
    neo4j_uri: str = field(repr=False)
    neo4j_user: str = field(repr=False)
    neo4j_password: str = field(repr=False)
    neo4j_database: str = field(repr=False)
    postgres_dsn: str = field(repr=False)
    redis_url: str = field(repr=False)
    inference_provider: str = field(repr=False)
    inference_base: str = field(repr=False)
    inference_key: str = field(repr=False)


def _settings(environment: Mapping[str, str], timeout: float) -> ConnectionSettings:
    provider = environment.get('LM_PROXY_PROVIDER', 'lmstudio').strip().lower()
    if provider == 'openai':
        base = environment.get('LM_PROXY_OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
        key = environment.get('OPENAI_API_KEY', '').strip()
    else:
        base = environment.get('LM_BASE', 'http://127.0.0.1:1234').rstrip('/') + '/v1'
        # Legacy proxy chat/model requests do not add LMSTUDIO_API_KEY auth.
        key = ''
    return ConnectionSettings(timeout, environment.get('LM_PROXY_NEO4J_URI', 'bolt://127.0.0.1:7687'),
                              environment.get('LM_PROXY_NEO4J_USER', 'neo4j'),
                              environment.get('LM_PROXY_NEO4J_PASSWORD', 'password'),
                              environment.get('LM_PROXY_NEO4J_DB', 'neo4j'),
                              environment.get('LM_PROXY_PG_DSN', ''),
                              environment.get('LM_PROXY_REDIS_URL', 'redis://localhost:6379/0'),
                              provider, base, key)


async def _drain_task(task: asyncio.Task):
    """Shield/drain owned cleanup before propagating explicit asyncio cancellation."""
    cancelled = False
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        try:
            result = task.result()
        except asyncio.CancelledError:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _close_resource(close: Callable[[], Awaitable[None]], timeout: float) -> bool:
    """Give temporary-resource cleanup an independent, shielded bounded budget."""
    async def cleanup():
        try:
            with anyio.move_on_after(min(timeout, 1.0), shield=True) as scope:
                await close()
            return not scope.cancel_called
        except Exception:
            return False

    return await _drain_task(asyncio.create_task(cleanup()))


async def probe_neo4j(settings: ConnectionSettings) -> None:
    if not settings.neo4j_uri or not settings.neo4j_database:
        raise ProbeFailure('not_configured')
    from neo4j import AsyncGraphDatabase, READ_ACCESS, unit_of_work

    driver = AsyncGraphDatabase.driver(settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password), max_connection_pool_size=1,
        connection_timeout=settings.timeout, connection_acquisition_timeout=settings.timeout,
        max_transaction_retry_time=0)
    session = None
    clean = True
    try:
        session = driver.session(database=settings.neo4j_database, default_access_mode=READ_ACCESS)

        @unit_of_work(timeout=settings.timeout, metadata={'operation': 'readiness'})
        async def read(transaction):
            result = await transaction.run('RETURN 1 AS ready')
            row = await result.single()
            if row is None or row['ready'] != 1:
                raise ProbeFailure('invalid_response')

        await session.execute_read(read)
    finally:
        try:
            if session is not None:
                clean = await _close_resource(session.close, settings.timeout)
        finally:
            clean = await _close_resource(driver.close, settings.timeout) and clean
        if not clean:
            raise ProbeFailure('cleanup_failed')


async def probe_postgres(settings: ConnectionSettings) -> None:
    if not settings.postgres_dsn:
        raise ProbeFailure('not_configured')
    from psycopg import AsyncConnection

    connection = await AsyncConnection.connect(settings.postgres_dsn, autocommit=True,
                                               connect_timeout=math.ceil(settings.timeout))
    cursor = None
    clean = True
    try:
        cursor = await connection.execute('SELECT 1 AS ready')
        row = await cursor.fetchone()
        if row is None or row[0] != 1:
            raise ProbeFailure('invalid_response')
    finally:
        try:
            if cursor is not None:
                clean = await _close_resource(cursor.close, settings.timeout)
        finally:
            clean = await _close_resource(connection.close, settings.timeout) and clean
        if not clean:
            raise ProbeFailure('cleanup_failed')


async def probe_redis(settings: ConnectionSettings) -> None:
    if not settings.redis_url:
        raise ProbeFailure('not_configured')
    from redis.asyncio import Redis

    client = Redis.from_url(settings.redis_url, socket_connect_timeout=settings.timeout,
                            socket_timeout=settings.timeout)
    try:
        if await client.ping() is not True:
            raise ProbeFailure('invalid_response')
    finally:
        if not await _close_resource(lambda: client.aclose(close_connection_pool=True), settings.timeout):
            raise ProbeFailure('cleanup_failed')


async def probe_embedded(settings: ConnectionSettings) -> None:
    # Importing or calling get_embedded_runtime would turn this check into initialization.
    module = sys.modules.get('memory.embedded_runtime')
    runtime = getattr(module, '_runtime', None) if module is not None else None
    if runtime is None:
        raise ProbeFailure('not_initialized')
    async with runtime._lock:
        if runtime._closed:
            raise ProbeFailure('owner_closed')
        owner = runtime._owner
        if owner is None:
            raise ProbeFailure('not_initialized')
        async with owner._lock:
            graph, vectors = owner.graph, owner.vectors
            if graph is None or vectors is None or graph._closed or vectors._closed:
                raise ProbeFailure('owner_closed')
            session = graph.session()
            entered = False
            try:
                await session.__aenter__()
                entered = True
                # Only this new connection's read-query timeout changes. The shared
                # database configuration, owner and publication remain untouched.
                session._connection.set_query_timeout(max(1, int(settings.timeout * 1000)))

                async def read(transaction):
                    rows = await (await transaction.run('RETURN 1 AS ready')).data()
                    if rows != [{'ready': 1}]:
                        raise ProbeFailure('invalid_response')

                await session.execute_read(read)
            finally:
                if entered and not await _close_resource(
                        lambda: session.__aexit__(None, None, None), settings.timeout):
                    raise ProbeFailure('cleanup_failed')


async def probe_inference(settings: ConnectionSettings) -> None:
    if settings.inference_provider not in {'lmstudio', 'openai'}:
        raise ProbeFailure('unsupported_provider')
    if settings.inference_provider == 'openai' and not settings.inference_key:
        raise ProbeFailure('not_configured')
    try:
        parsed = urlsplit(settings.inference_base)
    except ValueError:
        raise ProbeFailure('invalid_endpoint') from None
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ProbeFailure('invalid_endpoint')
    import httpx

    headers = {'Authorization': 'Bearer ' + settings.inference_key} if settings.inference_key else {}
    # Preserve ambient proxy routing used by the provider. Redirects remain disabled.
    client = httpx.AsyncClient(timeout=settings.timeout, follow_redirects=False, trust_env=True)
    response = None
    try:
        request = client.build_request('GET', settings.inference_base + '/models', headers=headers)
        response = await client.send(request, stream=True)
        if response.status_code != 200:
            raise ProbeFailure('http_error')
        body = bytearray()
        async for block in response.aiter_bytes(chunk_size=4096):
            if len(body) + len(block) > 65536:
                raise ProbeFailure('invalid_response')
            body.extend(block)
        try:
            catalog = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise ProbeFailure('invalid_response') from None
        if not isinstance(catalog, dict) or not isinstance(catalog.get('data'), list):
            raise ProbeFailure('invalid_response')
    finally:
        clean = True
        try:
            if response is not None:
                clean = await _close_resource(response.aclose, settings.timeout)
        finally:
            clean = await _close_resource(client.aclose, settings.timeout) and clean
        if not clean:
            raise ProbeFailure('cleanup_failed')


class ReadinessMonitor:
    """One app lifespan's declared readiness with bounded concurrent/single-flight probes."""

    def __init__(self, *, default_required: tuple[str, ...] = (), probes: Mapping[str, Probe] | None = None,
                 environ: Mapping[str, str] | None = None):
        environment = os.environ if environ is None else environ
        raw = environment.get('LM_PROXY_READY_REQUIRED', ','.join(default_required)).strip().lower()
        selected = raw.split(',') if raw else []
        self.required = tuple(sorted({value.strip() for value in selected if value.strip() in DEPENDENCIES}))
        self.config_error = None
        if selected and any(value.strip() not in DEPENDENCIES for value in selected):
            self.config_error = 'unsupported_dependency'
        try:
            self.timeout = float(environment.get('LM_PROXY_READY_TIMEOUT_SECONDS', '2'))
            if not math.isfinite(self.timeout) or not 0.1 <= self.timeout <= 5:
                raise ValueError
        except (TypeError, ValueError):
            self.timeout = 2.0
            self.config_error = self.config_error or 'invalid_timeout'
        self._settings = _settings(environment, self.timeout)
        defaults = {'neo4j': probe_neo4j, 'postgres': probe_postgres, 'redis': probe_redis,
                    'embedded': probe_embedded, 'inference': probe_inference}
        self._probes = {name: (lambda function=function: function(self._settings))
                        for name, function in defaults.items()}
        if probes:
            self._probes.update({name: probe for name, probe in probes.items() if name in DEPENDENCIES})
        self.transport_active = False
        self._task = None
        self._cached = None
        self._cache_until = 0.0

    def activate(self) -> None:
        """Mark transport initialization complete; callers activate after startup succeeds."""
        self.transport_active = True
        self._cached = None

    async def close(self) -> None:
        """Mark inactive before draining/cancelling owned temporary probe resources."""
        self.transport_active = False
        self._cached = None
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await _drain_task(task)
            except asyncio.CancelledError:
                # Probe cancellation is expected; its finally blocks already drained.
                if asyncio.current_task().cancelling():
                    raise

    def _payload(self, dependencies: dict | None = None) -> dict:
        reason = 'config_error' if self.config_error else 'transport_inactive'
        statuses = dependencies or {name: {'status': 'not_checked', 'reason': reason, 'check': CHECKS[name]}
                                    for name in self.required}
        payload = {'ready': self.transport_active and not self.config_error
                   and all(value['status'] == 'ready' for value in statuses.values()),
                   'scope': 'declared_dependencies' if self.required else 'transport',
                   'coverage': 'transport_and_readonly_connectivity' if self.required else 'transport_lifespan',
                   'transport': {'status': 'active' if self.transport_active else 'inactive'},
                   'required': list(self.required), 'dependencies': statuses}
        if self.config_error:
            payload['config_error'] = {'reason': self.config_error}
        if 'embedded' in self.required:
            payload['native_timeout_enforcement'] = 'engine_cooperation_required'
        return payload

    async def _probe(self, name: str) -> dict:
        try:
            async with asyncio.timeout(self.timeout):
                await self._probes[name]()
            result = {'status': 'ready', 'reason': 'probe_succeeded'}
        except TimeoutError:
            result = {'status': 'unready', 'reason': 'timeout'}
        except ProbeFailure as error:
            result = {'status': 'unready', 'reason': error.reason}
        except ImportError:
            result = {'status': 'unready', 'reason': 'missing_package'}
        except Exception:
            result = {'status': 'unready', 'reason': 'probe_failed'}
        return dict(result, check=CHECKS[name])

    async def _check_once(self) -> tuple[int, dict]:
        values = await asyncio.gather(*(self._probe(name) for name in self.required))
        payload = (self._payload(dict(zip(self.required, values))) if self.transport_active
                   else self._payload())
        result = (200 if payload['ready'] else 503, payload)
        if self.transport_active:
            self._cached = result
            self._cache_until = asyncio.get_running_loop().time() + 1.0
        return result

    async def check(self) -> tuple[int, dict]:
        """Return sanitized response status/body; request cancellation preserves shared work."""
        if not self.transport_active or self.config_error:
            return 503, self._payload()
        if self._cached is not None and asyncio.get_running_loop().time() < self._cache_until:
            return deepcopy(self._cached)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._check_once())
        return deepcopy(await asyncio.shield(self._task))

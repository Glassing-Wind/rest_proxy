"""Authenticated, read-only REST access to an explicitly selected local corpus.

The factory starts no listener, creates no state and invokes no acquisition,
lesson mutation, model or training operation. This surface supports native local
clients; browser Origin headers are refused and CORS is not enabled.
"""
from __future__ import annotations

import ipaddress
import re
import secrets

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route

from graphrag_core.lessons_corpus.reads import (
    CorpusReads, InvalidQuery, MissingSource, MAX_PAGE, MAX_SLICE, MAX_URL as MAX_URL, _STATUSES,
)


SECURITY_HEADERS = {
    'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Cross-Origin-Resource-Policy': 'same-origin',
    'X-Frame-Options': 'DENY',
}


class LocalReadAccessMiddleware:
    """Require an actual loopback peer, exact Host, no Origin and one bearer."""

    def __init__(self, app, token: str, allowed_host: str):
        self.app = app
        self.authorization = ('Bearer ' + token).encode('ascii')
        self.allowed_host = allowed_host

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        client = scope.get('client')
        try:
            local = client is not None and ipaddress.ip_address(client[0]).is_loopback
        except ValueError:
            local = False
        error, status = None, 403
        if not local or headers.getlist('host') != [self.allowed_host]:
            error = 'local-host-required'
        elif headers.getlist('origin'):
            error = 'browser-origin-not-supported'
        else:
            authorizations = headers.getlist('authorization')
            if len(authorizations) != 1 or not secrets.compare_digest(
                authorizations[0].encode('latin-1'), self.authorization
            ):
                error, status = 'local-capability-required', 401
        if error:
            await JSONResponse({'error': error}, status_code=status,
                               headers=SECURITY_HEADERS)(scope, receive, send)
            return

        async def protected_send(message):
            if message['type'] == 'http.response.start':
                additions = [(name.lower().encode('ascii'), value.encode('ascii'))
                             for name, value in SECURITY_HEADERS.items()]
                names = {name for name, _ in additions}
                current = [header for header in message.get('headers', []) if header[0].lower() not in names]
                message = dict(message, headers=[*current, *additions])
            await send(message)
        await self.app(scope, receive, protected_send)


def _parameters(request, allowed: set[str]) -> dict[str, str]:
    if len(request.scope.get('query_string', b'')) > 8192:
        raise InvalidQuery
    params = request.query_params
    if any(key not in allowed or len(params.getlist(key)) != 1 for key in params):
        raise InvalidQuery
    return dict(params)


def _number(params: dict, name: str, default: int, floor: int, ceiling: int) -> int:
    raw = params.get(name, str(default))
    if not re.fullmatch(r'[0-9]{1,10}', raw):
        raise InvalidQuery
    value = int(raw)
    if not floor <= value <= ceiling:
        raise InvalidQuery
    return value


def _text(params: dict, name: str, *, required: bool = False, limit: int = 2048) -> str | None:
    value = params.get(name)
    if value is None:
        if required:
            raise InvalidQuery
        return None
    if not value.strip() or len(value) > limit or '\x00' in value:
        raise InvalidQuery
    return value


def create_app(corpus_path: str, token: str, allowed_host: str = '127.0.0.1') -> Starlette:
    """Build the read adapter; callers explicitly choose corpus, token and Host."""
    if type(token) is not str or not re.fullmatch(r'[A-Za-z0-9._~+\-]{32,256}', token):
        raise ValueError('Require an unpredictable ASCII capability of 32 through 256 characters')
    if type(allowed_host) is not str:
        raise ValueError('Require an exact loopback hostname and optional valid port')
    match = re.fullmatch(r'(127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?', allowed_host)
    if match is None or (match[2] and not 1 <= int(match[2]) <= 65535):
        raise ValueError('Require an exact loopback hostname and optional valid port')
    reads = CorpusReads(corpus_path)
    corpus = reads.corpus

    async def invoke(function, *args, **kwargs):
        try:
            return JSONResponse(await run_in_threadpool(function, *args, **kwargs))
        except InvalidQuery:
            return JSONResponse({'error': 'invalid-query'}, status_code=400)
        except MissingSource:
            return JSONResponse({'error': 'source-not-found'}, status_code=404)
        except (ValueError, KeyError):
            return JSONResponse({'error': 'corpus-integrity-check-failed'}, status_code=409)
        except Exception:
            return JSONResponse({'error': 'corpus-operation-unavailable'}, status_code=503)

    async def health(request):
        _parameters(request, set())
        return JSONResponse({'ok': True, 'surface': 'local-lessons-corpus', 'read_only': True})

    async def sources(request):
        params = _parameters(request, {'offset', 'limit'})
        return await invoke(reads.catalog, _number(params, 'offset', 0, 0, 1000000000),
                            _number(params, 'limit', 50, 1, MAX_PAGE))

    async def source(request):
        path_source = request.path_params.get('source_id')
        params = _parameters(request, {'start_char', 'end_char'} | ({'source_id'} if path_source is None else set()))
        source_id = path_source or _text(params, 'source_id', required=True)
        if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 2048 or '\x00' in source_id:
            raise InvalidQuery
        start = _number(params, 'start_char', 0, 0, 1000000000)
        end = _number(params, 'end_char', 0, 0, 1000000000) if 'end_char' in params else None
        if end is not None and (end < start or end - start > MAX_SLICE):
            raise InvalidQuery
        return await invoke(reads.excerpt, source_id, start, end)

    async def search(request):
        params = _parameters(request, {'query', 'limit', 'source_kind'})
        query = _text(params, 'query', required=True, limit=1024)
        limit = _number(params, 'limit', 10, 1, 50)
        source_kind = _text(params, 'source_kind', limit=128)

        return await invoke(reads.search, query, limit, source_kind)

    async def reference_list(request):
        params = _parameters(request, {'offset', 'limit', 'source_id', 'status'})
        status = _text(params, 'status', limit=64)
        if status is not None and status not in _STATUSES:
            raise InvalidQuery
        return await invoke(reads.references, _number(params, 'offset', 0, 0, 1000000000),
                            _number(params, 'limit', 50, 1, MAX_PAGE),
                            _text(params, 'source_id'), status)

    async def invalid(request, error):
        return JSONResponse({'error': 'invalid-query'}, status_code=400, headers=SECURITY_HEADERS)

    async def http_error(request, error):
        name = 'method-not-supported' if error.status_code == 405 else 'route-not-found'
        return JSONResponse({'error': name}, status_code=error.status_code, headers=SECURITY_HEADERS)

    async def unavailable(request, error):
        return JSONResponse({'error': 'corpus-operation-unavailable'}, status_code=503, headers=SECURITY_HEADERS)

    app = Starlette(routes=[
        Route('/health', health), Route('/sources', sources), Route('/source', source),
        Route('/sources/{source_id:path}', source), Route('/search', search), Route('/references', reference_list),
    ], exception_handlers={InvalidQuery: invalid, HTTPException: http_error, Exception: unavailable})
    app.state.corpus = corpus
    app.add_middleware(LocalReadAccessMiddleware, token=token, allowed_host=allowed_host)
    return app

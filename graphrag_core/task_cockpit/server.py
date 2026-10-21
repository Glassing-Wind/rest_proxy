"""Explicit local task-status API and static Cockpit shell; no worker operations."""
from __future__ import annotations

import asyncio
from pathlib import Path
import re

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from graphrag_core.evidence_workspace.server import LocalAccessMiddleware, SECURITY_HEADERS
from graphrag_core.task_cockpit.reads import MissingTaskError, TaskStatusIntegrityError, TaskStatusReader


STATIC = Path(__file__).with_name('static')


class InvalidRequest(ValueError):
    """Only exact, body-free reads are supported by this surface."""


async def _exact_read(request):
    if request.method != 'GET':
        raise HTTPException(405)
    if request.scope.get('query_string', b''):
        raise InvalidRequest
    if request.headers.get('content-encoding', 'identity') != 'identity':
        raise InvalidRequest
    async for chunk in request.stream():
        if chunk:
            raise InvalidRequest


def create_app(state_directory: str, project: str, task_id: str,
               token: str, allowed_host: str) -> Starlette:
    """Select one existing task, one capability and one exact loopback Host.

    Construction opens no listener, creates no registry state and invokes no
    model, dispatcher, review operation or shared-service lifecycle.
    """
    if type(token) is not str or not re.fullmatch(r'[A-Za-z0-9._~+\-]{32,256}', token):
        raise ValueError('Require an unpredictable ASCII capability of 32 through 256 characters')
    match = re.fullmatch(r'(127\.0\.0\.1|localhost|\[::1\]):([0-9]{1,5})', allowed_host) if isinstance(allowed_host, str) else None
    if match is None or not 1 <= int(match[2]) <= 65535:
        raise ValueError('Require an exact loopback host and valid port')
    reader = TaskStatusReader(state_directory, project, task_id)

    async def status(request):
        await _exact_read(request)
        return JSONResponse(await asyncio.to_thread(reader.status))

    async def live(request):
        await _exact_read(request)
        return JSONResponse({'ok': True, 'surface': 'local-task-cockpit', 'read_only': True})

    def static_endpoint(name, media_type):
        async def endpoint(request):
            await _exact_read(request)
            return FileResponse(STATIC / name, media_type=media_type)
        return endpoint

    async def invalid(request, error):
        return JSONResponse({'error': 'invalid-request'}, status_code=400)

    async def missing(request, error):
        return JSONResponse({'error': 'task-not-found'}, status_code=404)

    async def integrity(request, error):
        return JSONResponse({'error': 'task-status-integrity-check-failed'}, status_code=409)

    async def http_error(request, error):
        name = 'method-not-supported' if error.status_code == 405 else 'route-not-found'
        return JSONResponse({'error': name}, status_code=error.status_code)

    async def unavailable(request, error):
        return JSONResponse({'error': 'task-status-unavailable'}, status_code=503,
                            headers=SECURITY_HEADERS)

    app = Starlette(routes=[
        Route('/', static_endpoint('index.html', 'text/html')),
        Route('/app.js', static_endpoint('app.js', 'text/javascript')),
        Route('/style.css', static_endpoint('style.css', 'text/css')),
        Route('/live', live), Route('/api/v1/task-status', status),
    ], exception_handlers={InvalidRequest: invalid, MissingTaskError: missing,
                           TaskStatusIntegrityError: integrity, HTTPException: http_error,
                           Exception: unavailable})
    app.state.reader = reader
    app.add_middleware(LocalAccessMiddleware, token=token, allowed_host=allowed_host)
    return app

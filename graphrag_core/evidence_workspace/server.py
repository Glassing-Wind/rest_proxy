"""A standalone local review surface with a per-launch capability and no egress."""
from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
import re
import secrets

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from memory.evidence_workspace import EvidenceWorkspaceStore
from graphrag_core.evidence_workspace.contracts import (
    MAX_BACKUP_BYTES, MAX_REQUEST_BYTES, WorkspaceConflict, WorkspaceError,
    WorkspaceIntegrityError, decode_content, identifier, object_fields, read_json,
)
from graphrag_core.evidence_workspace.demo import demo_package

STATIC = Path(__file__).with_name('static')
SECURITY_HEADERS = {
    'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "img-src 'self' blob:; connect-src 'self'; base-uri 'none'; "
                             "form-action 'none'; frame-ancestors 'none'",
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Cross-Origin-Resource-Policy': 'same-origin',
    'X-Frame-Options': 'DENY',
}


class LocalAccessMiddleware:
    """Only loopback, exact Host/Origin and the current in-memory capability."""

    def __init__(self, app, token: str, allowed_host: str):
        self.app, self.token, self.allowed_host = app, token, allowed_host
        self.origin = 'http://' + allowed_host

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        hosts, origins = headers.getlist('host'), headers.getlist('origin')
        client = scope.get('client')
        try:
            loopback = client is not None and ipaddress.ip_address(client[0]).is_loopback
        except ValueError:
            loopback = False
        error, status = None, 403
        if not loopback or hosts != [self.allowed_host]:
            error = 'local-host-required'
        elif origins and origins != [self.origin]:
            error = 'same-origin-required'
        elif scope['path'].startswith('/api/'):
            authorizations = headers.getlist('authorization')
            if len(authorizations) != 1 or not secrets.compare_digest(
                    authorizations[0].encode('utf-8'), ('Bearer ' + self.token).encode('utf-8')):
                error, status = 'local-capability-required', 401
        if error:
            await JSONResponse({'error': error}, status_code=status,
                               headers=SECURITY_HEADERS)(scope, receive, send)
            return

        async def protected_send(message):
            if message['type'] == 'http.response.start':
                extra = [(key.lower().encode(), value.encode()) for key, value in SECURITY_HEADERS.items()]
                message = dict(message, headers=[*message.get('headers', []), *extra])
            await send(message)
        await self.app(scope, receive, protected_send)


async def _body(request, limit=MAX_REQUEST_BYTES) -> bytes:
    if request.headers.get('content-encoding', 'identity') != 'identity':
        raise WorkspaceError('Compressed request bodies are unsupported')
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise WorkspaceError('Request exceeds its byte budget')
    return bytes(data)


async def _json(request) -> dict:
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise WorkspaceError('Require application/json')
    raw = await _body(request)
    result = read_json(raw)
    if not isinstance(result, dict):
        raise WorkspaceError('Require a JSON object')
    return result


def create_app(state_directory: str, token: str, allowed_host: str) -> Starlette:
    """Construct an explicit private workspace, without changing MCP/proxy services."""
    if not isinstance(token, str) or len(token) < 32 or not token.isascii():
        raise WorkspaceError('Require an unpredictable per-launch capability of at least 32 characters')
    match = re.fullmatch(r'(127\.0\.0\.1|localhost):([0-9]{1,5})', allowed_host)
    if match is None or not 1 <= int(match[2]) <= 65535:
        raise WorkspaceError('Require an exact loopback host and port')
    store = EvidenceWorkspaceStore(state_directory)

    async def invoke(function, *args, **kwargs):
        try:
            result = await asyncio.to_thread(function, *args, **kwargs)
            return JSONResponse(result)
        except WorkspaceConflict:
            return JSONResponse({'error': 'case-revision-or-identity-conflict'}, status_code=409)
        except WorkspaceIntegrityError:
            return JSONResponse({'error': 'evidence-integrity-check-failed'}, status_code=422)
        except WorkspaceError as error:
            return JSONResponse({'error': str(error)}, status_code=422)
        except Exception:
            return JSONResponse({'error': 'workspace-operation-unavailable'}, status_code=503)

    async def root(request):
        return FileResponse(STATIC / 'index.html', media_type='text/html')

    async def javascript(request):
        return FileResponse(STATIC / 'app.js', media_type='text/javascript')

    async def stylesheet(request):
        return FileResponse(STATIC / 'style.css', media_type='text/css')

    async def live(request):
        return JSONResponse({'ok': True, 'surface': 'local-evidence-workspace'})

    async def cases(request):
        return await invoke(store.list_cases)

    async def case(request):
        return await invoke(store.get_case, request.path_params['case_id'])

    async def imports(request):
        return await invoke(store.import_package, await _json(request))

    async def demo(request):
        await _body(request, 1024)
        return await invoke(lambda: store.import_package(demo_package()))

    async def source(request):
        try:
            first = int(request.query_params.get('start_line', '1'))
            count = int(request.query_params.get('max_lines', '200'))
        except ValueError as error:
            raise WorkspaceError('Source page parameters must be integers') from error
        return await invoke(store.get_source, request.path_params['case_id'],
                            request.path_params['source_id'], first, count)

    async def content(request):
        data, media_type = await asyncio.to_thread(
            store.get_source_content, request.path_params['case_id'], request.path_params['source_id'])
        source_id = identifier(request.path_params['source_id'])
        extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'text/csv': 'csv',
                     'application/json': 'json'}.get(media_type, 'txt')
        return Response(data, media_type=media_type,
                        headers={'Content-Disposition': f'attachment; filename="{source_id}.{extension}"'})

    async def review(request):
        value = await _json(request)
        object_fields(value, {'relationship_id', 'decision', 'reviewer', 'reason', 'expected_revision'}, {'statement'})
        return await invoke(store.review, request.path_params['case_id'], **value)

    async def check(request):
        value = await _json(request)
        object_fields(value, {'source_id', 'expected_revision'}, {'content_base64', 'missing'})
        if 'content_base64' not in value and value.get('missing') is not True:
            raise WorkspaceError('Supply current bytes or an explicit missing marker')
        current = decode_content(value['content_base64']) if 'content_base64' in value else None
        return await invoke(store.check_source, request.path_params['case_id'],
                            value['source_id'], value['expected_revision'], content=current,
                            missing=value.get('missing', False))

    async def report(request):
        case_id = identifier(request.path_params['case_id'])
        rendered = await asyncio.to_thread(store.export_report, case_id)
        return Response(rendered, media_type='text/markdown',
                        headers={'Content-Disposition': f'attachment; filename="{case_id}-reviewed-report.md"'})

    async def backup(request):
        case_id = identifier(request.path_params['case_id'])
        data = await asyncio.to_thread(store.export_backup, case_id)
        return Response(data, media_type='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{case_id}-private-recovery.zip"'})

    async def restore(request):
        if request.headers.get('content-type', '').split(';')[0].lower() not in {'application/zip', 'application/octet-stream'}:
            raise WorkspaceError('Require a ZIP request body')
        return await invoke(store.restore_backup, await _body(request, MAX_BACKUP_BYTES),
                            case_id=request.headers.get('x-restore-case-id') or None)

    async def invalid(request, error):
        return JSONResponse({'error': 'invalid-workspace-input'}, status_code=422)

    async def integrity(request, error):
        return JSONResponse({'error': 'evidence-integrity-check-failed'}, status_code=422)

    async def conflict(request, error):
        return JSONResponse({'error': 'case-revision-or-identity-conflict'}, status_code=409)

    async def unavailable(request, error):
        return JSONResponse({'error': 'workspace-operation-unavailable'}, status_code=503,
                            headers=SECURITY_HEADERS)

    app = Starlette(routes=[
        Route('/', root), Route('/app.js', javascript), Route('/style.css', stylesheet), Route('/live', live),
        Route('/api/cases', cases), Route('/api/import', imports, methods=['POST']),
        Route('/api/demo', demo, methods=['POST']),
        Route('/api/restore', restore, methods=['POST']),
        Route('/api/cases/{case_id}', case),
        Route('/api/cases/{case_id}/sources/{source_id}', source),
        Route('/api/cases/{case_id}/sources/{source_id}/content', content),
        Route('/api/cases/{case_id}/reviews', review, methods=['POST']),
        Route('/api/cases/{case_id}/checks', check, methods=['POST']),
        Route('/api/cases/{case_id}/report', report), Route('/api/cases/{case_id}/backup', backup),
    ], exception_handlers={WorkspaceError: invalid, WorkspaceIntegrityError: integrity,
                           WorkspaceConflict: conflict, Exception: unavailable})
    app.state.store = store
    app.add_middleware(LocalAccessMiddleware, token=token, allowed_host=allowed_host)
    return app

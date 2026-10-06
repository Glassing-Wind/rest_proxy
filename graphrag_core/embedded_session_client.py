"""Explicit loopback MCP registrar/discovery client; never opens embedded engines."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit


def validate_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != '/mcp'):
        raise ValueError('Use an explicit loopback HTTP /mcp endpoint without credentials/query/fragment')
    if parsed.port is None:
        raise ValueError('Specify the owning MCP service port')
    return url


def decode(result) -> dict:
    if result.isError:
        raise RuntimeError('Owning MCP service rejected the session operation')
    value = result.structuredContent
    if value is None:
        value = json.loads(result.content[0].text)
    if isinstance(value, dict) and set(value) == {'result'}:
        value = value['result']
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('Expected an object from the owning MCP service')
    return value


async def session_operation(session, session_id: str, workspace_path: str | None = None,
                            lease_seconds: int = 900) -> dict:
    if not session_id or len(session_id) > 128:
        raise ValueError('Use an explicit session ID of at most 128 characters')
    if workspace_path is None:
        result = decode(await session.call_tool('resolve_embedded_session', {'session_id': session_id}))
        if result.get('status') != 'resolved':
            raise ValueError('Session discovery did not resolve a unique active published workspace')
        return result
    if type(lease_seconds) is not int or lease_seconds != 0 and not 60 <= lease_seconds <= 3600:
        raise ValueError('Use lease 60..3600 seconds or zero to release')
    root = str(Path(workspace_path).expanduser().resolve())
    resolved = decode(await session.call_tool('resolve_graph_project', {'workspace_id': root}))
    project = resolved.get('project_id')
    if not project:
        raise ValueError('Workspace has no published embedded project')
    activity = decode(await session.call_tool('get_embedded_workspace_activity', {'project_id': project}))
    if activity.get('workspace_path') != root or activity.get('status') not in {'published', 'workspace_changed'}:
        raise ValueError('Workspace activity does not match the requested canonical root')
    result = decode(await session.call_tool('refresh_embedded_session',
                   {'project_id': project, 'session_id': session_id, 'lease_seconds': lease_seconds,
                    'expected_revision': activity['revision'], 'expected_run_id': activity['run_id']}))
    if result.get('status') != 'published':
        raise ValueError('Session registration conflicted; review current state before retrying')
    return result


async def run_session_operation(url: str, session_id: str, workspace_path: str | None = None,
                                lease_seconds: int = 900) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    validate_endpoint(url)
    async with asyncio.timeout(15):
        async with streamable_http_client(url) as streams:
            async with ClientSession(*streams[:2]) as session:
                await session.initialize()
                return await session_operation(session, session_id, workspace_path, lease_seconds)


async def refresh_loop(url: str, session_id: str, workspace_path: str, lease_seconds: int,
                       stop: asyncio.Event, interval: float | None = None) -> dict:
    """Explicit foreground refresh; stop on failed renewal and best-effort release on exit."""
    interval = lease_seconds / 3 if interval is None else interval
    if not 60 <= lease_seconds <= 3600 or not 0 < interval <= lease_seconds / 3:
        raise ValueError('Refresh interval must be positive and at most one third of a valid lease')
    result = None
    released = False
    try:
        while not stop.is_set():
            result = await run_session_operation(url, session_id, workspace_path, lease_seconds)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass
    finally:
        if result is not None:
            try:
                response = await run_session_operation(url, session_id, workspace_path, 0)
                released = response.get('status') == 'published'
            except Exception:
                pass
    return {'status': 'stopped', 'session_id': session_id, 'lease_release_confirmed': released}

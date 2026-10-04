#!/usr/bin/env python3
"""Optional real STDIO/HTTP MCP parity check against an existing embedded publication.

No indexing/model lifecycle mutation. Requires native storage extras and an explicit
state/project/file. Launches isolated servers with memory and watchers disabled.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]


def decode(result):
    if result.isError:
        raise AssertionError('Embedded MCP call failed')
    value = result.structuredContent if result.structuredContent is not None else json.loads(result.content[0].text)
    if isinstance(value, dict) and set(value) == {'result'}:
        value = value['result']
    return json.loads(value) if isinstance(value, str) else value



async def inspect(session, args):
    await session.initialize()
    names = {tool.name for tool in (await session.list_tools()).tools}
    assert {'get_embedded_overview', 'describe_embedded_file', 'search_embedded_repository'} <= names
    overview = decode(await session.call_tool('get_embedded_overview', {'project_id': args.project_id}))
    projects = decode(await session.call_tool('list_embedded_projects', {'limit': 100}))
    # Existing standard workspace tools must resolve the durable path to this ID.
    project = next(row for row in projects['projects'] if row['project_id'] == args.project_id)
    resolution = decode(await session.call_tool('resolve_graph_project', {'workspace_id': project['workspace_path']}))
    workspace_overview = decode(await session.call_tool('get_project_overview', {'workspace_id': project['workspace_path']}))
    assert resolution['project_id'] == args.project_id
    assert workspace_overview['run_id'] == overview['run_id']
    source = decode(await session.call_tool('describe_embedded_file',
                                           {'project_id': args.project_id, 'file_path': args.file,
                                            'max_lines': 12}))
    assert source['run_id'] == overview['run_id']
    hits = decode(await session.call_tool('search_embedded_repository',
                                          {'project_id': args.project_id, 'query': args.query,
                                           'mode': 'text', 'limit': 5}))
    # FastMCP wraps non-object structured output in {result: ...}.
    if isinstance(hits, dict):
        hits = hits['result']
    assert hits and all(hit['run_id'] == overview['run_id'] for hit in hits)
    return {'overview': overview, 'source': source, 'projects': projects,
            'resolution': resolution, 'workspace_overview': workspace_overview,
            'hit_citations': [{key: hit[key] for key in ('file_path', 'ref_id', 'source_sha256', 'run_id')}
                             for hit in hits]}


async def run(args):
    env = dict(os.environ, LM_PROXY_STORAGE_BACKEND='embedded', LM_PROXY_GRAPH_BACKEND='ladybug',
               LM_PROXY_EMBEDDED_STATE=str(args.state.resolve()), LM_PROXY_MEMORY_EMBEDDING_DIM=str(args.dimension),
               LM_PROXY_MEMORY_ENABLED='0', LM_PROXY_WATCHER_ENABLED='0', LM_PROXY_TOOL_PROFILE='primary')
    with tempfile.TemporaryDirectory() as cwd:
        command, parameters = sys.executable, [str(ROOT / 'mcp_server.py')]
        if args.sandbox:
            command, parameters = 'sandbox-exec', ['-p', '(version 1) (allow default) (deny network*)',
                                                   command, *parameters]
        async with stdio_client(StdioServerParameters(command=command, args=parameters, cwd=cwd, env=env)) as streams:
            async with ClientSession(*streams) as session:
                stdio = await inspect(session, args)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        command = [sys.executable, '-m', 'uvicorn', 'brain_server:app', '--host', '127.0.0.1', '--port', str(port)]
        if args.sandbox:
            policy = f'(version 1) (allow default) (deny network*) (allow network* (local ip "localhost:{port}"))'
            command = ['sandbox-exec', '-p', policy, *command]
        with tempfile.TemporaryFile() as logs:
            server = await asyncio.create_subprocess_exec(*command, cwd=ROOT, env=env, stdout=logs, stderr=logs)
            try:
                for _ in range(100):
                    if server.returncode is not None:
                        raise AssertionError('HTTP MCP server exited before readiness')
                    try:
                        _, writer = await asyncio.open_connection('127.0.0.1', port)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except OSError:
                        await asyncio.sleep(0.1)
                else:
                    raise AssertionError('HTTP MCP server readiness timed out')
                async with streamable_http_client(f'http://127.0.0.1:{port}/mcp') as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        http = await inspect(session, args)
            finally:
                if server.returncode is None:
                    server.terminate()
                    try:
                        await asyncio.wait_for(server.wait(), timeout=10)
                    except TimeoutError:
                        server.kill()
                        await server.wait()
    assert stdio == http, 'Transport outputs differ'
    return {'transports': ['stdio', 'streamable-http'], 'parity': True,
            'run_id': stdio['overview']['run_id'], 'files': stdio['overview']['files'],
            'dimension': args.dimension, 'source_sha256': stdio['source']['source_sha256'],
            'hit_citations': stdio['hit_citations'], 'model_required': False, 'workspace_resolution_verified': True,
            'project_listing_verified': True,
            'sandbox': args.sandbox, 'external_storage_network_allowed': False if args.sandbox else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--file', required=True)
    parser.add_argument('--query', required=True)
    parser.add_argument('--dimension', type=int, default=768)
    parser.add_argument('--sandbox', action='store_true', help='macOS: deny network except local HTTP transport')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), sort_keys=True))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Optional real registrar subprocess acceptance with disposable native stores/synthetic vectors."""
import asyncio
import json
import os
import signal
from pathlib import Path
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner  # noqa: E402


async def run():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        source = base / 'source'
        source.mkdir()
        (source / 'a.py').write_text('def first():\n    return 1\n')
        async def embed(texts):
            return [[1., 0., 0.] for _ in texts]
        async with EmbeddedRepositoryOwner(str(base / 'state'), 3) as owner:
            publication = await owner.index(str(source), 'fixture', ['a.py'], embed=embed, encoder_id='fixture')
        config = base / 'config'
        config.mkdir()
        for name in ('sessions.json', 'active_sessions.json', 'pinned_watches.json'):
            (config / name).write_text('{"sentinel":"unchanged"}')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        url = f'http://127.0.0.1:{port}/mcp'
        env = dict(os.environ, LM_PROXY_STORAGE_BACKEND='embedded', LM_PROXY_GRAPH_BACKEND='ladybug',
                   LM_PROXY_EMBEDDED_STATE=str(base / 'state'), LM_PROXY_MEMORY_EMBEDDING_DIM='3',
                   LM_PROXY_MEMORY_ENABLED='0', LM_PROXY_WATCHER_ENABLED='0', LM_PROXY_TOOL_PROFILE='primary',
                   LM_PROXY_CONFIG_DIR=str(config), PYTHONPATH=str(ROOT),
                   LM_PROXY_EMBEDDED_MODEL_ARTIFACT='', LM_PROXY_EMBEDDED_WATCH_ENABLED='0')
        with tempfile.TemporaryFile() as logs:
            server = await asyncio.create_subprocess_exec(sys.executable, '-m', 'uvicorn', 'brain_server:app',
                '--host', '127.0.0.1', '--port', str(port), cwd=base, env=env, stdout=logs, stderr=logs)
            async def cli(*arguments, success=True):
                process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / 'scripts/register_session.py'),
                    '--embedded', '--mcp-url', url, '--session-id', 'acceptance-ide', *arguments,
                    cwd=base, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                output, _ = await asyncio.wait_for(process.communicate(), 25)
                if (process.returncode == 0) != success:
                    raise AssertionError('Registrar CLI returned unexpected status')
                return json.loads(output) if success else None
            try:
                for _ in range(100):
                    try:
                        _, writer = await asyncio.open_connection('127.0.0.1', port)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except OSError:
                        await asyncio.sleep(0.1)
                else:
                    raise AssertionError('Owner service readiness timed out')
                watch = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / 'scripts/watch_embedded_project.py'),
                    'fixture', '--mcp-url', url, '--enable', cwd=base, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                watch_output, _ = await asyncio.wait_for(watch.communicate(), 35)
                blocked_watch = json.loads(watch_output)
                assert watch.returncode == 1 and not blocked_watch['ready']
                assert blocked_watch['scope']['paths'] == ['a.py']
                await cli('--discover', success=False)
                registered = await cli(str(source))
                assert registered['watch_requested'] is False
                assert registered['sessions'].get('acceptance-ide')
                assert registered['run_id'] == publication['run_id']
                discovered = await cli('--discover')
                assert discovered['workspace_path'] == str(source)
                assert discovered['project_id'] == 'fixture'
                released = await cli(str(source), '--lease-seconds', '0')
                assert released['sessions'] == {}
                await cli('--discover', success=False)
                await cli(str(base / 'missing'), success=False)
                refresh = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / 'scripts/register_session.py'),
                    str(source), '--embedded', '--mcp-url', url, '--session-id', 'acceptance-ide',
                    '--refresh', '--lease-seconds', '60', '--refresh-interval', '1',
                    cwd=base, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    await asyncio.sleep(1.5)
                    first = await cli('--discover')
                    await asyncio.sleep(1.5)
                    renewed = await cli('--discover')
                    assert renewed['expires_ms'] > first['expires_ms']
                    refresh.send_signal(signal.SIGTERM)
                    await asyncio.wait_for(refresh.communicate(), 25)
                    assert refresh.returncode == 0
                    await cli('--discover', success=False)
                finally:
                    if refresh.returncode is None:
                        refresh.kill()
                        await refresh.communicate()

            finally:
                if server.returncode is None:
                    server.terminate()
                    try:
                        await asyncio.wait_for(server.wait(), 10)
                    except TimeoutError:
                        server.kill()
                        await server.wait()
        assert all((config / name).read_text() == '{"sentinel":"unchanged"}'
                   for name in ('sessions.json', 'active_sessions.json', 'pinned_watches.json'))
        return {'watch_setup_blocked_without_intent_change': True, 'automatic_refresh_verified': True, 'sigterm_release_verified': True, 'registration_verified': True, 'discovery_verified': True, 'release_verified': True,
                'missing_and_released_sessions_refused': True, 'missing_workspace_refused': True,
                'legacy_registries_unchanged': True, 'owner_service_remained_usable': True,
                'synthetic_vectors': True, 'model_required': False, 'transports': ['streamable-http'],
                'run_id': publication['run_id']}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(run()), sort_keys=True))

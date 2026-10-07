#!/usr/bin/env python3
"""Disposable installed-package/native acceptance; synthetic vectors, loopback REST.

Run with the installed environment's Python and -I, from outside the checkout.
Requires the modified ts-pack wheel, embedded dependencies and core package.
Does not access models or operational storage. macOS/Linux transport check.
"""
import asyncio
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


async def check(base: Path) -> dict:
    # Use a private CWD and explicit paths before importing application modules.
    os.chdir(base)
    for name in list(os.environ):
        if name.startswith(('LM_PROXY_', 'LMSTUDIO_', 'OPENAI_')):
            del os.environ[name]
    os.environ.update({
        'LM_PROXY_STORAGE_BACKEND': 'embedded', 'LM_PROXY_GRAPH_BACKEND': 'ladybug',
        'LM_PROXY_EMBEDDED_STATE': str(base / 'state'), 'LM_PROXY_MEMORY_EMBEDDING_DIM': '3',
        'LM_PROXY_CONFIG_DIR': str(base / 'config'), 'LM_PROXY_RUNTIME_DIR': str(base / 'runtime'),
        'LM_PROXY_MEMORY_ENABLED': '0', 'LM_PROXY_MEMORY_ENABLE_REDIS': '0',
        'LM_PROXY_MEMORY_ENABLE_PERSISTENCE': '0', 'LM_PROXY_MEMORY_ENABLE_EMBEDDINGS': '0',
        'LM_PROXY_EMBEDDED_WATCH_ENABLED': '0', 'LM_PROXY_EMBEDDED_REST_ENABLED': '1',
        'LM_PROXY_CONTEXT_ENABLED': '1', 'LM_PROXY_FIRE_STATE': str(base / 'fire'),
    })
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    port = listener.getsockname()[1]

    def network_guard(event, arguments):
        if event == 'socket.connect':
            address = arguments[1]
            if not isinstance(address, tuple) or address[:2] != ('127.0.0.1', port):
                raise RuntimeError('Acceptance permits only its disposable REST listener')

    sys.addaudithook(network_guard)
    import httpx
    import graphrag_core.indexing.embedded_repository as repository
    import _jobs
    import ts_diagnostics
    for module in (repository, _jobs, ts_diagnostics):
        if not Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise RuntimeError('Acceptance must run from the installed environment')
    assert _jobs._ensure_jobs_runtime_dir() == base / 'runtime/jobs'
    assert _jobs._ensure_project_locks_dir() == base / 'runtime/project_locks'
    source = base / 'source'
    source.mkdir()
    text = 'def helper():\n    return 7\n'
    (source / 'helper.py').write_text(text)
    (source / 'main.py').write_text('from helper import helper\nprint(helper())\n')

    async def synthetic_embed(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    async with repository.EmbeddedRepositoryOwner(str(base / 'state'), 3) as owner:
        publication = await owner.index(str(source), 'installed-fixture', ['helper.py', 'main.py'],
                                        embed=synthetic_embed, encoder_id='synthetic-fixture-only')
        vectors = await owner.search('installed-fixture', text='helper', mode='vector',
                                     encoder_id='synthetic-fixture-only', vector=[1.0, 0.0, 0.0], limit=2)
        assert vectors
    # New process owns native storage for the REST reads; the first owner is closed.
    code = '''
import socket, sys
port = int(sys.argv[2])
def guard(event, arguments):
    if event == 'socket.connect':
        address = arguments[1]
        if not isinstance(address, tuple) or address[:2] != ('127.0.0.1', port):
            raise RuntimeError('External storage/network denied by acceptance')
sys.addaudithook(guard)
import uvicorn
from brain_server import app
server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
server.run(sockets=[socket.socket(fileno=int(sys.argv[1]))])
'''
    with (base / 'daemon.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-I', '-c', code, str(listener.fileno()), str(port)],
                                   cwd=base, env=os.environ.copy(), pass_fds=(listener.fileno(),),
                                   stdout=log, stderr=log)
        listener.close()
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                url = f'http://127.0.0.1:{port}'
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Installed daemon exited; inspect acceptance dependencies')
                    try:
                        response = await client.get(url + '/health')
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise RuntimeError('Installed daemon startup timed out')
                response = await client.post(url + '/evidence/read', json={
                    'tool': 'describe_embedded_file', 'arguments': {
                        'project_id': 'installed-fixture', 'file_path': 'helper.py'}})
                response.raise_for_status()
                evidence = response.json()
                assert 'return 7' in evidence['source']
                assert evidence['source_sha256'] == hashlib.sha256(text.encode()).hexdigest()
                response = await client.post(url + '/evidence/read', json={
                    'tool': 'search_embedded_repository', 'arguments': {
                        'project_id': 'installed-fixture', 'query': 'helper', 'mode': 'text', 'limit': 2}})
                response.raise_for_status()
                assert response.json()
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    return {'python': sys.version.split()[0], 'installed_modules_verified': True,
            'files_indexed': len(publication['manifest']['files']),
            'native_vector_search': True, 'fresh_daemon_rest_source_and_text_search': True,
            'source_hash_verified': True, 'explicit_runtime_paths_verified': True,
            'network_policy': 'Python socket audit guards allow only disposable REST loopback connections',
            'embedding': 'Synthetic vectors; no quality or resident-model identity claim',
            'versions': {name: version(name) for name in ('rest-proxy', 'ladybug', 'lancedb',
                                                         'tree-sitter-language-pack')}}


if __name__ == '__main__':
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='installed-embedded-') as directory:
        result = asyncio.run(check(Path(directory).resolve()))
    result['seconds'] = round(time.monotonic() - started, 3)
    print(json.dumps(result, indent=2))

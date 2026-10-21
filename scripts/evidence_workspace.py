"""Run one explicit loopback evidence viewer; never activate existing services."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import stat

from graphrag_core.evidence_workspace.server import create_app
from graphrag_core.evidence_workspace.demo import demo_package
from graphrag_core.evidence_workspace.contracts import WorkspaceError


def _write_launch(path: Path, url: str) -> tuple[int, int]:
    """Exclusively own one launch file, refusing stale files and other launches."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError as error:
        raise WorkspaceError('launch.json already exists; stop its viewer first. '
                             'For a crashed viewer, remove this file only after confirming it has stopped.') from error
    with os.fdopen(descriptor, 'w') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise WorkspaceError('Launch file must be a private regular file')
        os.fchmod(stream.fileno(), 0o600)
        json.dump({'url': url, 'pid': os.getpid()}, stream)
    return info.st_dev, info.st_ino


def _clear_launch(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
        if (info.st_dev, info.st_ino) == identity:
            path.unlink()
    except FileNotFoundError:
        pass


def _terminate(signum, frame) -> None:
    # Uvicorn re-raises a captured signal after gracefully closing its server.
    # Turn SIGTERM into a Python exit so this CLI's finally can clear its file.
    raise SystemExit(128 + signum)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, help='Absolute canonical private directory, mode 700')
    parser.add_argument('--port', type=int, default=0, help='Loopback port, 0 chooses an unused port')
    parser.add_argument('--demo', action='store_true', help='Explicitly import synthetic example records')
    options = parser.parse_args()
    if not 0 <= options.port <= 65535:
        parser.error('Port must be 0..65535')
    state = Path(options.state).expanduser()
    if not state.is_absolute():
        parser.error('State must be an absolute private path')
    listener = socket.socket()
    listener.bind(('127.0.0.1', options.port))
    port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    app = create_app(str(state), token, f'127.0.0.1:{port}')
    if options.demo:
        app.state.store.import_package(demo_package())
    launch = state / 'launch.json'
    # This one private local file conveys the ephemeral capability without printing
    # it, embedding it in an HTTP request/query, or adding it to recovery packets.
    try:
        identity = _write_launch(launch, f'http://127.0.0.1:{port}/#token={token}')
    except WorkspaceError as error:
        listener.close()
        parser.error(str(error))
    print(f'Evidence viewer listening at http://127.0.0.1:{port}/', flush=True)
    print('Open the URL from the private launch.json file; it contains a per-launch capability.', flush=True)
    previous_term = signal.signal(signal.SIGTERM, _terminate)
    try:
        import uvicorn
        uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='warning')).run(sockets=[listener])
    finally:
        listener.close()
        _clear_launch(launch, identity)
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == '__main__':
    main()

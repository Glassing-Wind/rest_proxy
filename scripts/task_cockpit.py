"""Serve one existing task's status on loopback, or create an explicit synthetic demo."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import signal
import socket
import sqlite3
import stat
import tempfile

from graphrag_core.evidence_workspace.contracts import WorkspaceError
from graphrag_core.task_cockpit.server import create_app
from scripts.evidence_workspace import _clear_launch, _terminate, _write_launch


def _runtime(path: str) -> Path:
    """Create only an explicit private runtime; never repair an existing directory."""
    root = Path(path).expanduser()
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('Runtime must be an absolute canonical path without symlinks')
    if not root.exists():
        root.mkdir(mode=0o700)  # Parent must already exist; no unexpected directory tree.
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Runtime must be an owner-controlled private directory (mode 700)')
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, help='Separate absolute private launch directory, mode 700')
    parser.add_argument('--state', help='Existing absolute private task registry directory')
    parser.add_argument('--project', help='One exact existing project identifier')
    parser.add_argument('--task', help='One exact existing task identifier')
    parser.add_argument('--port', type=int, default=0, help='127.0.0.1 port; 0 selects an unused port')
    parser.add_argument('--demo', action='store_true', help='Create only new synthetic state inside runtime')
    options = parser.parse_args()
    if not 0 <= options.port <= 65535:
        parser.error('Port must be 0..65535')
    if options.demo and any((options.state, options.project, options.task)):
        parser.error('Demo cannot be combined with existing task selection')
    if not options.demo and not all((options.state, options.project, options.task)):
        parser.error('Require --state, --project and --task, or explicit --demo')

    listener = socket.socket()
    identity = None
    previous_term = None
    launch = None
    try:
        runtime = Path(options.runtime).expanduser()
        if not options.demo:
            state = Path(options.state).expanduser()
            if not state.is_absolute() or state.resolve() != state:
                raise ValueError('State must be an absolute canonical path without symlinks')
            if runtime == state or state in runtime.parents or runtime in state.parents:
                raise ValueError('Runtime and registry must be separate directory trees')
        runtime = _runtime(options.runtime)
        launch = runtime / 'launch.json'
        if os.path.lexists(launch):
            raise ValueError('launch.json already exists; confirm its viewer has stopped before removing it')
        if options.demo:
            # This is the only writing path and is explicitly synthetic. Never reuse
            # an existing registry or workspace, even after an earlier demo stopped.
            from memory.task_registry import TaskRegistry
            demo = Path(tempfile.mkdtemp(prefix='synthetic-', dir=runtime))
            workspace = demo / 'workspace'
            workspace.mkdir(mode=0o700)
            state = demo / 'registry'
            registry = TaskRegistry(str(state))
            project = 'cockpit-demo'
            task = registry.create(project, str(workspace), 'Synthetic task-status demonstration',
                                   [], ['read_source'])
            task_id = task['id']
        else:
            project, task_id = options.project, options.task
        listener.bind(('127.0.0.1', options.port))
        port = listener.getsockname()[1]
        token = secrets.token_urlsafe(32)
        app = create_app(str(state), project, task_id, token, f'127.0.0.1:{port}')
        app.state.reader.status()  # Validate the selected task before announcing readiness.
        identity = _write_launch(launch, f'http://127.0.0.1:{port}/#token={token}')
        print(f'Task Cockpit listening at http://127.0.0.1:{port}/', flush=True)
        print(f'Open the capability URL in the private file: {launch}', flush=True)
        previous_term = signal.signal(signal.SIGTERM, _terminate)
        import uvicorn
        uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='warning',
                                      proxy_headers=False)).run(sockets=[listener])
    except (OSError, ValueError, LookupError, WorkspaceError, sqlite3.Error) as error:
        # No source data, task payload or capability belongs in launch diagnostics.
        parser.error(f'Cannot launch Task Cockpit ({type(error).__name__}); check private paths and task selection')
    finally:
        listener.close()
        if identity is not None:
            _clear_launch(launch, identity)
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)


if __name__ == '__main__':
    main()

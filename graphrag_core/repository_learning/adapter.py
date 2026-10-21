"""Opt-in subprocess boundary for RepoAnalyzer's indexed profile APIs.

The configured checkout is trusted administrator code, not request-selected code.
Nothing from that checkout is imported into the REST/MCP application's interpreter.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import signal
import sys
import tempfile

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESULT_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 48 * 1024
MAX_STDERR_BYTES = 16 * 1024
MAX_PLAINTEXT_BYTES = 16 * 1024
DEFAULT_TIMEOUT_SECONDS = 15.0
_BACKEND_MARKERS = (
    'learning_core/service.py', 'learning_core/models.py',
    'context_adapters/graphrag_brain.py', 'context_adapters/models.py',
)
_ERROR_CODES = frozenset({
    'disabled', 'backend_unavailable', 'invalid_configuration', 'invalid_request',
    'input_too_large', 'output_too_large', 'timeout', 'invalid_response',
    'missing_dependency', 'unsupported_backend', 'backend_failed',
    'runtime_initialization_refused', 'network_access_refused',
    'filesystem_write_refused',
})


class _OutputLimitError(Exception):
    pass


def _failure(code: str) -> dict:
    return {'ok': False, 'error': code}


class RepoAnalyzerAdapter:
    """Run bounded read-only profile/comparison requests in a disposable process."""

    _backend_markers = _BACKEND_MARKERS
    _backend_name = 'RepoAnalyzer'
    _error_codes = _ERROR_CODES

    def __init__(self, root: str | Path | None = None, python: str | Path | None = None,
                 *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self.root = str(root) if root else None
        self.python = str(python) if python else None
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> 'RepoAnalyzerAdapter':
        """A configured administrator checkout opts in; unset defaults to disabled."""
        return cls(root=os.getenv('LM_PROXY_REPOANALYZER_ROOT') or None,
                   python=os.getenv('LM_PROXY_REPOANALYZER_PYTHON') or None)

    def availability(self) -> dict:
        """Describe configuration without importing or starting the optional backend."""
        if self.root is None:
            return {'status': 'disabled'}
        error = self._configuration_error()
        return {'status': 'configured', **({'error': error} if error else {})}

    def _configuration_error(self) -> str | None:
        try:
            root = Path(self.root or '')
            timeout = self.timeout_seconds
            if (not root.is_absolute() or isinstance(timeout, bool)
                    or not isinstance(timeout, (int, float)) or not math.isfinite(timeout)
                    or not 0.1 <= timeout <= 30):
                return 'invalid_configuration'
            if not root.is_dir() or not all((root / name).is_file() for name in self._backend_markers):
                return 'backend_unavailable'
            executable = self._python_path()
            if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
                return 'invalid_configuration'
        except (OSError, ValueError):
            return 'invalid_configuration'
        return None

    def _python_path(self) -> Path:
        if self.python is not None:
            return Path(self.python)
        candidate = Path(self.root or '') / '.venv' / 'bin' / 'python'
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        # Preserve the virtualenv executable path instead of resolving its symlink.
        return Path(sys.executable).absolute()

    def _worker_path(self) -> Path:
        return Path(__file__).with_name('worker.py').absolute()

    async def analyze(self, operation: str, *, payload: dict | None = None,
                      reference_payload: dict | None = None,
                      target_payload: dict | None = None) -> dict:
        """Return a protocol result or a sanitized fixed-code optional-backend failure."""
        if self.root is None:
            return _failure('disabled')
        error = self._configuration_error()
        if error:
            return _failure(error)
        if operation == 'profile' and isinstance(payload, dict):
            request = {'protocol_version': PROTOCOL_VERSION, 'operation': operation, 'payload': payload}
        elif operation == 'plaintext_ast' and isinstance(payload, dict):
            text = payload.get('text')
            if not isinstance(text, str):
                return _failure('invalid_request')
            try:
                if len(text.encode('utf-8')) > MAX_PLAINTEXT_BYTES:
                    return _failure('input_too_large')
            except UnicodeError:
                return _failure('invalid_request')
            request = {'protocol_version': PROTOCOL_VERSION, 'operation': operation, 'payload': payload}
        elif (operation == 'compare' and isinstance(reference_payload, dict)
              and isinstance(target_payload, dict)):
            request = {'protocol_version': PROTOCOL_VERSION, 'operation': operation,
                       'reference_payload': reference_payload, 'target_payload': target_payload}
        else:
            return _failure('invalid_request')
        return await self._execute_request(request)

    async def _execute_request(self, request: dict) -> dict:
        """Share only bounded process execution with another explicit backend adapter."""
        if self.root is None:
            return _failure('disabled')
        error = self._configuration_error()
        if error:
            return _failure(error)
        try:
            encoded = json.dumps(request, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (TypeError, ValueError, RecursionError):
            return _failure('invalid_request')
        if len(encoded) > MAX_REQUEST_BYTES:
            return _failure('input_too_large')
        process = None
        tasks = []
        try:
            with tempfile.TemporaryDirectory(prefix='rest-proxy-learning-') as directory:
                worker = self._worker_path()
                # Retain ordinary user identity, but do not send service/provider secrets.
                env = {key: os.environ[key] for key in ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG')
                       if key in os.environ}
                env['PYTHONDONTWRITEBYTECODE'] = '1'
                process = await asyncio.create_subprocess_exec(
                    str(self._python_path()), '-I', '-B', '-X', 'utf8', '-X',
                    f'pycache_prefix={directory}/pycache', str(worker), str(Path(self.root).resolve()),
                    cwd=directory, env=env, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=(os.name == 'posix'),
                )
                tasks = [asyncio.create_task(self._write_request(process, encoded)),
                         asyncio.create_task(self._read_bounded(process.stdout, MAX_RESPONSE_BYTES)),
                         asyncio.create_task(self._drain_stderr(process.stderr)),
                         asyncio.create_task(process.wait())]
                try:
                    outcomes = await asyncio.wait_for(asyncio.gather(*tasks), self.timeout_seconds)
                finally:
                    await self._stop_process(process)
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                if outcomes[3] != 0:
                    return _failure('backend_failed')
                return self._validate_response(outcomes[1])
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return _failure('timeout')
        except _OutputLimitError:
            return _failure('output_too_large')
        except (OSError, ValueError):
            return _failure('backend_unavailable')
        except Exception:
            return _failure('backend_failed')

    @staticmethod
    async def _write_request(process, encoded: bytes) -> None:
        try:
            process.stdin.write(encoded)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

    @staticmethod
    async def _read_bounded(stream, limit: int) -> bytes:
        data = bytearray()
        while chunk := await stream.read(min(8192, limit + 1 - len(data))):
            data.extend(chunk)
            if len(data) > limit:
                raise _OutputLimitError
        return bytes(data)

    @staticmethod
    async def _drain_stderr(stream) -> None:
        # Discard diagnostics; neither raw errors nor checkout paths enter responses.
        retained = 0
        while chunk := await stream.read(8192):
            retained = min(MAX_STDERR_BYTES, retained + len(chunk))

    @staticmethod
    async def _stop_process(process) -> None:
        try:
            if os.name == 'posix':
                # Descendants may still hold pipes after the leader has exited.
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        if process.returncode is None:
            await process.wait()

    @classmethod
    def _validate_response(cls, encoded: bytes) -> dict:
        try:
            value = json.loads(encoded, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict) or value.get('protocol_version') != PROTOCOL_VERSION:
                return _failure('invalid_response')
            if value.get('ok') is False:
                code = value.get('error')
                return _failure(code if code in cls._error_codes else 'backend_failed')
            if value.get('ok') is not True or not isinstance(value.get('result'), dict):
                return _failure('invalid_response')
            backend = value.get('backend')
            if not isinstance(backend, dict) or backend.get('name') != cls._backend_name:
                return _failure('invalid_response')
            if len(json.dumps(value['result'], separators=(',', ':'), ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
                return _failure('output_too_large')
            return {'ok': True, 'result': value['result'], 'backend': backend}
        except (TypeError, ValueError, RecursionError):
            return _failure('invalid_response')

"""Invoke fixed genuine GithubAnalyzer components in disposable source/log scratch.

Only administrator-selected source is executable. Namespace package parents avoid
the broad project startup; every executable component/dependency is an unchanged
copy of a fixed source file. This is a narrow API/write guard, not an OS sandbox
for arbitrary administrator code.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, is_dataclass
import hashlib
import importlib.util
from importlib.machinery import ModuleSpec
import io
import json
import os
from pathlib import Path
import sys
import types

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 256 * 1024
MAX_TEXT_BYTES = 16 * 1024
MAX_RESULT_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 48 * 1024
MAX_ITEMS = 256
MAX_SOURCE_BYTES = 2 * 1024 * 1024
COMPONENT_FILES = (
    'src/GithubAnalyzer/services/parsers/core/custom_parsers.py',
    'src/GithubAnalyzer/models/core/parsers.py',
    'src/GithubAnalyzer/models/core/base_model.py',
    'src/GithubAnalyzer/services/core/base_service.py',
    'src/GithubAnalyzer/utils/logging/__init__.py',
)
_scratch = None
_network_attempted = False
_external_write_attempted = False


class _NetworkRefused(Exception):
    pass


class _ExternalWriteRefused(Exception):
    pass


class _DiscardStream(io.TextIOBase):
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        pass


def _allow_write(path) -> None:
    if isinstance(path, int) and path in {1, 2}:
        return
    if _scratch is not None and isinstance(path, (str, bytes, os.PathLike)):
        target = Path(os.fsdecode(path)).resolve()
        if target.is_relative_to(_scratch):
            return
    global _external_write_attempted
    _external_write_attempted = True
    raise _ExternalWriteRefused


def _audit(event: str, args) -> None:
    global _network_attempted
    if event.startswith(('socket.connect', 'socket.bind', 'socket.getaddrinfo',
                         'socket.sendto', 'socket.sendmsg', 'subprocess.Popen',
                         'os.system', 'os.exec', 'os.posix_spawn', 'os.spawn')):
        _network_attempted = True
        raise _NetworkRefused
    if event == 'open':
        path, mode, flags = args
        if ((isinstance(mode, str) and any(char in mode for char in 'wax+'))
                or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            _allow_write(path)
    elif event in {'os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.truncate'}:
        _allow_write(args[0])
    elif event in {'os.rename', 'os.link', 'os.symlink'}:
        _allow_write(args[0])
        _allow_write(args[1])


def _source_bytes(root: Path) -> dict[str, bytes]:
    sources = {}
    for relative in COMPONENT_FILES:
        path = root / relative
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError('Invalid component source')
        with path.open('rb') as stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError('Component source exceeds limit')
        raw.decode('utf-8')
        sources[relative] = raw
    return sources


def _stage(sources: dict[str, bytes]) -> Path:
    for relative, raw in sources.items():
        destination = _scratch / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    package = _scratch / 'src' / 'GithubAnalyzer'
    # These are package metadata paths, not fake parser, database or logging APIs.
    for suffix in ('', '.models', '.models.core', '.services', '.services.core',
                   '.services.parsers', '.services.parsers.core', '.utils'):
        name = 'GithubAnalyzer' + suffix
        module = types.ModuleType(name)
        module.__path__ = [str(package / suffix.lstrip('.').replace('.', '/'))]
        module.__package__ = name
        module.__spec__ = ModuleSpec(name, loader=None, is_package=True)
        sys.modules[name] = module
        if '.' in name:
            parent, child = name.rsplit('.', 1)
            setattr(sys.modules[parent], child, module)
    return package


def _load_component(package: Path):
    name = 'GithubAnalyzer.services.parsers.core.custom_parsers'
    spec = importlib.util.spec_from_file_location(name, package / 'services/parsers/core/custom_parsers.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _identity(sources: dict[str, bytes], parser: str) -> dict:
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}
    loaded = {}
    for name, module in tuple(sys.modules.items()):
        if not name.startswith('GithubAnalyzer'):
            continue
        if '.database' in name or '.db.' in name:
            raise RuntimeError('Database import refused')
        filename = getattr(module, '__file__', None)
        if filename is None:
            continue
        path = Path(filename).resolve()
        if not path.is_relative_to(_scratch):
            raise RuntimeError('Unstaged component import')
        relative = path.relative_to(_scratch).as_posix()
        if relative not in sources or path.read_bytes() != sources[relative]:
            raise RuntimeError('Component source identity mismatch')
        loaded[relative] = hashes[relative]
    encoded = json.dumps(loaded, sort_keys=True, separators=(',', ':')).encode()
    return {'name': 'GithubAnalyzer', 'api': 'typed-configuration-v1',
            'component': 'RequirementsParser' if parser == 'requirements' else 'GitignoreParser',
            'source_hashes': hashes, 'loaded_source_sha256': hashlib.sha256(encoded).hexdigest(),
            'loaded_source_files': len(loaded), 'python_version': sys.version.split()[0],
            'runtime_initialized': False, 'network_access': 'denied', 'external_writes': 'denied',
            'package_startup': 'skipped', 'import_boundary': 'fixed-unmodified-component-copies'}


def _execute(payload: dict, root: Path) -> dict:
    sources = _source_bytes(root)
    component = _load_component(_stage(sources))
    parser = payload['parser']
    target = component.RequirementsParser() if parser == 'requirements' else component.GitignoreParser()
    parsed = target.parse(payload['text'])
    if _network_attempted:
        raise _NetworkRefused
    if _external_write_attempted:
        raise _ExternalWriteRefused
    if not is_dataclass(parsed):
        return {'ok': False, 'error': 'invalid_response'}
    observations = asdict(parsed)
    if observations.get('is_valid') is not True:
        return {'ok': False, 'error': 'invalid_response'}
    groups = ('requirements', 'constraints') if parser == 'requirements' else ('patterns',)
    if any(not isinstance(observations.get(group), list) for group in groups):
        return {'ok': False, 'error': 'invalid_response'}
    if sum(len(observations[group]) for group in groups) > MAX_ITEMS:
        return {'ok': False, 'error': 'output_too_large'}
    if _source_bytes(root) != sources:
        return {'ok': False, 'error': 'backend_failed'}
    backend = _identity(sources, parser)
    limitations = [
        'Caller-supplied text; not a published or current-file repository observation.',
        'No repository acquisition, dependency solving or effective ignore evaluation.',
    ]
    limitations.append(
        'Actual parser recognizes simple >=, ==, <= and editable declarations; not complete PEP 508 parsing.'
        if parser == 'requirements' else
        'Negation/directory flags preserve component behavior, including stripping repeated leading exclamation marks.'
    )
    result = {'source_id': payload['source_id'],
              'source_sha256': hashlib.sha256(payload['text'].encode()).hexdigest(), 'parser': parser,
              'metadata': {'project_id': payload['project_id'], 'source_kind': 'provided-text'},
              'observations': observations, 'limitations': limitations}
    if len(json.dumps(result, separators=(',', ':'), allow_nan=False, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
        return {'ok': False, 'error': 'output_too_large'}
    return {'ok': True, 'result': result, 'backend': backend}


def _valid_payload(value) -> bool:
    if not isinstance(value, dict) or set(value) != {'project_id', 'source_id', 'parser', 'text'}:
        return False
    project, source, parser, text = (value[key] for key in ('project_id', 'source_id', 'parser', 'text'))
    return (isinstance(project, str) and 1 <= len(project) <= 128 and ':' not in project and '\0' not in project
            and isinstance(source, str) and 1 <= len(source) <= 256 and '\0' not in source
            and parser in {'requirements', 'gitignore'} and isinstance(text, str)
            and 1 <= len(text.encode()) <= MAX_TEXT_BYTES and '\0' not in text)


def main() -> None:
    global _scratch
    output = sys.stdout
    response = {'ok': False, 'error': 'invalid_request'}
    sys.dont_write_bytecode = True
    _scratch = (Path.cwd() / 'github-components').resolve()
    sys.addaudithook(_audit)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            response = {'ok': False, 'error': 'input_too_large'}
        else:
            request = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if (isinstance(request, dict) and request.get('protocol_version') == PROTOCOL_VERSION
                    and request.get('operation') == 'configuration' and _valid_payload(request.get('payload'))
                    and len(sys.argv) == 2):
                root = Path(sys.argv[1]).resolve(strict=True)
                with redirect_stdout(_DiscardStream()), redirect_stderr(_DiscardStream()):
                    response = _execute(request['payload'], root)
    except ModuleNotFoundError:
        response = {'ok': False, 'error': 'missing_dependency'}
    except _NetworkRefused:
        response = {'ok': False, 'error': 'network_access_refused'}
    except _ExternalWriteRefused:
        response = {'ok': False, 'error': 'filesystem_write_refused'}
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        response = {'ok': False, 'error': 'invalid_request'}
    except Exception:
        response = {'ok': False, 'error': 'backend_failed'}
    response['protocol_version'] = PROTOCOL_VERSION
    encoded = json.dumps(response, separators=(',', ':'), allow_nan=False, ensure_ascii=False)
    if len(encoded.encode()) > MAX_RESPONSE_BYTES:
        encoded = json.dumps({'protocol_version': PROTOCOL_VERSION, 'ok': False, 'error': 'output_too_large'})
    output.write(encoded + '\n')
    output.flush()


if __name__ == '__main__':
    main()

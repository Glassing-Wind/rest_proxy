"""One-request worker for trusted RepoAnalyzer code over normalized indexed facts.

Run only through the opt-in adapter. This is process isolation and a narrow API
guard, not an operating-system sandbox for arbitrary administrator checkout code.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESULT_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 48 * 1024
MAX_PLAINTEXT_BYTES = 16 * 1024
MAX_PLAINTEXT_NODES = 128
_IDENTITY_FILES = (
    'learning_core/service.py', 'learning_core/models.py', 'learning_core/framework_profiles.py',
    'context_adapters/graphrag_brain.py', 'context_adapters/models.py',
    'ingestion/local_repository.py',
)
_PLAINTEXT_IDENTITY_FILES = (
    'parsers/custom_parsers/custom_plaintext_parser.py', 'parsers/query_patterns/plaintext.py',
    'parsers/query_patterns/enhanced_patterns_custom.py', 'parsers/base_parser.py',
)
_network_attempted = False
_runtime_attempted = False
_write_attempted = False


class _RuntimeRefused(Exception):
    pass


class _NetworkRefused(Exception):
    pass


class _WriteRefused(Exception):
    pass


class _DiscardStream(io.TextIOBase):
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        pass


def _audit(event: str, _args) -> None:
    global _network_attempted, _write_attempted
    if event.startswith(('socket.connect', 'socket.bind', 'socket.getaddrinfo',
                         'socket.sendto', 'socket.sendmsg', 'subprocess.Popen',
                         'os.system', 'os.exec', 'os.posix_spawn', 'os.spawn')):
        _network_attempted = True
        raise _NetworkRefused
    cwd = Path.cwd().resolve()
    if event == 'open' and isinstance(_args[0], (str, bytes)):
        flags = _args[2]
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            path = Path(os.fsdecode(_args[0])).resolve()
            if not path.is_relative_to(cwd):
                _write_attempted = True
                raise _WriteRefused
    elif event == 'os.mkdir' and isinstance(_args[0], (str, bytes)):
        path = Path(os.fsdecode(_args[0])).resolve()
        # Existing import-time makedirs(exist_ok=True) attempts do not mutate.
        if not path.is_relative_to(cwd) and not path.is_dir():
            _write_attempted = True
            raise _WriteRefused
    elif event in {'os.remove', 'os.rmdir', 'os.chmod', 'os.chown', 'os.utime', 'os.truncate',
                   'os.rename', 'os.link', 'os.symlink'}:
        paths = _args[:2] if event in {'os.rename', 'os.link', 'os.symlink'} else _args[:1]
        for value in paths:
            if isinstance(value, (str, bytes)) and not Path(os.fsdecode(value)).resolve().is_relative_to(cwd):
                _write_attempted = True
                raise _WriteRefused


async def _forbid_runtime(*_args, **_kwargs):
    global _runtime_attempted
    _runtime_attempted = True
    raise _RuntimeRefused


def _json_default(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError('unsupported result type')


def _backend_identity(root: Path, *, plaintext: bool = False) -> dict:
    hashes = {}
    for name in _IDENTITY_FILES + (_PLAINTEXT_IDENTITY_FILES if plaintext else ()):
        path = root / name
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise RuntimeError('missing backend source')
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    loaded = {}
    for module in tuple(sys.modules.values()):
        filename = getattr(module, '__file__', None)
        if not filename:
            continue
        path = Path(filename).resolve()
        if path.suffix != '.py' or not path.is_relative_to(root):
            continue
        relative = path.relative_to(root)
        if any(part in {'.venv', 'venv', 'site-packages'} for part in relative.parts):
            continue
        name = relative.as_posix()
        if path.stat().st_size > 2 * 1024 * 1024 or len(loaded) >= 256:
            raise RuntimeError('backend source identity too large')
        loaded[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    canonical = json.dumps(loaded, sort_keys=True, separators=(',', ':')).encode()
    return {'name': 'RepoAnalyzer', 'api': 'plaintext-pattern-regex-v1' if plaintext else 'indexed-payload-v1',
            'source_hashes': hashes, 'loaded_source_sha256': hashlib.sha256(canonical).hexdigest(),
            'loaded_source_files': len(loaded), 'python_version': sys.version.split()[0],
            'runtime_initialized': False, 'network_access': 'denied',
            'external_writes': 'denied', **({'parser_backend': 'custom'} if plaintext else {})}


def _validate_payload(value) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get('files'), list):
        return False
    if not value['files'] or len(value['files']) > 1000:
        return False
    for file in value['files']:
        if (not isinstance(file, dict) or not isinstance(file.get('path'), str)
                or not isinstance(file.get('symbols', []), list)):
            return False
        if any(not isinstance(symbol, dict) for symbol in file.get('symbols', [])):
            return False
    return True


def _validate_plaintext_payload(value) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get('text'), str):
        return False
    text = value['text']
    if len(text.encode('utf-8')) > MAX_PLAINTEXT_BYTES:
        return False
    source_id = value.get('source_id')
    metadata = value.get('metadata')
    return (isinstance(source_id, str) and 0 < len(source_id) <= 512
            and value.get('source_sha256') == hashlib.sha256(text.encode('utf-8')).hexdigest()
            and isinstance(metadata, dict) and metadata.get('source_kind') == 'provided-text'
            and isinstance(metadata.get('project_id'), str) and bool(metadata['project_id']))


def _point(text: str, offset: int) -> list[int]:
    prefix = text[:offset]
    return [prefix.count('\n'), len(prefix.rsplit('\n', 1)[-1])]


def _check_guards() -> None:
    if _network_attempted:
        raise _NetworkRefused
    if _runtime_attempted:
        raise _RuntimeRefused
    if _write_attempted:
        raise _WriteRefused


async def _plaintext_ast(request: dict, root: Path) -> dict:
    from parsers.base_parser import BaseParser
    from parsers.custom_parsers.custom_plaintext_parser import PlaintextParser
    from parsers.query_patterns.enhanced_patterns_custom import AdaptivePattern
    from parsers.query_patterns.plaintext import ENHANCED_PATTERNS, PATTERN_RELATIONSHIPS

    # The actual authored regex extractor and node factory are the approved slice.
    # Calling the full parser or adaptive matcher would start DB/AI machinery.
    for cls in (BaseParser, PlaintextParser, AdaptivePattern):
        cls.initialize = _forbid_runtime
    PlaintextParser._parse_source = _forbid_runtime
    AdaptivePattern.matches = _forbid_runtime
    parser = PlaintextParser()
    _check_guards()
    before = _backend_identity(root, plaintext=True)
    payload = request['payload']
    text = payload['text']
    children = []
    pattern_counts = {}
    for category, patterns in ENHANCED_PATTERNS.items():
        for name, pattern in patterns.items():
            matches = await pattern._regex_matches(text)
            for match in matches:
                if len(children) >= MAX_PLAINTEXT_NODES:
                    return {'ok': False, 'error': 'output_too_large'}
                start, end = match['start'], match['end']
                if (not isinstance(start, int) or not isinstance(end, int)
                        or not 0 <= start <= end <= len(text)):
                    raise RuntimeError('backend returned invalid source span')
                kind = match.get('type', name)
                extracted = {key: value for key, value in match.items() if key != 'match'}
                templates = PATTERN_RELATIONSHIPS.get(kind, {})
                node = parser._create_node(kind, _point(text, start), _point(text, end),
                                           content=text[start:end], metadata=extracted,
                                           pattern_relationships=templates)
                node.update({'kind': kind, 'pattern_name': name, 'pattern_category': category.value,
                             'point_units': 'unicode-code-points',
                             'start_char': start, 'end_char': end,
                             'start_byte': len(text[:start].encode('utf-8')),
                             'end_byte': len(text[:end].encode('utf-8')),
                             'relationship_templates': templates})
                children.append(node)
                pattern_counts[name] = pattern_counts.get(name, 0) + 1
    document = parser._create_node('document', [0, 0], _point(text, len(text)), content=text)
    document.update({'start_char': 0, 'end_char': len(text), 'start_byte': 0,
                     'end_byte': len(text.encode('utf-8')), 'children': children,
                     'point_units': 'unicode-code-points'})
    _check_guards()
    if parser._initialized:
        raise _RuntimeRefused
    after = _backend_identity(root, plaintext=True)
    if before != after:
        raise RuntimeError('backend source identity changed')
    result = {'mode': 'pattern-regex-slice', 'source_id': payload['source_id'],
              'source_sha256': payload['source_sha256'], 'metadata': payload['metadata'],
              'ast': document, 'counts': {'nodes': len(children), 'patterns': pattern_counts},
              'limitations': [
                  'Uses authored pattern regex extraction and the actual node factory; full parser, adaptation and AI initialization are excluded.',
                  'MULTILINE|DOTALL behavior is retained; list and metadata matches may span subsequent lines.',
                  'Matches can overlap, including inline code within fenced code blocks.',
                  'Relationship templates name pattern kinds; they are not resolved graph or runtime edges.',
                  'Unmatched plaintext remains in document content; paragraph coverage is not implied.',
              ]}
    return _bounded_success(result, after)


def _bounded_success(result: dict, backend: dict) -> dict:
    encoded = json.dumps(result, separators=(',', ':'), ensure_ascii=False,
                         allow_nan=False, default=_json_default).encode()
    if len(encoded) > MAX_RESULT_BYTES:
        return {'ok': False, 'error': 'output_too_large'}
    return {'ok': True, 'result': result, 'backend': backend}


async def _execute(request: dict, root: Path) -> dict:
    # Only this disposable process gets the administrator-selected import path.
    sys.path.insert(0, str(root))
    if request['operation'] == 'plaintext_ast':
        return await _plaintext_ast(request, root)
    from learning_core.service import RepositoryLearningService

    RepositoryLearningService._get_runtime = _forbid_runtime
    RepositoryLearningService.ensure_initialized = _forbid_runtime
    before = _backend_identity(root)
    service = RepositoryLearningService()
    if request['operation'] == 'profile':
        result = await service.build_repository_profile_from_indexed_payload(request['payload'])
    else:
        result = await service.compare_repository_profiles_from_indexed_payloads(
            reference_payload=request['reference_payload'], target_payload=request['target_payload'],
        )
    _check_guards()
    if _runtime_attempted or service.runtime is not None or service.reference_learning is not None:
        raise _RuntimeRefused
    after = _backend_identity(root)
    if before != after:
        raise RuntimeError('backend source identity changed')
    if not is_dataclass(result):
        raise RuntimeError('backend API did not return a typed result')
    result = asdict(result)
    return _bounded_success(result, after)


def main() -> None:
    """Read bounded JSON, run one approved API, and emit JSON without backend logs."""
    response = {'ok': False, 'error': 'invalid_request'}
    output = sys.stdout
    sys.dont_write_bytecode = True
    # Native cache setup probes tempfile locations; keep those probes disposable.
    tempfile.tempdir = str(Path.cwd())
    sys.addaudithook(_audit)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            response = {'ok': False, 'error': 'input_too_large'}
        else:
            request = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if (isinstance(request, dict) and request.get('protocol_version') == PROTOCOL_VERSION
                    and request.get('operation') in {'profile', 'compare', 'plaintext_ast'}):
                payloads = ([request.get('payload')] if request['operation'] in {'profile', 'plaintext_ast'}
                            else [request.get('reference_payload'), request.get('target_payload')])
                validate = _validate_plaintext_payload if request['operation'] == 'plaintext_ast' else _validate_payload
                if all(validate(payload) for payload in payloads) and len(sys.argv) == 2:
                    root = Path(sys.argv[1]).resolve(strict=True)
                    with redirect_stdout(_DiscardStream()), redirect_stderr(_DiscardStream()):
                        response = asyncio.run(_execute(request, root))
    except ModuleNotFoundError:
        response = {'ok': False, 'error': 'missing_dependency'}
    except ImportError:
        response = {'ok': False, 'error': 'unsupported_backend'}
    except _RuntimeRefused:
        response = {'ok': False, 'error': 'runtime_initialization_refused'}
    except _NetworkRefused:
        response = {'ok': False, 'error': 'network_access_refused'}
    except _WriteRefused:
        response = {'ok': False, 'error': 'filesystem_write_refused'}
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        response = {'ok': False, 'error': 'invalid_request'}
    except Exception:
        response = {'ok': False, 'error': 'backend_failed'}
    response['protocol_version'] = PROTOCOL_VERSION
    encoded = json.dumps(response, separators=(',', ':'), ensure_ascii=False,
                         allow_nan=False, default=_json_default)
    if len(encoded.encode()) > MAX_RESPONSE_BYTES:
        encoded = json.dumps({'protocol_version': PROTOCOL_VERSION, 'ok': False, 'error': 'output_too_large'})
    output.write(encoded + '\n')
    output.flush()


if __name__ == '__main__':
    main()

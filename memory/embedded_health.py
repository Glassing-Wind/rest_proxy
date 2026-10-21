"""Read-only health for one embedded publication; no model or external server use."""
from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
from pathlib import Path
import stat

from graphrag_core.indexing.embedded_outlines import read_outline_publication
from memory.embedded_jobs import read_attempt


_MAX_FILES = 5000
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_DETAILS = 50
_MAX_OUTPUT_BYTES = 48000
_UNKNOWN_STATES = (
    'unreadable', 'scope_changed', 'invalid_path', 'not_regular_file',
    'budget_exceeded', 'changed_during_read', 'safe_reader_unavailable',
)


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _bounded_result(result: dict) -> dict:
    if len(_json(result).encode('utf-8')) > _MAX_OUTPUT_BYTES:
        raise ValueError('Embedded indexing health exceeds the 48000-byte output budget')
    return result


def _failure_state(error: OSError) -> str:
    if isinstance(error, FileNotFoundError):
        return 'missing'
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        return 'scope_changed'
    return 'unreadable'


def _open_directory(path: Path) -> int:
    """Anchor every directory component without following symlinks (POSIX only)."""
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_hash(root_fd: int, relative: Path, remaining: int) -> tuple[str, str | None, int]:
    descriptor = os.dup(root_fd)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            return 'scope_changed', None, 0
        if not stat.S_ISREG(info.st_mode):
            return 'not_regular_file', None, 0
        if info.st_size > _MAX_FILE_BYTES or info.st_size > remaining:
            return 'budget_exceeded', None, 0
        file_fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                          dir_fd=descriptor)
        with os.fdopen(file_fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                return 'not_regular_file', None, 0
            raw = stream.read(min(_MAX_FILE_BYTES, remaining) + 1)
            after = os.fstat(stream.fileno())
        if len(raw) > _MAX_FILE_BYTES or len(raw) > remaining:
            return 'budget_exceeded', None, len(raw)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size,
                                       after.st_mtime_ns, after.st_ctime_ns):
            return 'changed_during_read', None, len(raw)
        return 'hashed', hashlib.sha256(raw).hexdigest(), len(raw)
    except OSError as error:
        return _failure_state(error), None, 0
    finally:
        os.close(descriptor)


def _scan_manifest(root_path: str, files: list[dict]) -> dict:
    counts = dict.fromkeys(('matching', 'modified', 'missing', *_UNKNOWN_STATES), 0)
    details = []
    bytes_read = 0
    root = Path(root_path)
    safe_reader = (os.name == 'posix' and hasattr(os, 'O_NOFOLLOW')
                   and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd)
    root_fd = None
    root_status = 'available'
    if not safe_reader:
        root_status = 'safe_reader_unavailable'
    elif not root.is_absolute() or root.anchor != '/' or '..' in root.parts:
        root_status = 'scope_changed'
    else:
        try:
            root_fd = _open_directory(root)
        except OSError as error:
            root_status = _failure_state(error)
    seen = set()
    try:
        for entry in files:
            path = entry.get('path')
            expected = entry.get('sha256')
            relative = Path(path) if isinstance(path, str) and '\0' not in path else None
            invalid = (relative is None or not relative.parts or relative.is_absolute()
                       or '..' in relative.parts or path != relative.as_posix() or path in seen
                       or not isinstance(expected, str) or len(expected) != 64
                       or any(char not in '0123456789abcdef' for char in expected))
            current = None
            if invalid:
                state = 'invalid_path'
            elif root_status != 'available':
                state = root_status
            else:
                state, current, used = _read_hash(root_fd, relative, max(0, _MAX_TOTAL_BYTES - bytes_read))
                bytes_read += used
                if state == 'hashed':
                    state = 'matching' if current == expected else 'modified'
            if isinstance(path, str):
                seen.add(path)
            counts[state] += 1
            if state != 'matching' and len(details) < _MAX_DETAILS:
                detail = {'path': path[:512] if isinstance(path, str) else None, 'status': state}
                if isinstance(path, str) and len(path) > 512:
                    detail['path_truncated'] = True
                if state == 'modified':
                    detail.update(published_sha256=expected, current_sha256=current)
                details.append(detail)
    finally:
        if root_fd is not None:
            os.close(root_fd)
    unknown = sum(counts[state] for state in _UNKNOWN_STATES)
    stale = counts['modified'] + counts['missing']
    status = ('differs_from_manifest' if stale else 'unknown' if unknown
              else 'matches_manifest' if files else 'empty_manifest')
    return {'status': status, 'scope': 'published-manifest-only', 'root_status': root_status,
            'manifest_files': len(files), 'hashes_checked': counts['matching'] + counts['modified'],
            'checks_complete': unknown == 0, 'counts': counts, 'bytes_read': bytes_read,
            'details': details, 'details_truncated': sum(counts.values()) - counts['matching'] > len(details),
            'unlisted_files_checked': False, 'atomic_filesystem_snapshot': False,
            'limits': {'files': _MAX_FILES, 'file_bytes': _MAX_FILE_BYTES,
                       'total_bytes': _MAX_TOTAL_BYTES, 'issue_details': _MAX_DETAILS}}


async def workspace_indexing_health(owner, workspace_id: str, audit: bool = False) -> dict:
    """Check published manifest hashes and latest attempt under the caller's runtime lock.

    The owner's lock protects the publication/journal reads. Current files are read
    off-thread with explicit bounds and no symlink traversal. The scan is sequential,
    not an atomic checkout snapshot. It neither discovers unlisted files nor validates
    stored parser facts, vectors, resolved graph fidelity or semantic quality.
    """
    if not isinstance(workspace_id, str) or not workspace_id or len(workspace_id) > 4096 or '\0' in workspace_id:
        raise ValueError('Use a nonempty workspace identifier of at most 4096 characters')
    if type(audit) is not bool:
        raise ValueError('audit must be boolean')
    result = {'workspace_id': workspace_id, 'storage_backend': 'embedded',
              'coverage_complete': False, 'model_required': False,
              'audit': {'requested': audit, 'supported': False, 'performed': False,
                        'reason': 'Legacy Level 2 deep audit has no embedded parity',
                        'unsupported_checks': ['parser-fidelity', 'stored-source-integrity',
                                               'vector-integrity', 'resolved-graph-integrity',
                                               'semantic-quality', 'unlisted-workspace-files']}}
    project = await owner.resolve_project(workspace_id)
    if project is None:
        return _bounded_result(dict(result, status='not_published'))
    async with owner._lock:
        owner._require_open()
        publication = await read_outline_publication(owner.graph, project['project_id'])
        attempt = await read_attempt(owner.graph, project['project_id'])
    if (publication is None or publication['run_id'] != project['run_id']
            or attempt['published_run_id'] != project['run_id']):
        return _bounded_result(dict(result, status='publication_changed', project_id=project['project_id']))
    manifest = publication['manifest']
    canonical = dict(manifest)
    recorded_hash = canonical.pop('sha256', None)
    if hashlib.sha256(_json(canonical).encode()).hexdigest() != recorded_hash:
        raise RuntimeError('Published manifest does not match its recorded hash')
    files = manifest.get('files')
    if not isinstance(files, list) or len(files) > _MAX_FILES or any(not isinstance(row, dict) for row in files):
        raise ValueError('Published health manifest must contain at most 5000 file objects')
    result.update(status='published', project_id=project['project_id'], run_id=project['run_id'],
                  workspace_path=project['workspace_path'], matched_by=project.get('matched_by'),
                  manifest_sha256=recorded_hash,
                  publication={'files': len(files), 'symbols': manifest.get('symbols'),
                               'source_bytes': manifest.get('source_bytes'),
                               'retrieval': manifest.get('retrieval'),
                               'relationships': {key: value for key, value in manifest.get('relationships', {}).items()
                                                 if key != 'ids'}},
                  latest_indexing_attempt=attempt,
                  freshness=await asyncio.to_thread(_scan_manifest, project['workspace_path'], files))
    return _bounded_result(result)

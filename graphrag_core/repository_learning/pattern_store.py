"""Private immutable repository-pattern artifacts with explicit revision fencing.

Hashes establish retained artifact consistency, not current-source freshness or
trusted analyzer findings. Source identifiers are labels and are never opened.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Optional
from urllib.parse import quote


MAX_RECORD_BYTES = 512 * 1024
MAX_PROJECTS = 128
MAX_GENERATIONS = 128
MAX_REVISION = (1 << 63) - 2
_TABLE_COLUMNS = {
    'records': {'project_id': 'TEXT', 'revision': 'INTEGER', 'record_id': 'TEXT', 'payload': 'TEXT'},
    'heads': {'project_id': 'TEXT', 'revision': 'INTEGER', 'record_revision': 'INTEGER'},
}
_PRIMARY_KEYS = {'records': {'project_id': 1, 'revision': 2}, 'heads': {'project_id': 1}}


def canonical(value) -> str:
    """Encode detached finite JSON deterministically."""
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError('Pattern artifacts must contain bounded finite JSON') from error


def _identifier(value, maximum: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\0' in value:
        raise ValueError(f'Invalid {label}')
    return value


def _digest(value) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in '0123456789abcdef' for character in value)):
        raise ValueError('Artifact source hashes must be lowercase SHA256')
    return value


def _revision(value, *, positive: bool = False) -> int:
    if type(value) is not int or not (1 if positive else 0) <= value <= MAX_REVISION:
        raise ValueError('Invalid expected artifact revision')
    return value


def validate_record(project_id: str, record: dict) -> tuple[dict, str, str]:
    """Return a detached validated artifact, canonical payload and its identity."""
    _identifier(project_id, 128, 'project ID')
    if not isinstance(record, dict):
        raise ValueError('Pattern artifact must be an object')
    payload = canonical(record)
    if len(payload.encode()) > MAX_RECORD_BYTES:
        raise ValueError('Pattern artifact exceeds 512 KiB')
    detached = json.loads(payload)
    if (type(detached.get('version')) is not int or detached['version'] != 1
            or detached.get('project_id') != project_id
            or detached.get('kind') not in {'repository', 'provided-text', 'configuration'}):
        raise ValueError('Pattern artifact version or project scope is invalid')
    sources, patterns = detached.get('sources'), detached.get('patterns')
    if not isinstance(sources, list) or not 1 <= len(sources) <= 128:
        raise ValueError('Pattern artifact requires 1..128 sources')
    if not isinstance(patterns, list) or len(patterns) > 1024:
        raise ValueError('Pattern artifact permits at most 1024 patterns')
    if not isinstance(detached.get('backend'), dict) or not isinstance(detached.get('analysis'), dict):
        raise ValueError('Pattern artifact requires backend and analysis objects')
    if 'context' in detached and not isinstance(detached['context'], dict):
        raise ValueError('Captured context must be an object')
    known_sources = {}
    for source in sources:
        if (not isinstance(source, dict)
                or source.get('source_kind') not in {'published-source', 'provided-text'}
                or ('project_id' in source and source['project_id'] != project_id)):
            raise ValueError('Pattern source kind or project scope is invalid')
        source_id = _identifier(source.get('source_id'), 512, 'source label')
        digest = _digest(source.get('source_sha256'))
        if source_id in known_sources:
            raise ValueError('Duplicate artifact source label')
        known_sources[source_id] = digest
        for key, value in source.items():
            if key.endswith('_sha256'):
                _digest(value)
    observed = set()
    required = {'key', 'kind', 'value', 'occurrences', 'source_id', 'source_sha256', 'producer'}
    for pattern in patterns:
        if not isinstance(pattern, dict) or not required.issubset(pattern):
            raise ValueError('Malformed normalized pattern')
        key = _identifier(pattern['key'], 256, 'pattern key')
        _identifier(pattern['kind'], 128, 'pattern kind')
        _identifier(pattern['producer'], 256, 'pattern producer')
        source_id = _identifier(pattern['source_id'], 512, 'pattern source label')
        if (source_id not in known_sources or _digest(pattern['source_sha256']) != known_sources[source_id]
                or ('project_id' in pattern and pattern['project_id'] != project_id)):
            raise ValueError('Pattern evidence is outside its source scope')
        if type(pattern['occurrences']) is not int or not 1 <= pattern['occurrences'] <= 100000:
            raise ValueError('Pattern occurrence count must be 1..100000')
        if len(canonical(pattern['value']).encode()) > 4096:
            raise ValueError('Pattern value exceeds 4096 UTF-8 bytes')
        identity = (source_id, key)
        if identity in observed:
            raise ValueError('Duplicate source/pattern key')
        observed.add(identity)
    record_id = hashlib.sha256(payload.encode()).hexdigest()
    return detached, payload, record_id


class RepositoryPatternStore:
    """Synchronous connection-per-operation artifact history and explicit mutations."""

    def __init__(self, directory: str):
        try:
            if isinstance(directory, str) and not directory.strip():
                raise ValueError('Require an explicit pattern state directory')
            path = Path(directory).expanduser()
            if '..' in path.parts:
                raise ValueError('Pattern state paths must not traverse parent directories')
            self.directory = Path(os.path.abspath(path))
        except (TypeError, OSError, RuntimeError) as error:
            raise ValueError('Invalid pattern state directory') from error
        self.path = self.directory / 'learning.sqlite3'
        self._guard_directory()

    def _guard_directory(self) -> bool:
        for component in (*reversed(self.directory.parents), self.directory):
            try:
                information = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(information.st_mode) or not stat.S_ISDIR(information.st_mode):
                raise ValueError('Pattern state directory must not contain symlinks or non-directories')
        if not self.directory.exists():
            return False
        if stat.S_IMODE(self.directory.stat().st_mode) != 0o700:
            raise ValueError('Pattern state directory must have mode 700')
        return True

    def _guard_file(self) -> bool:
        try:
            information = self.path.lstat()
        except FileNotFoundError:
            return False
        if (not stat.S_ISREG(information.st_mode) or information.st_nlink != 1
                or stat.S_IMODE(information.st_mode) != 0o600):
            raise ValueError('Pattern state must be one private regular file with mode 600')
        return True

    def _exists(self) -> bool:
        return self._guard_directory() and self._guard_file()

    @staticmethod
    def _schema(db, *, initialize: bool) -> None:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if initialize and version == 0 and not tables:
            db.execute('CREATE TABLE records (project_id TEXT NOT NULL, revision INTEGER NOT NULL '
                       'CHECK(revision>0), record_id TEXT NOT NULL, payload TEXT NOT NULL, '
                       'PRIMARY KEY(project_id,revision))')
            db.execute('CREATE TABLE heads (project_id TEXT NOT NULL PRIMARY KEY, revision INTEGER NOT NULL '
                       'CHECK(revision>0), record_revision INTEGER, '
                       'FOREIGN KEY(project_id,record_revision) REFERENCES records(project_id,revision))')
            db.execute('PRAGMA user_version=1')
            version, tables = 1, set(_TABLE_COLUMNS)
        if version != 1 or tables != set(_TABLE_COLUMNS):
            raise ValueError('Unsupported or corrupt pattern store schema')
        for table, expected in _TABLE_COLUMNS.items():
            columns = db.execute(f'PRAGMA table_info({table})').fetchall()
            actual = {row[1]: row[2].upper() for row in columns}
            if actual != expected:
                raise ValueError('Unsupported or corrupt pattern store schema')
            for row in columns:
                nullable = table == 'heads' and row[1] == 'record_revision'
                if (row[3] != int(not nullable) or row[4] is not None
                        or row[5] != _PRIMARY_KEYS[table].get(row[1], 0)):
                    raise ValueError('Unsupported or corrupt pattern store constraints')
        foreign_keys = db.execute('PRAGMA foreign_key_list(heads)').fetchall()
        expected_foreign_key = [
            (0, 0, 'records', 'project_id', 'project_id', 'NO ACTION', 'NO ACTION', 'NONE'),
            (0, 1, 'records', 'record_revision', 'revision', 'NO ACTION', 'NO ACTION', 'NONE'),
        ]
        if foreign_keys != expected_foreign_key:
            raise ValueError('Unsupported or corrupt pattern store constraints')

    @contextmanager
    def _connect(self, *, mutation: bool = False):
        if mutation:
            self._guard_directory()
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._guard_directory()
            if not self._guard_file():
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                os.close(fd)
            self._guard_file()
        elif not self._exists():
            raise ValueError('Pattern store is unavailable')
        uri = 'file:' + quote(str(self.path), safe='/') + ('?mode=rw' if mutation else '?mode=ro')
        db = sqlite3.connect(uri, uri=True, timeout=2, isolation_level=None)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE' if mutation else 'BEGIN')
            self._schema(db, initialize=mutation)
            if mutation:
                db.commit()
            yield db
        except sqlite3.DatabaseError as error:
            if db.in_transaction:
                db.rollback()
            raise ValueError('Pattern store is unavailable or corrupt') from error
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _head(db, project_id: str) -> tuple[int, Optional[int]]:
        row = db.execute('SELECT revision,record_revision FROM heads WHERE project_id=?', (project_id,)).fetchone()
        if row is None:
            return 0, None
        revision, record_revision = row
        _revision(revision, positive=True)
        if record_revision is not None and record_revision != revision:
            raise ValueError('Corrupt pattern head identity')
        return revision, record_revision

    @staticmethod
    def _record(db, project_id: str, revision: int) -> tuple[dict, str]:
        row = db.execute('SELECT record_id,payload FROM records WHERE project_id=? AND revision=?',
                         (project_id, revision)).fetchone()
        if row is None:
            raise ValueError('Pattern head refers to a missing artifact')
        record_id, payload = row
        try:
            record, encoded, digest = validate_record(project_id, json.loads(payload))
        except (TypeError, ValueError, RecursionError) as error:
            raise ValueError('Corrupt pattern artifact') from error
        if payload != encoded or record_id != digest:
            raise ValueError('Pattern artifact hash mismatch')
        return record, record_id

    def save(self, project_id: str, record: dict, expected_revision: int) -> dict:
        """Append after CAS, or retain the identical current artifact without recounting."""
        _revision(expected_revision)
        _, payload, record_id = validate_record(project_id, record)
        with self._connect(mutation=True) as db:
            db.execute('BEGIN IMMEDIATE')
            revision, current = self._head(db, project_id)
            current_id = self._record(db, project_id, current)[1] if current is not None else None
            if revision != expected_revision:
                return {'status': 'conflict', 'revision': revision, 'record_id': current_id}
            if record_id == current_id:
                return {'status': 'unchanged', 'revision': revision, 'record_id': current_id}
            if revision == 0 and db.execute('SELECT count(*) FROM heads').fetchone()[0] >= MAX_PROJECTS:
                raise ValueError('Pattern store project limit reached')
            if db.execute('SELECT count(*) FROM records WHERE project_id=?', (project_id,)).fetchone()[0] >= MAX_GENERATIONS:
                raise ValueError('Pattern history limit reached; explicit deletion is required')
            new_revision = _revision(revision + 1, positive=True)
            db.execute('INSERT INTO records VALUES (?,?,?,?)', (project_id, new_revision, record_id, payload))
            db.execute('INSERT INTO heads VALUES (?,?,?) ON CONFLICT(project_id) DO UPDATE '
                       'SET revision=excluded.revision,record_revision=excluded.record_revision',
                       (project_id, new_revision, new_revision))
            db.commit()
            return {'status': 'stored', 'revision': new_revision, 'record_id': record_id}

    def read(self, project_id: str, revision: Optional[int] = None) -> dict:
        """Read a retained artifact without creating state or claiming live-source freshness."""
        _identifier(project_id, 128, 'project ID')
        if revision is not None:
            _revision(revision, positive=True)
        if not self._exists():
            return {'status': 'not-learned', 'revision': 0, 'record_id': None}
        with self._connect() as db:
            head, current = self._head(db, project_id)
            selected = revision if revision is not None else current
            if selected is not None and selected > head:
                if db.execute('SELECT 1 FROM records WHERE project_id=? AND revision=?',
                              (project_id, selected)).fetchone():
                    raise ValueError('Pattern history is outside its head revision')
                return {'status': 'not-learned', 'revision': head, 'record_id': None}
            if selected is None or not db.execute('SELECT 1 FROM records WHERE project_id=? AND revision=?',
                                                (project_id, selected)).fetchone():
                if revision is None and current is not None:
                    raise ValueError('Pattern head refers to a missing artifact')
                return {'status': 'not-learned', 'revision': head, 'record_id': None}
            record, record_id = self._record(db, project_id, selected)
            return {'status': 'stored', 'revision': selected, 'record_id': record_id, 'record': record}

    def history(self, project_id: str, limit: int = 20, after_revision: int = 0) -> dict:
        """Page immutable revision metadata in ascending order, without returning artifacts."""
        _identifier(project_id, 128, 'project ID')
        _revision(after_revision)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Use history limit 1..50')
        if not self._exists():
            return {'status': 'not-learned', 'revision': 0, 'records': [], 'next_after_revision': None}
        with self._connect() as db:
            revision, _ = self._head(db, project_id)
            rows = db.execute('SELECT revision FROM records WHERE project_id=? AND revision>? '
                              'ORDER BY revision LIMIT ?', (project_id, after_revision, limit + 1)).fetchall()
            records = []
            for (generation,) in rows[:limit]:
                if generation > revision:
                    raise ValueError('Pattern history is outside its head revision')
                record, record_id = self._record(db, project_id, generation)
                backend_name = record['backend'].get('name')
                records.append({'revision': generation, 'record_id': record_id, 'kind': record['kind'],
                                'source_count': len(record['sources']), 'pattern_count': len(record['patterns']),
                                'backend_name': backend_name[:128] if isinstance(backend_name, str) else None})
            return {'status': 'stored' if records else 'not-learned', 'revision': revision, 'records': records,
                    'next_after_revision': records[-1]['revision'] if len(rows) > limit else None}

    def delete(self, project_id: str, expected_revision: int) -> dict:
        """Delete retained scope history while preserving a monotonic ABA fence."""
        _identifier(project_id, 128, 'project ID')
        _revision(expected_revision, positive=True)
        if not self._exists():
            return {'status': 'conflict', 'revision': 0, 'record_id': None}
        with self._connect(mutation=True) as db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('BEGIN IMMEDIATE')
            revision, current = self._head(db, project_id)
            current_id = self._record(db, project_id, current)[1] if current is not None else None
            if revision != expected_revision:
                return {'status': 'conflict', 'revision': revision, 'record_id': current_id}
            if current is None:
                return {'status': 'not-learned', 'revision': revision, 'record_id': None}
            revision = _revision(revision + 1, positive=True)
            db.execute('UPDATE heads SET revision=?,record_revision=NULL WHERE project_id=?', (revision, project_id))
            db.execute('DELETE FROM records WHERE project_id=?', (project_id,))
            db.commit()
            return {'status': 'deleted', 'revision': revision, 'record_id': None}

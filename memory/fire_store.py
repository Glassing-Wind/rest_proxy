"""Explicit scoped FIRE recovery; originals are historical evidence, not instructions."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from dataclasses import asdict

from memory.types import EvidenceReference, TaskCheckpoint


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


class FireStore:
    """Private SQLite snapshots with revision CAS and bounded evidence recovery."""

    def __init__(self, directory: str):
        root = Path(directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.stat().st_mode & 0o077:
            raise ValueError('FIRE state directory must be private (mode 700)')
        self.path = root / 'fire.sqlite3'

    def connect(self):
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise ValueError('FIRE state file must be private (mode 600)')
        db = sqlite3.connect(self.path, timeout=2)
        db.execute('CREATE TABLE IF NOT EXISTS snapshots (scope TEXT, revision INTEGER, '
                   'payload TEXT NOT NULL, expires REAL NOT NULL, PRIMARY KEY(scope,revision))')
        db.execute('CREATE TABLE IF NOT EXISTS heads (scope TEXT PRIMARY KEY, revision INTEGER NOT NULL)')
        return db

    @staticmethod
    def scope(project_id, session_id, task_id):
        values = [project_id, session_id, task_id]
        if any(not isinstance(v, str) or not v.strip() or len(v) > 128 for v in values):
            raise ValueError('Require nonempty project/session/task IDs of at most 128 characters')
        return hashlib.sha256(encoded(values).encode()).hexdigest()

    def save(self, checkpoint: dict, originals: dict, expected_revision: int,
             retention_seconds: int = 86400, correction: dict | None = None):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Require a nonnegative expected revision')
        if type(retention_seconds) is not int or not 60 <= retention_seconds <= 2592000:
            raise ValueError('Retention must be 60 seconds to 30 days')
        if not isinstance(checkpoint, dict) or not isinstance(originals, dict):
            raise ValueError('Checkpoint and originals must be objects')
        data = dict(checkpoint)
        references = data.pop('evidence', [])
        if not isinstance(references, list) or len(references) > 100:
            raise ValueError('At most 100 evidence references')
        task = TaskCheckpoint(**data)
        task.created_at = time.time()
        if not isinstance(task.id, str) or not task.id or len(task.id) > 128:
            raise ValueError('Checkpoint ID must be a nonempty string of at most 128 characters')
        scope = self.scope(task.project_id, task.session_id, task.task_id)
        if type(task.schema_version) is not int or task.schema_version != 1 or not isinstance(task.goal, str) or not task.goal.strip():
            raise ValueError('Require a version 1 checkpoint with a goal')
        for values in [task.accepted_constraints, task.unresolved_questions, task.next_actions]:
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError('Constraints/questions/actions must be string lists')
        if not isinstance(task.decisions, list) or any(
                not isinstance(v, dict) or not v.get('origin') for v in task.decisions):
            raise ValueError('Decisions require origin fields')
        if correction is not None and (not isinstance(correction, dict) or not correction.get('origin')
                                       or not correction.get('reason')):
            raise ValueError('Corrections require origin and reason')
        if expected_revision and correction is None:
            raise ValueError('Replacing a checkpoint requires correction origin and reason')
        stored = {}
        ids = set()
        for value in references:
            reference = EvidenceReference(**value)
            if (reference.project_id, reference.session_id) != (task.project_id, task.session_id):
                raise ValueError('Evidence is outside checkpoint scope')
            if (type(reference.schema_version) is not int or reference.schema_version != 1
                    or not isinstance(reference.source_id, str) or not reference.source_id or len(reference.source_id) > 512
                    or not isinstance(reference.source_kind, str) or not reference.source_kind):
                raise ValueError('Evidence requires version 1 source ID and kind')
            if reference.content_hash is not None and (
                    not isinstance(reference.content_hash, str) or len(reference.content_hash) != 64
                    or any(c not in '0123456789abcdef' for c in reference.content_hash)):
                raise ValueError('Evidence content hashes must be lowercase SHA256')
            if reference.source_id in ids:
                raise ValueError('Duplicate evidence IDs')
            ids.add(reference.source_id)
            original = originals.get(reference.source_id)
            row = asdict(reference)
            row['freshness'] = 'historical-unvalidated'
            row['original_availability'] = 'unavailable'
            if original is not None:
                if not isinstance(original, str):
                    raise ValueError('Originals must be strings')
                digest = hashlib.sha256(original.encode()).hexdigest()
                if reference.content_hash and reference.content_hash != digest:
                    raise ValueError('Original does not match supplied hash')
                row.update(content_hash=digest, original_availability='stored')
                if reference.source_id in stored and stored[reference.source_id] != original:
                    raise ValueError('Conflicting originals')
                stored[reference.source_id] = original
            task.evidence.append(EvidenceReference(**row))
        if set(originals) - {r.source_id for r in task.evidence}:
            raise ValueError('Unreferenced originals are not accepted')
        state = asdict(task)
        if len(encoded(state).encode()) > 24000 or len(encoded(stored).encode()) > 128000:
            raise ValueError('Checkpoint/originals exceed byte budgets')
        payload = encoded({'checkpoint': state, 'originals': stored, 'correction': correction})
        if len(encoded(correction).encode()) > 4000 or len(payload.encode()) > 160000:
            raise ValueError('Correction/payload exceeds byte budget')
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            head = db.execute('SELECT revision FROM heads WHERE scope=?', (scope,)).fetchone()
            revision = head[0] if head else 0
            if revision != expected_revision:
                db.rollback()
                return {'status': 'conflict', 'revision': revision}
            revision += 1
            expires = time.time() + retention_seconds
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?)', (scope, revision, payload, expires))
            db.execute('INSERT OR REPLACE INTO heads VALUES (?,?)', (scope, revision))
            db.commit()
            return {'status': 'stored', 'revision': revision, 'checkpoint_id': task.id, 'expires_at': expires}
        finally:
            db.close()

    def resume(self, project_id, session_id, task_id, *, source_id=None, current_hashes=None,
               offset=0, max_chars=8000, _include_originals=False):
        if type(offset) is not int or not 0 <= offset <= 128000 or type(max_chars) is not int or not 1 <= max_chars <= 8000:
            raise ValueError('Original pages require offset 0..128000 and max_chars 1..8000')
        if current_hashes is not None and (not isinstance(current_hashes, dict) or any(
                not isinstance(k, str) or (v is not None and (not isinstance(v, str) or len(v) != 64
                    or any(c not in '0123456789abcdef' for c in v)))
                for k, v in current_hashes.items())):
            raise ValueError('Current hashes must map source IDs to SHA256 strings or null deletion markers')
        scope = self.scope(project_id, session_id, task_id)
        db = self.connect()
        try:
            row = db.execute('SELECT s.revision,s.payload,s.expires FROM snapshots s '
                             'JOIN heads h ON s.scope=h.scope AND s.revision=h.revision '
                             'WHERE s.scope=?', (scope,)).fetchone()
            head = db.execute('SELECT revision FROM heads WHERE scope=?', (scope,)).fetchone()
        finally:
            db.close()
        if row is None:
            return {'status': 'not_found', 'revision': head[0] if head else 0}
        revision, raw, expires = row
        if expires <= time.time():
            return {'status': 'expired', 'revision': revision}
        data = json.loads(raw)
        checkpoint = data['checkpoint']
        for ref in checkpoint['evidence']:
            original = data['originals'].get(ref['source_id'])
            if original is not None and hashlib.sha256(original.encode()).hexdigest() != ref['content_hash']:
                raise RuntimeError('Stored original hash mismatch')
            current = (current_hashes or {}).get(ref['source_id'])
            ref['freshness'] = ('historical-unvalidated' if ref['source_id'] not in (current_hashes or {}) else
                                'matches-supplied-current-hash' if current is not None and current == ref['content_hash'] else
                                'changed-or-deleted')
        if source_id is not None:
            refs = [r for r in checkpoint['evidence'] if r['source_id'] == source_id]
            original = data['originals'].get(source_id)
            if not refs or original is None:
                return {'status': 'original_unavailable', 'revision': revision}
            page = original[offset:offset + max_chars]
            next_offset = offset + len(page) if offset + len(page) < len(original) else None
            return {'status': 'historical-original', 'revision': revision, 'reference': refs[0],
                    'original': page, 'offset': offset, 'total_chars': len(original),
                    'next_offset': next_offset, 'original_complete': offset == 0 and next_offset is None}
        result = {'status': 'resumed', 'revision': revision, 'expires_at': expires,
                'checkpoint': checkpoint, 'correction': data['correction'],
                'originals_included': False, 'source_revalidation': 'caller-supplied-hashes-only'}
        if _include_originals:
            result['_originals'] = data['originals']
        return result

    def delete(self, project_id, session_id, task_id, expected_revision):
        scope = self.scope(project_id, session_id, task_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('Deletion requires a positive expected revision')
        db = self.connect()
        try:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('BEGIN IMMEDIATE')
            head = db.execute('SELECT revision FROM heads WHERE scope=?', (scope,)).fetchone()
            revision = head[0] if head else 0
            if revision != expected_revision:
                db.rollback()
                return {'status': 'conflict', 'revision': revision}
            db.execute('DELETE FROM snapshots WHERE scope=?', (scope,))
            db.execute('INSERT OR REPLACE INTO heads VALUES (?,?)', (scope, revision + 1))
            db.commit()
            return {'status': 'deleted', 'versions_deleted_through': revision, 'revision': revision + 1}
        finally:
            db.close()

    def purge_expired(self, project_id, session_id, task_id):
        scope = self.scope(project_id, session_id, task_id)
        db = self.connect()
        try:
            db.execute('PRAGMA secure_delete=ON')
            cursor = db.execute('DELETE FROM snapshots WHERE scope=? AND expires<=?', (scope, time.time()))
            db.commit()
            head = db.execute('SELECT revision FROM heads WHERE scope=?', (scope,)).fetchone()
            return {'status': 'purged', 'versions_deleted': cursor.rowcount, 'revision': head[0] if head else 0}
        finally:
            db.close()

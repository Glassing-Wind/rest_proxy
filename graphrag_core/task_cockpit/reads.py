"""Bounded task metadata reads; never initialize or modify registry state."""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3
import stat

from memory.task_registry import TaskRegistry


MAX_PAYLOAD_BYTES = 65536
MAX_RESPONSE_BYTES = 8192
STATUSES = {'queued', 'claimed', 'review_pending', 'completed', 'cancelled'}
ACTORS = {'human', 'agent', 'automated', 'unspecified'}
FAILURE_FIELDS = {'at', 'attempt', 'category', 'stage', 'observed_revision', 'remote_termination'}
INTEGER_FIELDS = {'revision', 'attempts', 'recovery_count', 'submission_count', 'review_count', 'assessment_count'}
BOOLEAN_FIELDS = {'claim_present', 'claim_expired', 'checkpoint_present', 'human_review_required',
                  'failure_history_complete'}
STATUS_FIELDS = INTEGER_FIELDS | BOOLEAN_FIELDS | {
    'task_id', 'project', 'status', 'latest_review_actor_type', 'failures', 'note',
}
STATUS_NOTES = {
    'Only instrumented generation failures retained; absence is not success',
    'Only instrumented worker failures retained; absence is not success',
}
FAILURE_STAGES = {
    'timeout': 'generation', 'generation_error': 'generation',
    'finding_rejected': 'returned_finding', 'preparation_error': 'preparation',
}


class TaskStatusIntegrityError(ValueError):
    """Existing state does not satisfy the bounded metadata read contract."""


class MissingTaskError(LookupError):
    """The fixed project/task pair is absent from the selected registry."""


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise TaskStatusIntegrityError('Duplicate JSON field')
        value[key] = item
    return value


def _reject_constant(value):
    raise TaskStatusIntegrityError('Non-finite JSON number')


def _integer(value, minimum=0, maximum=2**53 - 1):
    return type(value) is int and minimum <= value <= maximum


class TaskStatusReader(TaskRegistry):
    """Reuse registry inspection through read-only connections to existing state.

    No constructor from the writing registry is invoked. This object is an
    internal adapter; its HTTP surface exposes only ``status`` for one scope.
    Local file ownership is a prerequisite, not remote identity authentication.
    """

    def __init__(self, directory: str, project: str, task_id: str):
        self.identifier(project)
        self.identifier(task_id)
        self.root = Path(directory).expanduser().absolute()
        self.path = self.root / 'tasks.sqlite3'
        self.project, self.task_id = project, task_id
        self._identity = None
        self._validate_paths()

    def _validate_paths(self):
        if self.root.resolve(strict=True) != self.root or self.path.resolve(strict=True) != self.path:
            raise TaskStatusIntegrityError('Require canonical state paths without symlinks')
        root_info, file_info = self.root.lstat(), self.path.lstat()
        if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_mode & 0o077 or
                root_info.st_uid != os.getuid()):
            raise TaskStatusIntegrityError('Require private owner-controlled state directory')
        if (not stat.S_ISREG(file_info.st_mode) or stat.S_IMODE(file_info.st_mode) != 0o600 or
                file_info.st_uid != os.getuid() or file_info.st_nlink != 1):
            raise TaskStatusIntegrityError('Require private owner-controlled registry file')
        identity = (root_info.st_dev, root_info.st_ino, file_info.st_dev, file_info.st_ino)
        if self._identity is not None and identity != self._identity:
            raise TaskStatusIntegrityError('Registry identity changed')
        self._identity = identity
        # The registry writer uses rollback journals. A read-only SQLite WAL
        # connection can create shared-memory sidecars, so WAL is deliberately
        # unsupported here rather than opening immutable, potentially stale data.
        if any(os.path.lexists(str(self.path) + suffix) for suffix in ('-wal', '-shm')):
            raise TaskStatusIntegrityError('WAL state is unsupported by this read adapter')
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            header = os.read(fd, 100)
            if (opened.st_dev, opened.st_ino) != identity[2:]:
                raise TaskStatusIntegrityError('Registry identity changed during validation')
        finally:
            os.close(fd)
        if len(header) < 100 or header[:16] != b'SQLite format 3\x00' or header[18:20] != b'\x01\x01':
            raise TaskStatusIntegrityError('Require an existing rollback-journal registry')

    @contextmanager
    def connect(self):
        self._validate_paths()
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            yield db
        finally:
            db.close()

    @staticmethod
    def _read(db, project, task_id):
        TaskRegistry.identifier(project)
        TaskRegistry.identifier(task_id)
        size = db.execute('SELECT length(CAST(payload AS BLOB)) FROM tasks WHERE project=? AND id=?',
                          (project, task_id)).fetchone()
        if size is None:
            raise MissingTaskError('Task not found in configured scope')
        if not _integer(size[0], 1, MAX_PAYLOAD_BYTES):
            raise TaskStatusIntegrityError('Registry payload exceeds read budget')
        payload = db.execute('SELECT payload FROM tasks WHERE project=? AND id=?',
                             (project, task_id)).fetchone()[0]
        try:
            task = json.loads(payload, object_pairs_hook=_json_object, parse_constant=_reject_constant)
        except (ValueError, TypeError, RecursionError) as error:
            raise TaskStatusIntegrityError('Invalid registry payload') from error
        if not isinstance(task, dict) or task.get('project') != project or task.get('id') != task_id:
            raise TaskStatusIntegrityError('Registry scope identity mismatch')
        if type(task.get('schema_version')) is not int or task['schema_version'] != 1:
            raise TaskStatusIntegrityError('Unsupported registry record schema')
        if 'human_review_required' in task and type(task['human_review_required']) is not bool:
            raise TaskStatusIntegrityError('Invalid stored review requirement')
        claim = task.get('claim')
        if claim is not None:
            expires = claim.get('expires_at') if isinstance(claim, dict) else None
            if (not isinstance(claim, dict) or type(expires) not in (int, float) or
                    not math.isfinite(expires) or not 0 <= expires <= 2**53 - 1):
                raise TaskStatusIntegrityError('Invalid stored claim expiry')
        return task

    def status(self) -> dict:
        """Inspect one fixed task and reject malformed metadata rather than echo it."""
        try:
            result = super().inspect(self.project, self.task_id)
            # Older registry implementations predate these review fields.
            # Missing policy metadata means unknown, never implied permission.
            result.setdefault('human_review_required', None)
            result.setdefault('latest_review_actor_type', 'unspecified')
            if set(result) != STATUS_FIELDS:
                raise TaskStatusIntegrityError('Unexpected status fields')
            if result['status'] not in STATUSES or result['latest_review_actor_type'] not in ACTORS:
                raise TaskStatusIntegrityError('Invalid status classification')
            if any(not _integer(result[key], 1 if key == 'revision' else 0) for key in INTEGER_FIELDS):
                raise TaskStatusIntegrityError('Invalid status counter')
            if result['attempts'] > 5 or result['review_count'] > 20 or result['assessment_count'] > 10:
                raise TaskStatusIntegrityError('Status counter exceeds registry bounds')
            if (any(type(result[key]) is not bool for key in BOOLEAN_FIELDS - {'human_review_required'}) or
                    (result['human_review_required'] is not None and
                     type(result['human_review_required']) is not bool)):
                raise TaskStatusIntegrityError('Invalid status flag')
            if result['failure_history_complete'] is not False or result['note'] not in STATUS_NOTES:
                raise TaskStatusIntegrityError('Invalid failure qualification')
            failures = result['failures']
            if not isinstance(failures, list) or len(failures) > 5:
                raise TaskStatusIntegrityError('Failure metadata exceeds registry bounds')
            for item in failures:
                if not isinstance(item, dict) or set(item) != FAILURE_FIELDS:
                    raise TaskStatusIntegrityError('Invalid failure fields')
                if (type(item['at']) not in (float, int) or not math.isfinite(item['at']) or
                        not 0 <= item['at'] <= 2**53 - 1 or not _integer(item['attempt'], 1, 5) or
                        not _integer(item['observed_revision'], 1) or
                        item['category'] not in FAILURE_STAGES or
                        item['stage'] != FAILURE_STAGES[item['category']] or
                        item['remote_termination'] != 'unknown'):
                    raise TaskStatusIntegrityError('Invalid failure classification')
            result = {'schema_version': 1, 'read_only': True, **result}
            if len(json.dumps(result, allow_nan=False).encode('utf-8')) > MAX_RESPONSE_BYTES:
                raise TaskStatusIntegrityError('Status response exceeds read budget')
            return result
        except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as error:
            raise TaskStatusIntegrityError('Malformed registry status metadata') from error

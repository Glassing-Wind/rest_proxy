"""Local durable task claims; no worker execution or external authorization."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from memory.fire_store import encoded


class TaskRegistry:
    """Scoped SQLite task records with transactional claims and revision checks."""

    def __init__(self, directory: str):
        root = Path(directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.stat().st_mode & 0o077:
            raise ValueError('Task state directory must be private')
        self.path = root / 'tasks.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks ('
                       'project TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(project,id))')

    @contextmanager
    def connect(self):
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise ValueError('Task state file must be private')
        db = sqlite3.connect(self.path, timeout=2)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def identifier(value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError('Require nonempty identifier of at most 128 characters')
        return value

    def create(self, project: str, workspace: str, goal: str, evidence: list,
               actions: list) -> dict:
        self.identifier(project)
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4096:
            raise ValueError('Require bounded goal')
        if not isinstance(evidence, list) or len(evidence) > 100:
            raise ValueError('At most 100 evidence references')
        # Initial slice deliberately accepts read-only tasks only.
        if not isinstance(actions, list) or not actions or any(a != 'read_source' for a in actions):
            raise ValueError('Only read_source capability supported')
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Workspace must be a directory')
        task = dict(schema_version=1, id=uuid.uuid4().hex, project=project,
                    workspace=str(root), goal=goal, evidence=evidence, actions=['read_source'],
                    revision=1, status='queued', claim=None, attempts=0, checkpoint=None,
                    created_at=time.time())
        payload = encoded(task)
        if len(payload.encode()) > 65536:
            raise ValueError('Task exceeds 64 KiB')
        with self.connect() as db:
            db.execute('INSERT INTO tasks VALUES (?,?,?)', (project, task['id'], payload))
        return task

    def get(self, project: str, task_id: str) -> dict:
        self.identifier(project)
        self.identifier(task_id)
        with self.connect() as db:
            return self._read(db, project, task_id)

    @staticmethod
    def _read(db, project, task_id):
        row = db.execute('SELECT payload FROM tasks WHERE project=? AND id=?',
                         (project, task_id)).fetchone()
        if row is None:
            raise KeyError('Task not found in project')
        return json.loads(row[0])

    def _update(self, project, task_id, revision, edit):
        if type(revision) is not int or revision < 1:
            raise ValueError('Require positive expected revision')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task = self._read(db, project, task_id)
            if task['revision'] != revision:
                raise ValueError('Stale task revision')
            edit(task)
            task['revision'] += 1
            payload = encoded(task)
            if len(payload.encode()) > 65536:
                raise ValueError('Task exceeds 64 KiB')
            db.execute('UPDATE tasks SET payload=? WHERE project=? AND id=?',
                       (payload, project, task_id))
        return task

    def claim(self, project: str, task_id: str, revision: int, worker: str,
              lease_seconds: int = 60) -> dict:
        self.identifier(worker)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError('Lease must be 1–3600 seconds')

        def edit(task):
            # Expired claims require the explicit reclaim operation.
            if task['status'] != 'queued':
                raise ValueError('Task is not queued')
            if task['attempts'] >= 5:
                raise ValueError('Task attempt limit reached')
            task['status'] = 'claimed'
            task['attempts'] += 1
            task['claim'] = dict(token=uuid.uuid4().hex, worker=worker,
                                 expires_at=time.time() + lease_seconds)
        return self._update(project, task_id, revision, edit)

    def checkpoint(self, project: str, task_id: str, revision: int,
                   claim_token: str, checkpoint: dict) -> dict:
        if not isinstance(checkpoint, dict):
            raise ValueError('Checkpoint must be an object')

        def edit(task):
            claim = task['claim']
            if task['status'] != 'claimed' or not claim or claim['token'] != claim_token:
                raise ValueError('Invalid active claim')
            if claim['expires_at'] <= time.time():
                raise ValueError('Claim expired')
            task['checkpoint'] = checkpoint
        return self._update(project, task_id, revision, edit)

    def cancel(self, project: str, task_id: str, revision: int, reason: str) -> dict:
        """Persist cancellation; this does not interrupt a running process."""
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
            raise ValueError('Require bounded cancellation reason')

        def edit(task):
            if task['status'] not in ('queued', 'claimed', 'review_pending'):
                raise ValueError('Task cannot be cancelled in this state')
            task['status'] = 'cancelled'
            task['cancellation'] = dict(reason=reason, at=time.time())
        return self._update(project, task_id, revision, edit)

    def reclaim(self, project: str, task_id: str, revision: int, worker: str,
                reason: str, lease_seconds: int = 60) -> dict:
        """Explicitly replace an expired read-only claim, preserving its checkpoint."""
        self.identifier(worker)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
            raise ValueError('Require bounded reclaim reason')
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError('Lease must be 1–3600 seconds')

        def edit(task):
            claim = task['claim']
            now = time.time()
            if task['status'] != 'claimed' or not claim or claim['expires_at'] > now:
                raise ValueError('Require expired claimed task')
            if task['actions'] != ['read_source']:
                raise ValueError('Only read-only tasks may be reclaimed')
            if task['attempts'] >= 5:
                raise ValueError('Task attempt limit reached')
            task.setdefault('claim_history', []).append(dict(
                worker=claim['worker'], expires_at=claim['expires_at'],
                replaced_at=now, reason=reason))
            task['attempts'] += 1
            task['claim'] = dict(token=uuid.uuid4().hex, worker=worker,
                                 expires_at=now + lease_seconds)
        return self._update(project, task_id, revision, edit)

    def submit(self, project: str, task_id: str, revision: int,
               claim_token: str, finding: dict, generation: dict | None = None) -> dict:
        """Retain a worker finding and relinquish its claim for explicit review."""
        if generation is not None and not isinstance(generation, dict):
            raise ValueError('Generation provenance must be an object')
        if not isinstance(finding, dict) or not finding:
            raise ValueError('Require nonempty finding object')

        def edit(task):
            claim = task['claim']
            if task['status'] != 'claimed' or not claim or claim['token'] != claim_token:
                raise ValueError('Invalid active claim')
            if claim['expires_at'] <= time.time():
                raise ValueError('Claim expired')
            task.setdefault('submissions', []).append(dict(
                finding=finding, worker=claim['worker'], at=time.time(), generation=generation))
            task['status'] = 'review_pending'
            task['claim'] = None
        return self._update(project, task_id, revision, edit)

    def review(self, project: str, task_id: str, revision: int,
               reviewer: str, decision: str, reason: str) -> dict:
        """Record caller-supplied review identity; this is not authentication."""
        self.identifier(reviewer)
        if decision not in ('accept', 'request_correction'):
            raise ValueError('Unsupported review decision')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
            raise ValueError('Require bounded review reason')

        def edit(task):
            if task['status'] != 'review_pending':
                raise ValueError('Task is not awaiting review')
            submission = task['submissions'][-1]
            if reviewer == submission['worker']:
                raise ValueError('Reviewer must differ from submitting worker')
            if decision == 'request_correction' and task['attempts'] >= 5:
                raise ValueError('Task attempt limit reached')
            task.setdefault('reviews', []).append(dict(
                reviewer=reviewer, decision=decision, reason=reason,
                submission=len(task['submissions']), at=time.time()))
            task['status'] = 'completed' if decision == 'accept' else 'queued'
        return self._update(project, task_id, revision, edit)

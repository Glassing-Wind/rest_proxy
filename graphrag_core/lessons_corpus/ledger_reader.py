"""Read existing private lesson ledgers without opening a writable connection.

This reader shares the authored-ledger validation contract, not its initialization
or mutation API. It requires a quiescent rollback-journal database: WAL-format
files and all journal/WAL/shared-memory sidecars are refused. Stop the writer and
checkpoint or copy its database explicitly before presenting that state here.
No directory, database, schema or recovery file is created by this module.

The explicit source resolver must itself provide read-only source access. Private
paths and read-only SQLite are not isolation from another process under the same
operating-system identity, nor authentication of an operator-supplied reviewer.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Callable

from graphrag_core.lessons_corpus.ledger import (
    LessonConflict, LessonIntegrityError, LessonLedger, LessonLedgerError,
    _HASH, _TABLE_SQL, _TRIGGER_SQL, _identifier, _revision,
)


class LessonLedgerReader:
    """Validate and read an existing ledger; no save or review methods exist."""

    def __init__(self, path: Path, source_resolver: Callable[[str], dict]):
        self.path = Path(path)
        if not self.path.is_absolute() or '..' in self.path.parts:
            raise LessonLedgerError('Reader requires an explicit absolute canonical ledger path')
        if not callable(source_resolver):
            raise LessonLedgerError('A read-only source resolver is required')
        # Reuse precisely the existing history, record and citation validators
        # without calling the constructor that creates a database and schema.
        self._validator = object.__new__(LessonLedger)
        self._validator.path = self.path
        self._validator.source_resolver = source_resolver
        self._guard()
        with self._connection():
            pass

    def _guard(self) -> tuple:
        self._validator._guard()
        try:
            if self.path.resolve(strict=True) != self.path:
                raise LessonLedgerError('Reader ledger path must be canonical')
            info = self.path.lstat()
            for suffix in ('-journal', '-wal', '-shm'):
                if Path(str(self.path) + suffix).exists() or Path(str(self.path) + suffix).is_symlink():
                    raise LessonLedgerError('Read-only ledger requires no journal, WAL or SHM sidecars')
            with self.path.open('rb') as original:
                header = original.read(100)
        except FileNotFoundError:
            raise LessonLedgerError('Read-only ledger must already exist') from None
        if (len(header) != 100 or header[:16] != b'SQLite format 3\x00'
                or header[18:20] != b'\x01\x01'):
            raise LessonIntegrityError('Reader requires an existing rollback-journal SQLite database')
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    @staticmethod
    def _schema(db: sqlite3.Connection) -> None:
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise LessonIntegrityError('Unsupported lessons ledger schema')
        expected = {('table', 'lesson_revisions', 'lesson_revisions', _TABLE_SQL)}
        expected.update(('trigger', name, 'lesson_revisions', sql) for name, sql in _TRIGGER_SQL.items())
        observed = set(db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE substr(name,1,7) != 'sqlite_'"
        ))
        if observed != expected:
            raise LessonIntegrityError('Ledger schema or immutability triggers differ from the contract')
        if db.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
            raise LessonIntegrityError('Reader requires a quiescent rollback-journal ledger')

    @contextmanager
    def _connection(self):
        before = self._guard()
        db = None
        try:
            db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True,
                                 timeout=5, isolation_level=None)
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('BEGIN')
            self._schema(db)
            if self._guard() != before:
                raise LessonIntegrityError('Ledger changed while opening its read snapshot')
            yield db
            if self._guard() != before:
                raise LessonIntegrityError('Ledger changed during its read snapshot')
        except sqlite3.DatabaseError:
            raise LessonIntegrityError('Ledger cannot be read without database mutation or recovery') from None
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    def get(self, lesson_id: str, revision: int | None = None) -> dict | None:
        """Read one revision after validating its entire history and source spans."""
        lesson_id = _identifier(lesson_id)
        if revision is not None:
            revision = _revision(revision)
        with self._connection() as db:
            return self._validator._load(db, lesson_id, revision)

    def list_lessons(self, limit: int = 100) -> list[dict]:
        """Read checked current revisions in stable lesson identifier order."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise LessonLedgerError('Lesson list limit must be between 1 and 1000')
        with self._connection() as db:
            identifiers = [row[0] for row in db.execute(
                'SELECT DISTINCT lesson_id FROM lesson_revisions ORDER BY lesson_id LIMIT ?', (limit,)
            )]
            return [self._validator._load(db, identifier) for identifier in identifiers]

    def read_current(self, lesson_id: str, expected_revision: int, expected_hash: str) -> dict:
        """Bind a checked current revision; stale revision or hash is a conflict."""
        expected_revision = _revision(expected_revision)
        if type(expected_hash) is not str or not _HASH.fullmatch(expected_hash):
            raise LessonLedgerError('Expected revision hash must be lowercase SHA-256')
        current = self.get(lesson_id)
        if (current is None or current['revision'] != expected_revision
                or current['revision_sha256'] != expected_hash):
            raise LessonConflict('Lesson changed; select its current revision and hash again')
        return current

    @staticmethod
    def _selections(selections: list[dict]) -> list[tuple[str, int, str]]:
        """Normalize bounded selections before opening the read-only database."""
        if type(selections) is not list or not 1 <= len(selections) <= 10:
            raise LessonLedgerError('Select between one and ten lessons')
        checked, identifiers = [], set()
        for selection in selections:
            if type(selection) is not dict or set(selection) != {'lesson_id', 'revision', 'revision_sha256'}:
                raise LessonLedgerError('Lesson selection fields do not match the contract')
            lesson_id = _identifier(selection['lesson_id'])
            revision = _revision(selection['revision'])
            revision_hash = selection['revision_sha256']
            if type(revision_hash) is not str or not _HASH.fullmatch(revision_hash):
                raise LessonLedgerError('Selected revision hash must be lowercase SHA-256')
            if lesson_id in identifiers:
                raise LessonLedgerError('Select each lesson identifier only once')
            identifiers.add(lesson_id)
            checked.append((lesson_id, revision, revision_hash))
        return checked

    def _selected(self, db: sqlite3.Connection, checked: list[tuple[str, int, str]]) -> list[dict]:
        results = []
        for lesson_id, revision, revision_hash in checked:
            current = self._validator._load(db, lesson_id)
            if (current is None or current['revision'] != revision
                    or current['revision_sha256'] != revision_hash):
                raise LessonConflict('Lesson changed; select its current revision and hash again')
            results.append(current)
        return results

    def read_selected(self, selections: list[dict]) -> list[dict]:
        """Bind one through ten distinct current records in a single read snapshot."""
        checked = self._selections(selections)
        with self._connection() as db:
            return self._selected(db, checked)

    def read_investigated(self, selections: list[dict]) -> dict:
        """Bind version-2 lessons and fresh reviewed findings in one read snapshot.

        General historical reads remain available through get/list/read_selected.
        This stricter path is for live lesson review and release preparation.
        """
        checked = self._selections(selections)
        with self._connection() as db:
            lessons = self._selected(db, checked)
            investigations = {}
            for lesson in lessons:
                for finding in self._validator.validate_investigations(db, lesson):
                    investigations[finding['lesson_id']] = finding
            return {'lessons': lessons,
                    'investigations': [investigations[key] for key in sorted(investigations)]}

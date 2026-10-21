"""Private, append-only authored lessons with source-checked citation spans.

Review decisions are operator-supplied assessments. Neither review nor hash
validation upgrades a statement to fact or authenticates a reviewer's identity.
Constructing a ledger requires an explicit absolute path; importing this module
does not create state, contact services or train models.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Callable


KINDS = frozenset({'experience', 'source_claim', 'value', 'hypothesis', 'proposed_lesson'})
DECISIONS = frozenset({'accepted', 'disputed', 'rejected'})
MAX_INVESTIGATION_REFS = 32
_INVESTIGATION_FIELDS = frozenset({'record_id', 'revision', 'revision_sha256', 'role'})
_INVESTIGATION_ROLES = frozenset({'supports', 'challenges', 'context'})
_RECORD_FIELDS = frozenset({
    'kind', 'statement', 'author', 'context', 'values', 'affected_parties',
    'uncertainty', 'citations', 'counter_citations',
})
_CITATION_FIELDS = frozenset({
    'source_id', 'source_sha256', 'text_sha256', 'start_char', 'end_char', 'quote',
})
_EVENT_FIELDS = frozenset({
    'schema_version', 'lesson_id', 'revision', 'status', 'record', 'review',
    'operation', 'created_at', 'previous_hash', 'verification',
})
_HASH = re.compile(r'^[0-9a-f]{64}$')
_IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$')
_ZERO_HASH = '0' * 64
_MAX_RECORD_BYTES = 256 * 1024
_MAX_REVISIONS = 10000
_TABLE_SQL = (
    'CREATE TABLE lesson_revisions (lesson_id TEXT NOT NULL, revision INTEGER NOT NULL '
    'CHECK (revision > 0), payload TEXT NOT NULL, sha256 TEXT NOT NULL, '
    'PRIMARY KEY (lesson_id, revision))'
)
_TRIGGER_SQL = {
    'lesson_revisions_no_update':
        "CREATE TRIGGER lesson_revisions_no_update BEFORE UPDATE ON lesson_revisions "
        "BEGIN SELECT RAISE(ABORT, 'Lesson revisions are immutable'); END",
    'lesson_revisions_no_delete':
        "CREATE TRIGGER lesson_revisions_no_delete BEFORE DELETE ON lesson_revisions "
        "BEGIN SELECT RAISE(ABORT, 'Lesson revisions are immutable'); END",
}


class LessonLedgerError(ValueError):
    """Invalid ledger input or unsupported private-state path."""


class LessonConflict(LessonLedgerError):
    """The caller's expected revision is no longer current."""


class LessonIntegrityError(LessonLedgerError):
    """A stored revision, source identity or citation no longer validates."""


def _encoded(value: dict) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, UnicodeError):
        raise LessonLedgerError('Record must be finite UTF-8 JSON') from None


def _hash(value: str) -> str:
    try:
        return hashlib.sha256(value.encode('utf-8')).hexdigest()
    except UnicodeError:
        raise LessonLedgerError('Text must be valid UTF-8') from None


def _object(value: object, fields: frozenset[str], label: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise LessonLedgerError(f'{label} fields do not match the contract')
    return value


def _text(value: object, label: str, limit: int = 32768) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise LessonLedgerError(f'{label} requires nonempty bounded text')
    _hash(value)
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        raise LessonLedgerError('Invalid lesson identifier')
    return value


def _identity(value: object, *, reviewer: bool = False) -> dict:
    if reviewer and type(value) is str:
        return {'kind': 'operator', 'name': _text(value, 'Reviewer', 512)}
    if (type(value) is not dict or type(value.get('kind')) is not str
            or value['kind'] not in {'human', 'model'}):
        raise LessonLedgerError('Identity requires a human or model kind')
    fields = frozenset({'kind', 'name', 'model_id'} if value['kind'] == 'model' else {'kind', 'name'})
    _object(value, fields, 'Identity')
    result = {'kind': value['kind'], 'name': _text(value['name'], 'Identity name', 512)}
    if value['kind'] == 'model':
        result['model_id'] = _text(value['model_id'], 'Model identity', 512)
    return result


def _revision(value: object, *, creation: bool = False) -> int:
    floor = 0 if creation else 1
    if type(value) is not int or not floor <= value <= _MAX_REVISIONS:
        raise LessonLedgerError('Invalid revision')
    return value


def _investigation_refs(value: object) -> list[dict]:
    """Validate exact revision references without consulting mutable ledger heads."""
    if type(value) is not list or not 1 <= len(value) <= MAX_INVESTIGATION_REFS:
        raise LessonLedgerError(f'Investigation references require one through {MAX_INVESTIGATION_REFS} records')
    result, identifiers = [], set()
    for reference in value:
        item = _object(reference, _INVESTIGATION_FIELDS, 'Investigation reference')
        record_id = _identifier(item['record_id'])
        revision = _revision(item['revision'])
        revision_hash = item['revision_sha256']
        if type(revision_hash) is not str or not _HASH.fullmatch(revision_hash):
            raise LessonLedgerError('Investigation revision hash must be lowercase SHA-256')
        if type(item['role']) is not str or item['role'] not in _INVESTIGATION_ROLES:
            raise LessonLedgerError('Investigation role must be supports, challenges or context')
        if record_id in identifiers:
            raise LessonLedgerError('Reference each investigation record only once')
        identifiers.add(record_id)
        result.append({'record_id': record_id, 'revision': revision,
                       'revision_sha256': revision_hash, 'role': item['role']})
    return result


class LessonLedger:
    """Immutable revisions with compare-and-swap writes and checked source reads."""

    def __init__(self, path: Path, source_resolver: Callable[[str], dict]):
        self.path = Path(path)
        if not self.path.is_absolute() or '..' in self.path.parts:
            raise LessonLedgerError('Ledger requires an explicit absolute canonical file path')
        if not callable(source_resolver):
            raise LessonLedgerError('A source resolver is required')
        self.source_resolver = source_resolver
        self._guard()
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        self._guard()
        with self._connection():
            pass

    def _guard(self) -> None:
        for component in (*reversed(self.path.parent.parents), self.path.parent):
            try:
                info = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise LessonLedgerError('Ledger directory components must be real directories')
        if self.path.parent.exists():
            info = self.path.parent.stat()
            if stat.S_IMODE(info.st_mode) != 0o700 or (
                hasattr(os, 'getuid') and info.st_uid != os.getuid()
            ):
                raise LessonLedgerError('Ledger directory requires current-user ownership and mode 700')
        for suffix in ('', '-journal', '-wal', '-shm'):
            try:
                info = Path(str(self.path) + suffix).lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
                raise LessonLedgerError('Ledger files require current-user ownership, mode 600 and one link')

    @contextmanager
    def _connection(self):
        self._guard()
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(descriptor)
        self._guard()
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if version == 0 and not tables:
                db.execute(_TABLE_SQL)
                for sql in _TRIGGER_SQL.values():
                    db.execute(sql)
                db.execute('PRAGMA user_version=1')
                version, tables = 1, {'lesson_revisions'}
            if version != 1 or tables != {'lesson_revisions'}:
                raise LessonIntegrityError('Unsupported lessons ledger schema')
            columns = {row[1] for row in db.execute('PRAGMA table_info(lesson_revisions)')}
            if columns != {'lesson_id', 'revision', 'payload', 'sha256'}:
                raise LessonIntegrityError('Lessons ledger columns do not match the contract')
            triggers = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
            if triggers != _TRIGGER_SQL:
                raise LessonIntegrityError('Lessons ledger immutability triggers do not match the contract')
            db.commit()
            self._guard()
            yield db
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def _citation(self, citation: object) -> dict:
        item = _object(citation, _CITATION_FIELDS, 'Citation')
        source_id = _text(item['source_id'], 'Citation source', 2048)
        for name in ('source_sha256', 'text_sha256'):
            if type(item[name]) is not str or not _HASH.fullmatch(item[name]):
                raise LessonIntegrityError('Citation requires a valid source and text hash')
        start, end = item['start_char'], item['end_char']
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise LessonLedgerError('Citation span requires half-open Unicode character offsets')
        quote = _text(item['quote'], 'Citation quote')
        try:
            source = self.source_resolver(source_id)
        except Exception:
            raise LessonIntegrityError('Citation source cannot be resolved') from None
        if type(source) is not dict or any(name not in source for name in (
            'source_id', 'source_sha256', 'text_sha256', 'parser_version', 'text'
        )):
            raise LessonIntegrityError('Source resolver did not return the required provenance')
        if (source['source_id'] != source_id or source['source_sha256'] != item['source_sha256']
                or source['text_sha256'] != item['text_sha256']
                or type(source['text']) is not str
                or type(source['parser_version']) is not str or not source['parser_version'].strip()
                or _hash(source['text']) != item['text_sha256']):
            raise LessonIntegrityError('Citation source or derived-text identity has changed')
        if end > len(source['text']) or source['text'][start:end] != quote:
            raise LessonIntegrityError('Citation quote does not match the exact source span')
        return dict(item)

    def _record(self, record: object, *, schema_version: int = 2) -> dict:
        fields = _RECORD_FIELDS | {'outcomes'} if type(record) is dict and 'outcomes' in record else _RECORD_FIELDS
        if schema_version == 2 and type(record) is dict and record.get('kind') == 'proposed_lesson':
            fields = fields | {'investigation_refs'}
        value = _object(record, fields, 'Lesson record')
        if type(value['kind']) is not str or value['kind'] not in KINDS:
            raise LessonLedgerError('Unsupported lesson kind')
        result = {'kind': value['kind'], 'author': _identity(value['author'])}
        if 'investigation_refs' in fields:
            result['investigation_refs'] = _investigation_refs(value['investigation_refs'])
        for name in ('statement', 'context', 'uncertainty'):
            result[name] = _text(value[name], name)
        for name in ('values', 'affected_parties'):
            if type(value[name]) is not list or len(value[name]) > 128:
                raise LessonLedgerError(f'{name} requires a bounded list')
            result[name] = [_text(item, name, 2048) for item in value[name]]
        for name in ('citations', 'counter_citations'):
            if type(value[name]) is not list or len(value[name]) > 128:
                raise LessonLedgerError(f'{name} requires a bounded list')
            result[name] = [self._citation(item) for item in value[name]]
        if 'outcomes' in value:
            if type(value['outcomes']) is not list or len(value['outcomes']) > 128:
                raise LessonLedgerError('outcomes requires a bounded list')
            result['outcomes'] = []
            for item in value['outcomes']:
                outcome = _object(item, frozenset({'kind', 'description', 'method', 'citations'}), 'Outcome')
                if type(outcome['kind']) is not str or outcome['kind'] not in {'reported', 'measured', 'predicted'}:
                    raise LessonLedgerError('Outcome kind must be reported, measured or predicted')
                citations = outcome['citations']
                if type(citations) is not list or len(citations) > 128:
                    raise LessonLedgerError('Outcome citations requires a bounded list')
                if not citations and outcome['kind'] != 'predicted':
                    raise LessonLedgerError('Reported and measured outcomes require cited evidence')
                result['outcomes'].append({
                    'kind': outcome['kind'], 'description': _text(outcome['description'], 'Outcome description'),
                    'method': _text(outcome['method'], 'Outcome method'),
                    'citations': [self._citation(citation) for citation in citations],
                })
        raw = _encoded(result)
        if len(raw.encode('utf-8')) > _MAX_RECORD_BYTES:
            raise LessonLedgerError('Lesson record exceeds the size limit')
        return result

    @staticmethod
    def _decode(raw: str) -> dict:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise LessonIntegrityError('Stored revision contains duplicate JSON keys')
                result[key] = value
            return result

        try:
            return json.loads(raw, object_pairs_hook=pairs)
        except (TypeError, ValueError):
            raise LessonIntegrityError('Stored revision is not valid JSON') from None

    def _load(self, db: sqlite3.Connection, lesson_id: str, revision: int | None = None) -> dict | None:
        rows = db.execute(
            'SELECT revision,payload,sha256 FROM lesson_revisions WHERE lesson_id=? ORDER BY revision',
            (lesson_id,),
        ).fetchall()
        if not rows:
            if revision is not None:
                raise LessonLedgerError('Requested lesson revision does not exist')
            return None
        if len(rows) > _MAX_REVISIONS:
            raise LessonIntegrityError('Lesson revision count exceeds the limit')
        previous, selected = None, None
        for ordinal, (number, raw, digest) in enumerate(rows, 1):
            if number != ordinal or type(raw) is not str or type(digest) is not str or _hash(raw) != digest:
                raise LessonIntegrityError('Stored lesson revision or hash is inconsistent')
            event = _object(self._decode(raw), _EVENT_FIELDS, 'Stored lesson revision')
            if (type(event['schema_version']) is not int or event['schema_version'] not in {1, 2}
                    or event['lesson_id'] != lesson_id or event['revision'] != number
                    or type(event['revision']) is not int
                    or event['previous_hash'] != (previous['revision_sha256'] if previous else _ZERO_HASH)
                    or event['verification'] != 'citation_integrity_only'):
                raise LessonIntegrityError('Stored lesson revision metadata is inconsistent')
            try:
                observed = datetime.fromisoformat(event['created_at'])
                if observed.tzinfo is None:
                    raise ValueError
            except (TypeError, ValueError):
                raise LessonIntegrityError('Stored lesson revision timestamp is invalid') from None
            if previous is not None and event['schema_version'] < previous['schema_version']:
                raise LessonIntegrityError('Stored lesson schema version moved backwards')
            if self._record(event['record'], schema_version=event['schema_version']) != event['record']:
                raise LessonIntegrityError('Stored lesson record does not match its contract')
            if any(item['record_id'] == lesson_id for item in event['record'].get('investigation_refs', [])):
                raise LessonIntegrityError('A lesson cannot reference itself as an investigation')
            if event['operation'] == 'save':
                if event['status'] != 'proposed' or event['review'] is not None:
                    raise LessonIntegrityError('New lesson wording must be proposed')
            elif event['operation'] == 'review':
                if (previous is None or event['record'] != previous['record'] or event['status'] not in DECISIONS
                        or event['schema_version'] != previous['schema_version']):
                    raise LessonIntegrityError('Review changed the immutable lesson wording')
                review = _object(event['review'], frozenset({'decision', 'reviewer', 'reason', 'provenance'}), 'Review')
                reviewer = review['reviewer']
                if type(reviewer) is dict and reviewer.get('kind') == 'operator':
                    _object(reviewer, frozenset({'kind', 'name'}), 'Reviewer')
                    _text(reviewer['name'], 'Reviewer', 512)
                else:
                    _identity(reviewer)
                _text(review['reason'], 'Review reason')
                if review['decision'] != event['status'] or review['provenance'] != 'operator_supplied':
                    raise LessonIntegrityError('Review provenance does not match the contract')
            else:
                raise LessonIntegrityError('Unsupported revision operation')
            previous = dict(event, revision_sha256=digest)
            if revision is None or revision == number:
                selected = previous
        if selected is None:
            raise LessonLedgerError('Requested lesson revision does not exist')
        return selected

    def validate_investigations(self, db: sqlite3.Connection, lesson_event: dict) -> list[dict]:
        """Resolve fresh reviewed findings in the caller's existing transaction.

        This is a dynamic prerequisite, deliberately separate from historical
        record validation: later corrections must not make past lessons unreadable.
        A reviewed finding retains its accepted, disputed or rejected assessment.
        """
        if not db.in_transaction:
            raise LessonLedgerError('Investigation validation requires one ledger transaction')
        if (type(lesson_event) is not dict or type(lesson_event.get('schema_version')) is not int
                or lesson_event['schema_version'] != 2
                or type(lesson_event.get('record')) is not dict
                or lesson_event['record'].get('kind') != 'proposed_lesson'):
            raise LessonLedgerError('A lesson requires a new version-2 proposal with reviewed investigations')
        lesson_id = _identifier(lesson_event.get('lesson_id'))
        references = _investigation_refs(lesson_event['record'].get('investigation_refs'))
        investigations = []
        for reference in references:
            record_id = reference['record_id']
            if record_id == lesson_id:
                raise LessonLedgerError('A lesson cannot reference itself as an investigation')
            current = self._load(db, record_id)
            if (current is None or current['revision'] != reference['revision']
                    or current['revision_sha256'] != reference['revision_sha256']):
                raise LessonConflict('Investigation changed or is missing; select its current reviewed revision again')
            if (current['record']['kind'] == 'proposed_lesson' or current['operation'] != 'review'
                    or current['status'] not in DECISIONS):
                raise LessonIntegrityError('Investigation references require reviewed non-lesson records')
            investigations.append(current)
        return sorted(investigations, key=lambda item: item['lesson_id'])

    @staticmethod
    def _append(db: sqlite3.Connection, event: dict) -> dict:
        raw = _encoded(event)
        digest = _hash(raw)
        db.execute('INSERT INTO lesson_revisions VALUES (?,?,?,?)',
                   (event['lesson_id'], event['revision'], raw, digest))
        return dict(event, revision_sha256=digest)

    def save(self, lesson_id: str, record: dict, expected_revision: int) -> dict:
        """Append proposed wording; expected revision zero creates a new lesson."""
        lesson_id = _identifier(lesson_id)
        expected_revision = _revision(expected_revision, creation=True)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            record = self._record(record)
            previous = self._load(db, lesson_id)
            current = previous['revision'] if previous else 0
            if expected_revision != current:
                raise LessonConflict('Lesson changed; read the current revision before saving')
            if current >= _MAX_REVISIONS:
                raise LessonLedgerError('Lesson revision count exceeds the limit')
            event = {
                'schema_version': 2, 'lesson_id': lesson_id, 'revision': current + 1,
                'status': 'proposed', 'record': record, 'review': None, 'operation': 'save',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'previous_hash': previous['revision_sha256'] if previous else _ZERO_HASH,
                'verification': 'citation_integrity_only',
            }
            if record['kind'] == 'proposed_lesson':
                self.validate_investigations(db, event)
            result = self._append(db, event)
            db.commit()
            self._guard()
            return result

    def get(self, lesson_id: str, revision: int | None = None) -> dict | None:
        """Read a selected revision after validating the entire lesson history."""
        lesson_id = _identifier(lesson_id)
        if revision is not None:
            revision = _revision(revision)
        with self._connection() as db:
            db.execute('BEGIN')
            result = self._load(db, lesson_id, revision)
            db.commit()
            return result

    def list_lessons(self, limit: int = 100) -> list[dict]:
        """Return validated current revisions in stable lesson identifier order."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise LessonLedgerError('Lesson list limit must be between 1 and 1000')
        with self._connection() as db:
            db.execute('BEGIN')
            identifiers = [row[0] for row in db.execute(
                'SELECT DISTINCT lesson_id FROM lesson_revisions ORDER BY lesson_id LIMIT ?', (limit,)
            )]
            result = [self._load(db, identifier) for identifier in identifiers]
            db.commit()
            return result

    def review(self, lesson_id: str, expected_revision: int, decision: str,
               reviewer: dict | str, reason: str) -> dict:
        """Append an operator-supplied assessment without asserting factual truth."""
        lesson_id = _identifier(lesson_id)
        expected_revision = _revision(expected_revision)
        if type(decision) is not str or decision not in DECISIONS:
            raise LessonLedgerError('Review decision must be accepted, disputed or rejected')
        reviewer = _identity(reviewer, reviewer=True)
        reason = _text(reason, 'Review reason')
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = self._load(db, lesson_id)
            if previous is None or previous['revision'] != expected_revision:
                raise LessonConflict('Lesson changed; read the current revision before review')
            if expected_revision >= _MAX_REVISIONS:
                raise LessonLedgerError('Lesson revision count exceeds the limit')
            if decision == 'accepted' and previous['record']['kind'] == 'proposed_lesson':
                self.validate_investigations(db, previous)
            event = {
                'schema_version': previous['schema_version'], 'lesson_id': lesson_id,
                'revision': expected_revision + 1,
                'status': decision, 'record': previous['record'], 'operation': 'review',
                'review': {'decision': decision, 'reviewer': reviewer, 'reason': reason,
                           'provenance': 'operator_supplied'},
                'created_at': datetime.now(timezone.utc).isoformat(),
                'previous_hash': previous['revision_sha256'], 'verification': 'citation_integrity_only',
            }
            result = self._append(db, event)
            db.commit()
            self._guard()
            return result

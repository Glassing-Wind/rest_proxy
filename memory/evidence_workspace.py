"""Private originals and review history, separate from FIRE retention and indexes."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import zipfile

from memory.fire_store import encoded
from graphrag_core.evidence_workspace.contracts import (
    MAX_ARCHIVE_JSON_BYTES, MAX_BACKUP_BYTES, MAX_CASES, MAX_EVENTS,
    MAX_METADATA_BYTES, MAX_REVISION, MAX_SOURCE_BYTES, MAX_TOTAL_SOURCE_BYTES,
    REVIEW_DECISIONS, TEXT_MEDIA, WorkspaceConflict, WorkspaceError,
    WorkspaceIntegrityError, digest, finite_json, identifier, object_fields,
    read_json, text, timestamp, validate_event, validate_package,
)

_TABLES = {
    'cases': {'case_id', 'baseline', 'baseline_hash', 'revision', 'imported_at', 'origin_case_id'},
    'sources': {'case_id', 'source_id', 'content'},
    'events': {'case_id', 'revision', 'payload', 'payload_hash'},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _markdown(value) -> str:
    value = str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    value = value.replace(chr(96), '\\' + chr(96))
    return re.sub(r'([\\*_{}\[\]()#!|])', r'\\\1', value).replace('\r', '').replace('\n', ' ')


def _check_description(source: dict) -> str:
    event = source.get('check')
    if event is None:
        return 'not checked'
    compared = event['current_sha256'] or 'missing marker'
    return f"{event['created_at']}; revision {event['revision']}; compared {compared}"


class EvidenceWorkspaceStore:
    """Immutable case baselines, originals, and append-only revision-checked review."""

    def __init__(self, state_directory: str):
        path = Path(state_directory).expanduser()
        if not path.is_absolute() or '..' in path.parts:
            raise WorkspaceError('State requires an explicit absolute canonical private directory')
        self.directory = path
        self.path = path / 'workspaces.sqlite3'
        self._guard()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._guard()
        with self._connection():
            pass

    def _guard(self) -> None:
        for component in (*reversed(self.directory.parents), self.directory):
            try:
                info = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise WorkspaceError('State directory components must not be symlinks')
        if self.directory.exists():
            info = self.directory.stat()
            if stat.S_IMODE(info.st_mode) != 0o700:
                raise WorkspaceError('State directory must have mode 700')
            if hasattr(os, 'getuid') and info.st_uid != os.getuid():
                raise WorkspaceError('State directory must belong to the current user')
        for name in ('workspaces.sqlite3', 'workspaces.sqlite3-journal',
                     'workspaces.sqlite3-wal', 'workspaces.sqlite3-shm'):
            try:
                info = (self.directory / name).lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
                raise WorkspaceError('State files must be private regular files with mode 600 and one link')

    @contextmanager
    def _connection(self):
        self._guard()
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(descriptor)
        self._guard()
        db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if version == 0 and not tables:
                db.executescript(
                    'BEGIN IMMEDIATE;'
                    'CREATE TABLE cases (case_id TEXT PRIMARY KEY, baseline TEXT NOT NULL, '
                    'baseline_hash TEXT NOT NULL, revision INTEGER NOT NULL, imported_at TEXT NOT NULL, '
                    'origin_case_id TEXT NOT NULL);'
                    'CREATE TABLE sources (case_id TEXT NOT NULL, source_id TEXT NOT NULL, content BLOB NOT NULL, '
                    'PRIMARY KEY(case_id,source_id), FOREIGN KEY(case_id) REFERENCES cases(case_id));'
                    'CREATE TABLE events (case_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, '
                    'payload_hash TEXT NOT NULL, PRIMARY KEY(case_id,revision), '
                    'FOREIGN KEY(case_id) REFERENCES cases(case_id));'
                    'PRAGMA user_version=1;COMMIT;')
                version, tables = 1, set(_TABLES)
            if version != 1 or tables != set(_TABLES):
                raise WorkspaceIntegrityError('Unsupported workspace schema')
            for table, columns in _TABLES.items():
                if {row[1] for row in db.execute(f'PRAGMA table_info({table})')} != columns:
                    raise WorkspaceIntegrityError('Workspace columns do not match the schema')
            self._guard()
            yield db
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    @staticmethod
    def _load(db, case_id: str) -> dict:
        identifier(case_id)
        row = db.execute('SELECT baseline,baseline_hash,revision,imported_at,origin_case_id '
                         'FROM cases WHERE case_id=?', (case_id,)).fetchone()
        if row is None:
            raise WorkspaceError('Case not found')
        raw, expected, revision, imported_at, origin = row
        if not isinstance(raw, str) or digest(raw.encode()) != expected:
            raise WorkspaceIntegrityError('Case metadata hash mismatch')
        baseline = json.loads(raw)
        contents = dict(db.execute('SELECT source_id,content FROM sources WHERE case_id=?', (case_id,)))
        stored = {s['id']: s for s in baseline['sources'] if s['availability'] == 'stored'}
        if set(contents) != set(stored):
            raise WorkspaceIntegrityError('Original source set does not match case metadata')
        for source_id, content in contents.items():
            source = stored[source_id]
            if (not isinstance(content, bytes) or len(content) != source['bytes']
                    or digest(content) != source['sha256']):
                raise WorkspaceIntegrityError('Stored original hash or size mismatch')
        events = []
        for number, payload, expected_hash in db.execute(
                'SELECT revision,payload,payload_hash FROM events WHERE case_id=? ORDER BY revision', (case_id,)):
            if digest(payload.encode()) != expected_hash:
                raise WorkspaceIntegrityError('Review history hash mismatch')
            events.append(validate_event(json.loads(payload), baseline, len(events) + 2))
            if number != len(events) + 1:
                raise WorkspaceIntegrityError('Review history revision mismatch')
        if type(revision) is not int or revision != len(events) + 1 or len(events) > MAX_EVENTS:
            raise WorkspaceIntegrityError('Case head does not match review history')
        return {'case_id': case_id, 'baseline': baseline, 'contents': contents,
                'events': events, 'revision': revision, 'imported_at': imported_at,
                'origin_case_id': origin, 'baseline_hash': expected}

    def _snapshot(self, case_id: str) -> dict:
        with self._connection() as db:
            db.execute('BEGIN')
            snapshot = self._load(db, case_id)
            db.commit()
            return snapshot

    def import_package(self, package: dict) -> dict:
        baseline, contents = validate_package(package)
        raw = finite_json(baseline, MAX_METADATA_BYTES)
        checksum = digest(raw.encode())
        case_id = baseline['case_id']
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT baseline_hash FROM cases WHERE case_id=?', (case_id,)).fetchone()
            if row is not None:
                if row[0] != checksum:
                    raise WorkspaceConflict('Case exists; import a new case ID instead of replacing originals')
                snapshot = self._load(db, case_id)
                db.commit()
                return {'status': 'already-imported', 'case_id': case_id, 'revision': snapshot['revision']}
            if db.execute('SELECT count(*) FROM cases').fetchone()[0] >= MAX_CASES:
                raise WorkspaceError('Workspace case limit reached')
            db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)', (case_id, raw, checksum, 1, _now(), case_id))
            db.executemany('INSERT INTO sources VALUES (?,?,?)',
                           [(case_id, source_id, content) for source_id, content in contents.items()])
            db.commit()
        return {'status': 'imported', 'case_id': case_id, 'revision': 1}

    @staticmethod
    def _view(snapshot: dict) -> dict:
        baseline = json.loads(encoded(snapshot['baseline']))
        checks = {e['source_id']: e for e in snapshot['events'] if e['kind'] == 'source_check'}
        for source in baseline['sources']:
            source['integrity'] = 'matches-stored-hash' if source['availability'] == 'stored' else 'unavailable'
            source['freshness'] = checks.get(source['id'], {}).get('freshness', 'not-checked')
            source['check'] = checks.get(source['id'])
        for relation in baseline['relationships']:
            history = [e for e in snapshot['events']
                       if e['kind'] == 'review' and e['relationship_id'] == relation['id']]
            relation['history'] = history
            relation['review'] = history[-1] if history else {'decision': 'unreviewed'}
            effective = relation['statement']
            for event in history:
                if event['decision'] == 'amended':
                    effective = event['statement']
            relation['effective_statement'] = effective
        baseline.update(case_id=snapshot['case_id'], origin_case_id=snapshot['origin_case_id'],
                        revision=snapshot['revision'], imported_at=snapshot['imported_at'])
        baseline['coverage']['stored_source_count'] = len(snapshot['contents'])
        baseline['coverage']['unavailable_source_count'] = len(baseline['sources']) - len(snapshot['contents'])
        expected = baseline['coverage'].get('expected_source_count', len(baseline['sources']))
        baseline['coverage']['unlisted_source_count'] = max(0, expected - len(baseline['sources']))
        return baseline

    def get_case(self, case_id: str) -> dict:
        return self._view(self._snapshot(case_id))

    def list_cases(self) -> dict:
        with self._connection() as db:
            ids = [row[0] for row in db.execute('SELECT case_id FROM cases ORDER BY case_id')]
        cases = []
        for case_id in ids:
            view = self.get_case(case_id)
            cases.append({key: view[key] for key in ('case_id', 'title', 'revision', 'coverage')})
            cases[-1].update(source_count=len(view['sources']), relationship_count=len(view['relationships']),
                             unreviewed_count=sum(r['review']['decision'] == 'unreviewed' for r in view['relationships']))
        return {'cases': cases}

    def get_source(self, case_id: str, source_id: str, start_line: int = 1, max_lines: int = 200) -> dict:
        identifier(source_id)
        if type(start_line) is not int or start_line < 1 or type(max_lines) is not int or not 1 <= max_lines <= 200:
            raise WorkspaceError('Source pages require a positive first line and 1..200 lines')
        snapshot = self._snapshot(case_id)
        source = next((s for s in self._view(snapshot)['sources'] if s['id'] == source_id), None)
        if source is None:
            raise WorkspaceError('Source not found in this case')
        if source['availability'] == 'stored' and source['media_type'] in TEXT_MEDIA:
            lines = snapshot['contents'][source_id].decode().splitlines()
            if start_line > max(1, len(lines)):
                raise WorkspaceError('Source page starts outside the original')
            selected = lines[start_line - 1:start_line - 1 + max_lines]
            source['numbered_lines'] = [{'number': start_line + index, 'text': line}
                                       for index, line in enumerate(selected)]
            source['next_line'] = start_line + len(selected) if start_line + len(selected) <= len(lines) else None
        else:
            source.update(numbered_lines=[], next_line=None)
        return source

    def get_source_content(self, case_id: str, source_id: str) -> tuple[bytes, str]:
        identifier(source_id)
        snapshot = self._snapshot(case_id)
        source = next((s for s in snapshot['baseline']['sources'] if s['id'] == source_id), None)
        if source is None or source['availability'] != 'stored':
            raise WorkspaceError('Original unavailable in this case')
        return snapshot['contents'][source_id], source['media_type']

    def _append(self, case_id: str, expected_revision: int, event: dict) -> dict:
        if type(expected_revision) is not int or not 1 <= expected_revision < MAX_REVISION:
            raise WorkspaceError('A mutation requires the current positive revision')
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            snapshot = self._load(db, case_id)
            if snapshot['revision'] != expected_revision:
                raise WorkspaceConflict('Case changed; inspect its latest revision before reviewing again')
            if len(snapshot['events']) >= MAX_EVENTS:
                raise WorkspaceError('Case review/check history limit reached')
            event = dict(event, revision=expected_revision + 1, created_at=_now())
            validate_event(event, snapshot['baseline'], expected_revision + 1)
            raw = finite_json(event, 12000)
            # Every committed revision must still fit the recovery packet. The
            # per-event limit alone does not bound the aggregate history.
            metadata = {key: snapshot[key] for key in (
                'case_id', 'baseline', 'events', 'revision', 'imported_at', 'origin_case_id', 'baseline_hash')}
            metadata.update(events=[*snapshot['events'], event], revision=expected_revision + 1)
            finite_json(dict(schema_version=1, **metadata), MAX_ARCHIVE_JSON_BYTES)
            prospective = dict(snapshot, events=metadata['events'], revision=metadata['revision'])
            if len(self._report(prospective).encode()) > MAX_ARCHIVE_JSON_BYTES:
                raise WorkspaceError('Reviewed report exceeds its recovery byte budget')
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (case_id, expected_revision + 1, raw, digest(raw.encode())))
            db.execute('UPDATE cases SET revision=? WHERE case_id=?', (expected_revision + 1, case_id))
            db.commit()
        return {'status': 'recorded', 'case_id': case_id, 'revision': expected_revision + 1, **event}

    def review(self, case_id: str, relationship_id: str, decision: str, reviewer: str,
               reason: str, expected_revision: int, statement: str | None = None) -> dict:
        identifier(relationship_id)
        if not isinstance(decision, str) or decision not in REVIEW_DECISIONS:
            raise WorkspaceError('Require accepted, rejected or amended review')
        event = {'kind': 'review', 'relationship_id': relationship_id, 'decision': decision,
                 'reviewer': text(reviewer, 128), 'reason': text(reason, 4000)}
        if decision == 'amended':
            event['statement'] = text(statement, 4000)
        elif statement is not None:
            raise WorkspaceError('Replacement text is permitted only for amendments')
        return self._append(case_id, expected_revision, event)

    def check_source(self, case_id: str, source_id: str, expected_revision: int,
                     content: bytes | None = None, missing: bool = False) -> dict:
        if type(missing) is not bool or (missing and content is not None):
            raise WorkspaceError('Supply a current byte copy or an explicit missing marker')
        if not missing and (not isinstance(content, bytes) or len(content) > MAX_SOURCE_BYTES):
            raise WorkspaceError('Current source byte copy is invalid or oversized')
        source = self.get_source(case_id, source_id)
        if source['availability'] != 'stored':
            raise WorkspaceError('Cannot compare an unavailable original')
        current = None if missing else digest(content)
        freshness = 'missing' if missing else 'matches-supplied-bytes' if current == source['sha256'] else 'changed'
        return self._append(case_id, expected_revision,
                            {'kind': 'source_check', 'source_id': source_id,
                             'current_sha256': current, 'freshness': freshness})

    @staticmethod
    def _report(snapshot: dict) -> str:
        view = EvidenceWorkspaceStore._view(snapshot)
        reviewed = [r for r in view['relationships'] if r['review']['decision'] in {'accepted', 'amended'}]
        unresolved = sum(r['review']['decision'] == 'unreviewed' for r in view['relationships'])
        rejected = sum(r['review']['decision'] == 'rejected' for r in view['relationships'])
        by_id = {s['id']: s for s in view['sources']}
        lines = [f"# {_markdown(view['title'])}", '', f"Case: {_markdown(view['case_id'])} · Revision: {view['revision']}",
                 '', 'This report contains reviewed historical statements. Review labels are self-reported; acceptance does not authenticate a source, identity, payment or legal conclusion.',
                 '', f"Coverage declaration: {view['coverage']['status']}. {_markdown(view['coverage']['notes'])}",
                 f"Unavailable sources: {view['coverage']['unavailable_source_count']}; unlisted expected sources: {view['coverage']['unlisted_source_count']}.",
                 f"Unreviewed relationships: {unresolved}; rejected relationships: {rejected}.",
                 '', '## Reviewed findings', '']
        if not reviewed:
            lines.append('No relationships have an accepted or amended review.')
        for relation in reviewed:
            lines += [f"### {_markdown(relation['id'])}", '', _markdown(relation['effective_statement']),
                      '', f"Evidence type: {relation['evidence_status']}; review: {relation['review']['decision']}.",
                      f"Review reason: {_markdown(relation['review']['reason'])}", '',
                      'Supporting source references:']
            for cite in relation['citations']:
                location = (f"lines {cite['start_line']}–{cite['end_line']}" if 'start_line' in cite
                            else 'image region; transcription unverified')
                current = by_id[cite['source_id']]['freshness']
                checked = _check_description(by_id[cite['source_id']])
                lines.append(f"- {_markdown(cite['source_id'])}: {location}; supplied-copy check: {current}; {checked}.")
            lines.append('')
        lines += ['## Exhibit index', '', '| Source | Kind | SHA256 | Availability | Supplied-copy check | Last operator check |',
                  '|---|---|---|---|---|---|']
        for source in view['sources']:
            lines.append(f"| {_markdown(source['id'])}: {_markdown(source['label'])} | {_markdown(source['kind'])} | "
                         f"{source['sha256'] or 'unknown'} | {source['availability']} | {source['freshness']} | "
                         f"{_check_description(source)} |")
        lines += ['', 'Original bytes and retired review statements are excluded from this report. The separate recovery archive contains all originals and history.',
                  'Hashes establish snapshot consistency. Current-copy checks compare operator-supplied bytes, not an independently monitored live source.',
                  'Review this report for sensitive information before sharing it.', '']
        return '\n'.join(lines)

    def export_report(self, case_id: str) -> str:
        return self._report(self._snapshot(case_id))

    def export_backup(self, case_id: str) -> bytes:
        snapshot = self._snapshot(case_id)
        metadata = {key: snapshot[key] for key in (
            'case_id', 'baseline', 'events', 'revision', 'imported_at', 'origin_case_id', 'baseline_hash')}
        files = {'case.json': finite_json(dict(schema_version=1, **metadata), MAX_ARCHIVE_JSON_BYTES).encode(),
                 'report.md': self._report(snapshot).encode()}
        files.update({'originals/' + key: value for key, value in snapshot['contents'].items()})
        manifest = {'schema_version': 1, 'case_id': case_id, 'revision': snapshot['revision'],
                    'privacy': 'Contains private originals and complete review/check history',
                    'files': [{'path': name, 'bytes': len(value), 'sha256': digest(value)}
                              for name, value in sorted(files.items())]}
        files['manifest.json'] = encoded(manifest).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in sorted(files.items()):
                entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(entry, value)
        result = buffer.getvalue()
        if len(result) > MAX_BACKUP_BYTES:
            raise WorkspaceError('Recovery archive exceeds its byte budget')
        return result

    def restore_backup(self, data: bytes, case_id: str | None = None) -> dict:
        """Validate the whole packet, then atomically restore into a new case."""
        try:
            snapshot, contents = self._validate_backup(data)
        except WorkspaceIntegrityError:
            raise
        except (WorkspaceError, ValueError, TypeError, KeyError, UnicodeDecodeError,
                zipfile.BadZipFile, RuntimeError, OSError, RecursionError,
                NotImplementedError, EOFError) as error:
            raise WorkspaceIntegrityError('Invalid or inconsistent recovery archive') from error
        target = identifier(case_id) if case_id is not None else snapshot['case_id']
        raw = encoded(snapshot['baseline'])
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM cases WHERE case_id=?', (target,)).fetchone():
                raise WorkspaceConflict('Restore requires a new case ID; existing evidence is never overwritten')
            if db.execute('SELECT count(*) FROM cases').fetchone()[0] >= MAX_CASES:
                raise WorkspaceError('Workspace case limit reached')
            db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)',
                       (target, raw, digest(raw.encode()), snapshot['revision'],
                        snapshot['imported_at'], snapshot['origin_case_id']))
            db.executemany('INSERT INTO sources VALUES (?,?,?)',
                           [(target, key, content) for key, content in contents.items()])
            db.executemany('INSERT INTO events VALUES (?,?,?,?)', [
                (target, event['revision'], encoded(event), digest(encoded(event).encode()))
                for event in snapshot['events']])
            db.commit()
        return {'status': 'restored', 'case_id': target, 'revision': snapshot['revision'],
                'origin_case_id': snapshot['origin_case_id']}

    @staticmethod
    def _validate_backup(data: bytes) -> tuple[dict, dict]:
        if not isinstance(data, bytes) or len(data) > MAX_BACKUP_BYTES:
            raise WorkspaceIntegrityError('Recovery archive exceeds its byte budget')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (len(entries) > 67 or len(names) != len(set(names))
                    or not {'case.json', 'manifest.json', 'report.md'} <= set(names)):
                raise WorkspaceIntegrityError('Unexpected recovery archive entries')
            if sum(e.file_size for e in entries) > MAX_TOTAL_SOURCE_BYTES + MAX_ARCHIVE_JSON_BYTES * 2 + MAX_METADATA_BYTES:
                raise WorkspaceIntegrityError('Expanded recovery archive exceeds its budget')
            for entry in entries:
                allowed = entry.filename in {'case.json', 'manifest.json', 'report.md'}
                if entry.filename.startswith('originals/'):
                    identifier(entry.filename.removeprefix('originals/'))
                    allowed = True
                file_type = stat.S_IFMT(entry.external_attr >> 16)
                if (not allowed or entry.is_dir() or entry.flag_bits & 1
                        or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or file_type not in (0, stat.S_IFREG)
                        or entry.file_size > (MAX_SOURCE_BYTES if entry.filename.startswith('originals/')
                                              else MAX_ARCHIVE_JSON_BYTES)):
                    raise WorkspaceIntegrityError('Unsafe or oversized recovery entry')
            files = {entry.filename: archive.read(entry) for entry in entries}
        manifest_bytes = files.pop('manifest.json')
        if len(manifest_bytes) > MAX_METADATA_BYTES:
            raise WorkspaceIntegrityError('Archive manifest exceeds its byte budget')
        manifest = read_json(manifest_bytes)
        object_fields(manifest, {'schema_version', 'case_id', 'revision', 'privacy', 'files'})
        if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
            raise WorkspaceIntegrityError('Unsupported archive manifest')
        identifier(manifest['case_id'])
        if type(manifest['revision']) is not int or not 1 <= manifest['revision'] <= MAX_REVISION:
            raise WorkspaceIntegrityError('Invalid archive manifest revision')
        if not isinstance(manifest['files'], list) or len(manifest['files']) != len(files):
            raise WorkspaceIntegrityError('Archive manifest does not cover its entries')
        seen = set()
        for item in manifest['files']:
            object_fields(item, {'path', 'bytes', 'sha256'})
            name = item['path']
            if (name not in files or name in seen or type(item['bytes']) is not int
                    or len(files[name]) != item['bytes'] or digest(files[name]) != item['sha256']):
                raise WorkspaceIntegrityError('Archive entry hash, size or identity mismatch')
            seen.add(name)
        snapshot = read_json(files['case.json'])
        object_fields(snapshot, {'schema_version', 'case_id', 'baseline', 'events', 'revision',
                                 'imported_at', 'origin_case_id', 'baseline_hash'})
        if type(snapshot['schema_version']) is not int or snapshot['schema_version'] != 1:
            raise WorkspaceIntegrityError('Unsupported recovery snapshot')
        identifier(snapshot['case_id'])
        identifier(snapshot['origin_case_id'])
        timestamp(snapshot['imported_at'])
        baseline = snapshot['baseline']
        if baseline['case_id'] != snapshot['origin_case_id'] or digest(encoded(baseline).encode()) != snapshot['baseline_hash']:
            raise WorkspaceIntegrityError('Recovery baseline or original case scope mismatch')
        contents = {name.removeprefix('originals/'): value for name, value in files.items()
                    if name.startswith('originals/')}
        package = {key: json.loads(encoded(baseline[key])) for key in (
            'schema_version', 'case_id', 'title', 'coverage', 'entities', 'relationships')}
        for relation in package['relationships']:
            relation['citations'] = [
                {key: value for key, value in cite.items()
                 if key in {'source_id', 'start_line', 'end_line', 'quote', 'bbox'}}
                for cite in relation['citations']]
        package['sources'] = []
        for source in baseline['sources']:
            item = {key: source[key] for key in ('id', 'label', 'kind', 'media_type', 'availability',
                                                'sha256', 'observed_at', 'original_reference', 'source_revision')}
            if source['availability'] == 'stored':
                item['content_base64'] = base64.b64encode(contents[source['id']]).decode()
            package['sources'].append(item)
        normalized, originals = validate_package(package)
        if normalized != baseline or set(originals) != set(contents):
            raise WorkspaceIntegrityError('Restored metadata/citations do not match original bytes')
        events = snapshot['events']
        if (not isinstance(events, list) or len(events) > MAX_EVENTS
                or type(snapshot['revision']) is not int or snapshot['revision'] != len(events) + 1
                or snapshot['case_id'] != manifest['case_id'] or snapshot['revision'] != manifest['revision']):
            raise WorkspaceIntegrityError('Recovery revision/history mismatch')
        for revision, event in enumerate(events, 2):
            validate_event(event, baseline, revision)
        if files['report.md'] != EvidenceWorkspaceStore._report(dict(snapshot, contents=contents)).encode():
            raise WorkspaceIntegrityError('Bundled report does not match the reviewed recovery state')
        return snapshot, contents

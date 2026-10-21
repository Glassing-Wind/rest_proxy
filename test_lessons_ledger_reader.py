"""Offline read-only ledger checks. No environment, services or private data needed."""
from contextlib import closing
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from graphrag_core.lessons_corpus.ledger import (
    LessonConflict, LessonIntegrityError, LessonLedger, LessonLedgerError,
)
from graphrag_core.lessons_corpus.ledger_reader import LessonLedgerReader


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class LessonLedgerReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.root = Path(self.temporary.name)
        self.path = self.root / 'ledger.sqlite3'
        self.text = 'Care includes rest. Café 🌱.\n'
        self.source = {'source_id': 'source-1', 'source_sha256': digest('original'),
                       'text_sha256': digest(self.text), 'parser_version': 'synthetic-v1',
                       'text': self.text}
        quote = 'Café 🌱'
        start = self.text.index(quote)
        self.record = {
            'kind': 'source_claim', 'statement': 'Care may include rest.',
            'author': {'kind': 'model', 'name': 'Synthetic author', 'model_id': 'synthetic/v1'},
            'context': 'A synthetic interpretation.', 'values': ['care'],
            'affected_parties': ['readers'], 'uncertainty': 'No outcome measurement.',
            'citations': [{'source_id': 'source-1', 'source_sha256': self.source['source_sha256'],
                           'text_sha256': self.source['text_sha256'], 'start_char': start,
                           'end_char': start + len(quote), 'quote': quote}],
            'counter_citations': [],
            'outcomes': [{'kind': 'predicted', 'description': 'Rest might help.',
                          'method': 'Untested hypothesis.', 'citations': []}],
        }
        self.writer = LessonLedger(self.path, self.resolve)
        self.first = self.writer.save('care', self.record, 0)

    def tearDown(self):
        self.temporary.cleanup()

    def resolve(self, source_id):
        if source_id != self.source['source_id']:
            raise KeyError(source_id)
        return copy.deepcopy(self.source)

    def reader(self):
        return LessonLedgerReader(self.path, self.resolve)

    def snapshot(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns,
                                                   path.stat().st_mode & 0o777)
                for path in self.root.rglob('*') if path.is_file()}

    def test_reads_reopen_and_bound_current_leave_all_files_unchanged(self):
        before = self.snapshot()
        with patch.object(LessonLedger, '__init__', side_effect=AssertionError('Writable initialization')):
            reader = self.reader()
            self.assertEqual(reader.get('care'), self.first)
            self.assertEqual(reader.list_lessons(), [self.first])
            self.assertEqual(reader.read_current('care', 1, self.first['revision_sha256']), self.first)
            self.assertEqual(self.reader().get('care', 1), self.first)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(hasattr(reader, 'save'))
        self.assertFalse(hasattr(reader, 'review'))

    def test_connection_uses_read_only_uri_and_sqlite_query_only(self):
        reader = self.reader()
        connect = sqlite3.connect
        calls = []

        def inspect(*args, **kwargs):
            calls.append((args, kwargs))
            return connect(*args, **kwargs)

        before = self.snapshot()
        with patch('graphrag_core.lessons_corpus.ledger_reader.sqlite3.connect', side_effect=inspect):
            with reader._connection() as db:
                self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
                self.assertEqual(db.execute('PRAGMA trusted_schema').fetchone()[0], 0)
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute('CREATE TABLE forbidden (value TEXT)')
        self.assertTrue(calls[0][0][0].endswith('?mode=ro'))
        self.assertTrue(calls[0][1]['uri'])
        self.assertEqual(self.snapshot(), before)

    def test_missing_ledger_or_parent_creates_nothing(self):
        before = self.snapshot()
        for path in (self.root / 'missing.sqlite3', self.root / 'missing' / 'ledger.sqlite3'):
            with self.subTest(path=path):
                with self.assertRaises(LessonLedgerError):
                    LessonLedgerReader(path, self.resolve)
                self.assertFalse(path.exists())
                self.assertFalse((self.root / 'missing').exists())
        self.assertEqual(self.snapshot(), before)

    def test_empty_file_is_not_initialized(self):
        empty = self.root / 'empty.sqlite3'
        empty.touch(mode=0o600)
        before = self.snapshot()
        with self.assertRaises(LessonIntegrityError):
            LessonLedgerReader(empty, self.resolve)
        self.assertEqual(self.snapshot(), before)

    def test_revision_history_and_reviewer_kinds_remain_attributed(self):
        reviewers = [
            ({'kind': 'model', 'name': 'Synthetic critic', 'model_id': 'critic/v2'}, 'model'),
            ('Local operator label', 'operator'),
            ({'kind': 'human', 'name': 'Self-reported human'}, 'human'),
        ]
        expected = [self.first]
        for reviewer, kind in reviewers:
            current = self.writer.review('care', len(expected), 'accepted', reviewer, 'Limited interpretation.')
            expected.append(current)
            self.assertEqual(self.reader().get('care')['review']['reviewer']['kind'], kind)
        reader = self.reader()
        for revision, event in enumerate(expected, 1):
            self.assertEqual(reader.get('care', revision), event)
            self.assertEqual(event['verification'], 'citation_integrity_only')
        self.assertEqual(reader.list_lessons()[0]['record']['outcomes'][0]['kind'], 'predicted')

    def test_bound_current_refuses_stale_revision_or_hash(self):
        reader = self.reader()
        for revision, digest_value in ((2, self.first['revision_sha256']), (1, 'f' * 64)):
            with self.subTest(revision=revision, digest=digest_value):
                with self.assertRaises(LessonConflict):
                    reader.read_current('care', revision, digest_value)
        self.writer.review('care', 1, 'disputed', 'Operator', 'More context needed.')
        with self.assertRaises(LessonConflict):
            reader.read_current('care', 1, self.first['revision_sha256'])
        with self.assertRaises(LessonConflict):
            reader.read_current('missing', 1, self.first['revision_sha256'])

    def test_source_is_revalidated_on_every_read(self):
        reader = self.reader()
        self.source['source_sha256'] = 'f' * 64
        for operation in (lambda: reader.get('care'), lambda: reader.list_lessons()):
            with self.assertRaises(LessonIntegrityError):
                operation()
        self.source['source_sha256'] = digest('original')
        self.source['text'] = self.text.replace('Care', 'Harm')
        with self.assertRaises(LessonIntegrityError):
            reader.get('care')

    def test_counter_and_outcome_only_citations_are_revalidated(self):
        record = copy.deepcopy(self.record)
        record['counter_citations'], record['citations'] = record['citations'], []
        self.writer.save('counter', record, 0)
        record['outcomes'] = [{'kind': 'reported', 'description': 'A source-reported observation.',
                               'method': 'Attributed source reading.',
                               'citations': record['counter_citations']}]
        record['counter_citations'] = []
        self.writer.save('outcome', record, 0)
        reader = self.reader()
        self.source['text_sha256'] = 'f' * 64
        for lesson in ('counter', 'outcome'):
            with self.subTest(lesson=lesson):
                with self.assertRaises(LessonIntegrityError):
                    reader.get(lesson)

    def rewrite_payload(self, mutate, *, rehash=False):
        with closing(sqlite3.connect(self.path)) as db, db:
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='lesson_revisions_no_update'").fetchone()[0]
            db.execute('DROP TRIGGER lesson_revisions_no_update')
            event = json.loads(db.execute('SELECT payload FROM lesson_revisions WHERE revision=1').fetchone()[0])
            mutate(event)
            raw = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if rehash:
                db.execute('UPDATE lesson_revisions SET payload=?,sha256=? WHERE revision=1', (raw, digest(raw)))
            else:
                db.execute('UPDATE lesson_revisions SET payload=? WHERE revision=1', (raw,))
            db.execute(sql)

    def test_payload_tampering_and_rehashed_forged_citation_are_refused(self):
        self.rewrite_payload(lambda event: event['record'].update(statement='Changed.'))
        before = self.snapshot()
        with self.assertRaises(LessonIntegrityError):
            self.reader().get('care')
        self.assertEqual(self.snapshot(), before)
        self.rewrite_payload(lambda event: event['record']['citations'][0].update(quote='Fabricated.'), rehash=True)
        with self.assertRaises(LessonIntegrityError):
            self.reader().get('care')

    def test_entire_history_is_validated_even_when_older_revision_requested(self):
        self.writer.review('care', 1, 'accepted', 'Operator', 'An attributed review.')
        self.rewrite_payload(lambda event: event['record'].update(statement='A different record.'), rehash=True)
        with self.assertRaises(LessonIntegrityError):
            self.reader().get('care', 1)

    def test_unknown_schema_tables_or_removed_triggers_are_refused(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE sqlitexunexpected (value TEXT)')
        before = self.snapshot()
        with self.assertRaises(LessonIntegrityError):
            self.reader()
        self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TABLE sqlitexunexpected')
            db.execute('DROP TRIGGER lesson_revisions_no_delete')
        with self.assertRaises(LessonIntegrityError):
            self.reader()

    def test_journal_wal_and_shared_memory_sidecars_are_refused_without_changes(self):
        for suffix in ('-journal', '-wal', '-shm'):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.path) + suffix)
                sidecar.touch(mode=0o600)
                before = self.snapshot()
                with self.assertRaises(LessonLedgerError):
                    self.reader()
                self.assertEqual(self.snapshot(), before)
                sidecar.unlink()

    def test_wal_format_database_without_sidecars_is_refused(self):
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        self.assertFalse(Path(str(self.path) + '-wal').exists())
        before = self.snapshot()
        with self.assertRaises(LessonIntegrityError):
            self.reader()
        self.assertEqual(self.snapshot(), before)

    def test_noncanonical_symlink_and_hardlink_paths_are_refused(self):
        for path in (Path('relative.sqlite3'), self.root / '..' / self.root.name / 'ledger.sqlite3'):
            with self.assertRaises(LessonLedgerError):
                LessonLedgerReader(path, self.resolve)
        linked_dir = self.root / 'linked-dir'
        linked_dir.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(LessonLedgerError):
            LessonLedgerReader(linked_dir / 'ledger.sqlite3', self.resolve)
        linked = self.root / 'linked.sqlite3'
        linked.symlink_to(self.path)
        with self.assertRaises(LessonLedgerError):
            LessonLedgerReader(linked, self.resolve)
        linked.unlink()
        os.link(self.path, linked)
        with self.assertRaises(LessonLedgerError):
            self.reader()

    def test_private_directory_and_file_permissions_are_required_on_every_read(self):
        reader = self.reader()
        os.chmod(self.path, 0o644)
        with self.assertRaises(LessonLedgerError):
            reader.get('care')
        os.chmod(self.path, 0o600)
        os.chmod(self.root, 0o755)
        with self.assertRaises(LessonLedgerError):
            reader.list_lessons()
        os.chmod(self.root, 0o700)

    @unittest.skipUnless(hasattr(os, 'getuid'), 'Ownership check requires POSIX user IDs')
    def test_wrong_owner_is_refused_without_changes(self):
        before = self.snapshot()
        with patch('graphrag_core.lessons_corpus.ledger.os.getuid', return_value=os.getuid() + 1):
            with self.assertRaises(LessonLedgerError):
                self.reader()
        self.assertEqual(self.snapshot(), before)

    def test_file_replacement_during_source_resolution_is_refused(self):
        replacement = self.root / 'replacement.sqlite3'
        replacement.write_bytes(self.path.read_bytes())
        replacement.chmod(0o600)

        def replace_once(source_id):
            replacement.replace(self.path)
            return self.resolve(source_id)

        reader = LessonLedgerReader(self.path, replace_once)
        with self.assertRaises(LessonIntegrityError):
            reader.get('care')

    def test_read_transaction_refuses_sidecar_appearing_during_source_resolution(self):
        def add_sidecar(source_id):
            Path(str(self.path) + '-journal').touch(mode=0o600)
            return self.resolve(source_id)

        reader = LessonLedgerReader(self.path, add_sidecar)
        with self.assertRaises(LessonLedgerError):
            reader.get('care')

    def test_unknown_lessons_revisions_and_invalid_argument_bounds(self):
        reader = self.reader()
        self.assertIsNone(reader.get('missing'))
        for name, revision in (('missing', 1), ('care', 2), ('care', True), ('../care', None)):
            with self.assertRaises(LessonLedgerError):
                reader.get(name, revision)
        for limit in (False, 0, -1, 1001):
            with self.assertRaises(LessonLedgerError):
                reader.list_lessons(limit)
        for revision, expected_hash in ((False, 'f' * 64), (1, 'short'), (1, 'F' * 64)):
            with self.assertRaises(LessonLedgerError):
                reader.read_current('care', revision, expected_hash)

    def test_uri_reserved_characters_in_explicit_path_remain_literal(self):
        target = self.root / 'ledger ?#λ.sqlite3'
        self.path.replace(target)
        self.assertEqual(LessonLedgerReader(target, self.resolve).get('care'), self.first)

    @staticmethod
    def selection(event):
        return {name: event[name] for name in ('lesson_id', 'revision', 'revision_sha256')}

    @staticmethod
    def reference(event, role='context'):
        return {'record_id': event['lesson_id'], 'revision': event['revision'],
                'revision_sha256': event['revision_sha256'], 'role': role}

    def proposal(self, identifier, references):
        record = copy.deepcopy(self.record)
        record['kind'] = 'proposed_lesson'
        record['investigation_refs'] = references
        return self.writer.save(identifier, record, 0)

    def test_investigated_read_binds_lessons_and_distinct_sorted_findings_in_one_snapshot(self):
        care = self.writer.review('care', 1, 'disputed', 'Operator', 'A disputed interpretation.')
        self.writer.save('alternative', self.record, 0)
        alternative = self.writer.review('alternative', 1, 'rejected', 'Operator', 'Rejected as written.')
        first = self.proposal('lesson-one', [self.reference(care, 'challenges'),
                                             self.reference(alternative, 'context')])
        second = self.proposal('lesson-two', [self.reference(care, 'supports')])
        reader = self.reader()
        before = self.snapshot()
        load = reader._validator._load
        snapshots = []

        def inspect_load(db, lesson_id, revision=None):
            self.assertTrue(db.in_transaction)
            self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
            snapshots.append(db)
            return load(db, lesson_id, revision)

        with patch.object(reader, '_connection', wraps=reader._connection) as connection:
            with patch.object(reader._validator, '_load', side_effect=inspect_load):
                result = reader.read_investigated([self.selection(second), self.selection(first)])
        self.assertEqual(connection.call_count, 1)
        self.assertEqual(len(snapshots), 5)
        self.assertTrue(all(db is snapshots[0] for db in snapshots))
        self.assertEqual(result, {'lessons': [second, first], 'investigations': [alternative, care]})
        self.assertEqual([event['status'] for event in result['investigations']], ['rejected', 'disputed'])
        self.assertEqual(self.snapshot(), before)

    def test_investigated_read_refuses_legacy_and_nonlesson_but_general_history_remains_readable(self):
        record = copy.deepcopy(self.record)
        record['kind'] = 'proposed_lesson'
        event = {
            'schema_version': 1, 'lesson_id': 'legacy', 'revision': 1,
            'status': 'proposed', 'record': record, 'review': None, 'operation': 'save',
            'created_at': '2026-01-01T00:00:00+00:00', 'previous_hash': '0' * 64,
            'verification': 'citation_integrity_only',
        }
        with self.writer._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            legacy = self.writer._append(db, event)
            db.commit()
        reader = self.reader()
        before = self.snapshot()
        for invalid in (self.first, legacy):
            with self.subTest(kind=invalid['record']['kind'], version=invalid['schema_version']):
                self.assertEqual(reader.get(invalid['lesson_id']), invalid)
                self.assertEqual(reader.read_selected([self.selection(invalid)]), [invalid])
                with self.assertRaises(LessonLedgerError):
                    reader.read_investigated([self.selection(invalid)])
        self.assertEqual(self.snapshot(), before)

    def test_investigated_read_refuses_changed_finding_but_keeps_lesson_history_readable(self):
        finding = self.writer.review('care', 1, 'accepted', 'Operator', 'A limited finding.')
        lesson = self.proposal('lesson', [self.reference(finding)])
        reader = self.reader()
        self.assertEqual(reader.read_investigated([self.selection(lesson)])['investigations'], [finding])
        changed = copy.deepcopy(self.record)
        changed['statement'] = 'A corrected finding with a narrower interpretation.'
        self.writer.save('care', changed, 2)
        before = self.snapshot()
        self.assertEqual(reader.get('lesson'), lesson)
        self.assertEqual(reader.read_selected([self.selection(lesson)]), [lesson])
        with self.assertRaises(LessonConflict):
            reader.read_investigated([self.selection(lesson)])
        self.assertEqual(self.snapshot(), before)

    def test_investigated_read_checks_finding_source_spans_even_when_lesson_has_no_citation(self):
        finding = self.writer.review('care', 1, 'accepted', 'Operator', 'An attributed reading.')
        record = copy.deepcopy(self.record)
        record.update(kind='proposed_lesson', investigation_refs=[self.reference(finding)], citations=[])
        lesson = self.writer.save('lesson', record, 0)
        reader = self.reader()
        self.source['source_sha256'] = 'f' * 64
        self.assertEqual(reader.get('lesson'), lesson)
        with self.assertRaises(LessonIntegrityError):
            reader.read_investigated([self.selection(lesson)])

    def test_investigated_read_preflights_selection_bounds_without_opening_database(self):
        reader = self.reader()
        good = self.selection(self.first)
        with patch.object(reader, '_connection', side_effect=AssertionError('Invalid input reached SQLite')):
            for invalid in ([], [good] * 11, [good, good], [dict(good, revision=True)]):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(LessonLedgerError):
                        reader.read_investigated(invalid)

    def test_selected_lessons_share_one_transaction_and_preserve_selected_order(self):
        second = self.writer.save('another', self.record, 0)
        reader = self.reader()
        before = self.snapshot()
        load = reader._validator._load
        snapshots = []

        def inspect_load(db, lesson_id, revision=None):
            snapshots.append(db)
            self.assertTrue(db.in_transaction)
            return load(db, lesson_id, revision)

        with patch.object(reader, '_connection', wraps=reader._connection) as connection:
            with patch.object(reader._validator, '_load', side_effect=inspect_load):
                result = reader.read_selected([self.selection(second), self.selection(self.first)])
        self.assertEqual(result, [second, self.first])
        self.assertEqual(connection.call_count, 1)
        self.assertEqual(len(snapshots), 2)
        self.assertIs(snapshots[0], snapshots[1])
        self.assertEqual(self.snapshot(), before)

    def test_selected_lessons_preflight_strict_bounds_fields_and_duplicates(self):
        reader = self.reader()
        good = self.selection(self.first)
        invalid = [None, (), [], [good] * 11, [good, good],
                   [dict(good, unexpected=True)], [dict(good, revision=True)],
                   [dict(good, revision=0)], [dict(good, revision_sha256='F' * 64)],
                   [dict(good, lesson_id='../care')], [{'lesson_id': 'care'}]]
        before = self.snapshot()
        with patch.object(reader, '_connection', side_effect=AssertionError('Invalid input reached SQLite')):
            for selections in invalid:
                with self.subTest(selections=selections):
                    with self.assertRaises(LessonLedgerError):
                        reader.read_selected(selections)
        self.assertEqual(self.snapshot(), before)

    def test_selected_lessons_refuse_any_stale_missing_or_changed_source(self):
        second = self.writer.save('another', self.record, 0)
        reader = self.reader()
        good = self.selection(self.first)
        stale = self.selection(second)
        self.writer.review('another', 1, 'disputed', 'Operator', 'New assessment.')
        before = self.snapshot()
        for invalid in (stale, dict(good, revision_sha256='f' * 64),
                        dict(good, lesson_id='missing')):
            with self.subTest(invalid=invalid):
                with self.assertRaises(LessonConflict):
                    reader.read_selected([good, invalid] if invalid['lesson_id'] != 'care' else [invalid])
        self.assertEqual(self.snapshot(), before)
        self.source['source_sha256'] = 'f' * 64
        with self.assertRaises(LessonIntegrityError):
            reader.read_selected([good])


if __name__ == '__main__':
    unittest.main()

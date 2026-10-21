"""Offline lesson-ledger acceptance with synthetic sources and disposable state.

Run with .venv/bin/python test_lessons_ledger.py. No network, model, production
case records or live indexing services are required.
"""
from concurrent.futures import ThreadPoolExecutor
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
    MAX_INVESTIGATION_REFS, LessonConflict, LessonIntegrityError, LessonLedger, LessonLedgerError,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class LessonLedgerTests(unittest.TestCase):
    def setUp(self):
        temporary_root = str(Path(tempfile.gettempdir()).resolve())
        self.temporary = tempfile.TemporaryDirectory(prefix='lessons-ledger-', dir=temporary_root)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'lessons.sqlite3'
        texts = {
            'source-a': 'Care can include rest.\nCafé 🌱 and service.\n',
            'source-b': 'Service without rest may cause harm.\n',
        }
        self.sources = {
            key: {'source_id': key, 'source_sha256': digest('original-' + text),
                  'text_sha256': digest(text), 'parser_version': 'synthetic-v1', 'text': text}
            for key, text in texts.items()
        }
        self.ledger = LessonLedger(self.path, self.resolve)

    def tearDown(self):
        self.temporary.cleanup()

    def resolve(self, source_id):
        return copy.deepcopy(self.sources[source_id])

    def citation(self, source_id='source-a', quote='Care can include rest.'):
        source = self.sources[source_id]
        start = source['text'].index(quote)
        return {'source_id': source_id, 'source_sha256': source['source_sha256'],
                'text_sha256': source['text_sha256'], 'start_char': start,
                'end_char': start + len(quote), 'quote': quote}

    def record(self):
        return {
            'kind': 'source_claim', 'statement': 'Care includes preserving capacity for rest.',
            'author': {'kind': 'human', 'name': 'Synthetic author'},
            'context': 'An authored example, not an outcome measurement.',
            'values': ['care', 'rest'], 'affected_parties': ['source narrator'],
            'uncertainty': 'The narrative does not prove a universal causal relationship.',
            'citations': [self.citation()],
            'counter_citations': [self.citation('source-b', 'Service without rest may cause harm.')],
        }

    @staticmethod
    def reference(event, role='supports'):
        return {'record_id': event['lesson_id'], 'revision': event['revision'],
                'revision_sha256': event['revision_sha256'], 'role': role}

    def investigation(self, identifier='finding', decision='accepted'):
        self.ledger.save(identifier, self.record(), 0)
        return self.ledger.review(identifier, 1, decision, 'Operator', 'A bounded attributed assessment.')

    def proposal(self, references):
        record = self.record()
        record['kind'] = 'proposed_lesson'
        record['investigation_refs'] = references
        return record

    def legacy(self, identifier='legacy', *, accepted=False):
        """Seed an actual old-format event without asking the new save API to downgrade."""
        record = self.record()
        record['kind'] = 'proposed_lesson'
        event = {
            'schema_version': 1, 'lesson_id': identifier, 'revision': 1,
            'status': 'proposed', 'record': record, 'review': None, 'operation': 'save',
            'created_at': '2026-01-01T00:00:00+00:00', 'previous_hash': '0' * 64,
            'verification': 'citation_integrity_only',
        }
        with self.ledger._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = self.ledger._append(db, event)
            if accepted:
                event.update(revision=2, status='accepted', operation='review',
                             previous_hash=previous['revision_sha256'],
                             review={'decision': 'accepted', 'reviewer': {'kind': 'operator', 'name': 'Operator'},
                                     'reason': 'An old-format assessment.', 'provenance': 'operator_supplied'})
                previous = self.ledger._append(db, event)
            db.commit()
        return previous

    def test_new_lesson_requires_exact_reviewed_investigation_and_preserves_assessment(self):
        findings = [self.investigation(name, decision) for name, decision in (
            ('support', 'accepted'), ('challenge', 'disputed'), ('context', 'rejected'))]
        refs = [self.reference(event, role) for event, role in zip(
            findings, ('supports', 'challenges', 'context'))]
        saved = self.ledger.save('lesson', self.proposal(refs), 0)
        reviewed = self.ledger.review('lesson', 1, 'accepted', 'Operator', 'A limited working lesson.')
        self.assertEqual(saved['schema_version'], 2)
        self.assertEqual(reviewed['record']['investigation_refs'], refs)
        self.assertEqual([self.ledger.get(item['lesson_id'])['status'] for item in findings],
                         ['accepted', 'disputed', 'rejected'])
        self.assertEqual(self.ledger.get('lesson', 1), saved)

    def test_malformed_investigation_references_refuse_all_writes(self):
        finding = self.investigation()
        good = self.reference(finding)
        invalid = [None, [], [good] * (MAX_INVESTIGATION_REFS + 1), [good, good],
                   [dict(good, ledger='/another/private/ledger')], [dict(good, record_id='../bad')],
                   [dict(good, revision=True)], [dict(good, revision=0)],
                   [dict(good, revision_sha256='F' * 64)], [dict(good, role='proven')],
                   [dict(good, role=True)], [{'record_id': 'finding'}]]
        missing = self.proposal([good])
        del missing['investigation_refs']
        before = self.path.read_bytes()
        for record in [missing, *(self.proposal(refs) for refs in invalid)]:
            with self.subTest(record=record):
                with self.assertRaises(LessonLedgerError):
                    self.ledger.save('lesson', record, 0)
                self.assertEqual(self.path.read_bytes(), before)
        nonlesson = self.record()
        nonlesson['investigation_refs'] = []
        with self.assertRaises(LessonLedgerError):
            self.ledger.save('nonlesson', nonlesson, 0)
        self.assertIsNone(self.ledger.get('lesson'))

    def test_missing_stale_self_unreviewed_and_lesson_targets_refuse_proposals(self):
        finding = self.investigation()
        unreviewed = self.ledger.save('unreviewed', self.record(), 0)
        lesson = self.ledger.save('other-lesson', self.proposal([self.reference(finding)]), 0)
        lesson = self.ledger.review('other-lesson', 1, 'accepted', 'Operator', 'Working lesson only.')
        good = self.reference(finding)
        invalid = [dict(good, record_id='missing'), dict(good, revision=1),
                   dict(good, revision_sha256='f' * 64), dict(good, record_id='lesson'),
                   self.reference(unreviewed), self.reference(lesson)]
        before = self.path.read_bytes()
        for reference in invalid:
            with self.subTest(reference=reference):
                with self.assertRaises(LessonLedgerError):
                    self.ledger.save('lesson', self.proposal([reference]), 0)
                self.assertEqual(self.path.read_bytes(), before)

    def test_changed_investigation_blocks_acceptance_but_history_and_negative_review_work(self):
        finding = self.investigation()
        original = self.ledger.save('lesson', self.proposal([self.reference(finding)]), 0)
        changed = self.record()
        changed['statement'] = 'A correction narrows the earlier interpretation.'
        self.ledger.save('finding', changed, 2)
        before = self.path.read_bytes()
        with self.assertRaises(LessonConflict):
            self.ledger.review('lesson', 1, 'accepted', 'Operator', 'Must not accept stale dependencies.')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.ledger.get('lesson'), original)
        self.assertIn(original, self.ledger.list_lessons())
        disputed = self.ledger.review('lesson', 1, 'disputed', 'Operator', 'The underlying finding changed.')
        rejected = self.ledger.review('lesson', 2, 'rejected', 'Operator', 'Withdrawing this wording.')
        self.assertEqual(self.ledger.get('lesson', 1), original)
        self.assertEqual(self.ledger.get('lesson', 2), disputed)
        self.assertEqual(self.ledger.get('lesson'), rejected)

    def test_legacy_lessons_remain_readable_but_need_explicit_new_save_before_acceptance(self):
        legacy = self.legacy(accepted=True)
        self.assertEqual(self.ledger.get('legacy'), legacy)
        before = self.path.read_bytes()
        with self.assertRaises(LessonLedgerError):
            self.ledger.review('legacy', 2, 'accepted', 'Operator', 'New acceptance requires investigations.')
        self.assertEqual(self.path.read_bytes(), before)
        negative = self.ledger.review('legacy', 2, 'disputed', 'Operator', 'No investigation prerequisite recorded.')
        self.assertEqual(negative['schema_version'], 1)
        finding = self.investigation()
        revised = self.ledger.save('legacy', self.proposal([self.reference(finding)]), 3)
        accepted = self.ledger.review('legacy', 4, 'accepted', 'Operator', 'Explicitly revised with findings.')
        self.assertEqual(revised['schema_version'], 2)
        self.assertEqual(accepted['schema_version'], 2)
        self.assertEqual(self.ledger.get('legacy', 2), legacy)
        self.assertEqual(self.ledger.get('legacy', 3), negative)

    def test_investigation_resolution_and_append_share_one_immediate_transaction(self):
        finding = self.investigation()
        validate = self.ledger.validate_investigations
        append = self.ledger._append
        connections = []

        def check_validate(db, event):
            self.assertTrue(db.in_transaction)
            connections.append(db)
            return validate(db, event)

        def check_append(db, event):
            self.assertIs(db, connections[-1])
            self.assertTrue(db.in_transaction)
            return append(db, event)

        with patch.object(self.ledger, 'validate_investigations', side_effect=check_validate):
            with patch.object(self.ledger, '_append', side_effect=check_append):
                self.ledger.save('lesson', self.proposal([self.reference(finding)]), 0)
                self.ledger.review('lesson', 1, 'accepted', 'Operator', 'Same-transaction verification.')
        self.assertEqual(len(connections), 2)

    def test_persistence_and_reopen_preserve_proposed_record(self):
        saved = self.ledger.save('care', self.record(), 0)
        reopened = LessonLedger(self.path, self.resolve)
        self.assertEqual(reopened.get('care'), saved)
        self.assertEqual(saved['status'], 'proposed')
        self.assertEqual(saved['verification'], 'citation_integrity_only')
        self.assertEqual(saved['record']['kind'], 'source_claim')
        self.assertIsNone(saved['review'])

    def test_competing_lessons_and_countercitations_remain_distinct(self):
        first = self.ledger.save('care', self.record(), 0)
        alternative = self.record()
        alternative['statement'] = 'The proper balance depends on the situation.'
        alternative['kind'] = 'hypothesis'
        alternative['citations'], alternative['counter_citations'] = (
            alternative['counter_citations'], alternative['citations']
        )
        second = self.ledger.save('context', alternative, 0)
        self.assertEqual(self.ledger.list_lessons(), [first, second])
        self.assertNotEqual(first['record']['statement'], second['record']['statement'])
        self.assertEqual(second['record']['counter_citations'], first['record']['citations'])

    def test_save_after_review_appends_new_proposed_wording(self):
        original = self.ledger.save('care', self.record(), 0)
        accepted = self.ledger.review('care', 1, 'accepted', 'Local operator', 'Useful in this context.')
        revised = self.record()
        revised['statement'] = 'Rest may support care in some contexts.'
        newest = self.ledger.save('care', revised, 2)
        self.assertEqual(newest['status'], 'proposed')
        self.assertIsNone(newest['review'])
        self.assertEqual(self.ledger.get('care', 1), original)
        self.assertEqual(self.ledger.get('care', 2), accepted)
        self.assertEqual(newest['previous_hash'], accepted['revision_sha256'])

    def test_review_preserves_wording_and_does_not_assert_truth(self):
        original = self.ledger.save('care', self.record(), 0)
        reviewed = self.ledger.review('care', 1, 'accepted', {'kind': 'human', 'name': 'Self-reported name'},
                                      'Accepted as an interpretation; identity not authenticated.')
        self.assertEqual(reviewed['record'], original['record'])
        self.assertEqual(reviewed['record']['kind'], 'source_claim')
        self.assertEqual(reviewed['review']['provenance'], 'operator_supplied')
        self.assertEqual(reviewed['verification'], 'citation_integrity_only')
        self.assertNotIn('independently_verified', reviewed)
        self.assertNotIn('truth', reviewed)

    def test_disputed_and_rejected_reviews_keep_history(self):
        self.ledger.save('care', self.record(), 0)
        disputed = self.ledger.review('care', 1, 'disputed', 'Operator A', 'A counterexample may apply.')
        rejected = self.ledger.review('care', 2, 'rejected', 'Operator B', 'The proposed scope is too broad.')
        self.assertEqual(self.ledger.get('care', 2), disputed)
        self.assertEqual(self.ledger.get('care'), rejected)
        self.assertEqual(rejected['record']['counter_citations'], self.record()['counter_citations'])

    def test_model_author_requires_exact_model_identity(self):
        record = self.record()
        record['author'] = {'kind': 'model', 'name': 'Synthetic model'}
        with self.assertRaises(LessonLedgerError):
            self.ledger.save('model', record, 0)
        record['author']['model_id'] = 'synthetic-model/version-7'
        saved = self.ledger.save('model', record, 0)
        reviewed = self.ledger.review('model', 1, 'disputed', record['author'], 'A model-authored critique.')
        self.assertEqual(saved['record']['author']['model_id'], 'synthetic-model/version-7')
        self.assertEqual(reviewed['review']['reviewer'], record['author'])
        self.assertEqual(reviewed['review']['provenance'], 'operator_supplied')

    def test_stale_save_and_review_do_not_change_head(self):
        first = self.ledger.save('care', self.record(), 0)
        with self.assertRaises(LessonConflict):
            self.ledger.save('care', self.record(), 0)
        reviewed = self.ledger.review('care', 1, 'accepted', 'Operator', 'Limited acceptance.')
        with self.assertRaises(LessonConflict):
            self.ledger.review('care', 1, 'rejected', 'Operator', 'Stale decision.')
        self.assertEqual(self.ledger.get('care', 1), first)
        self.assertEqual(self.ledger.get('care'), reviewed)

    def test_concurrent_compare_and_swap_allows_only_one_winner(self):
        self.ledger.save('care', self.record(), 0)

        def attempt(name):
            ledger = LessonLedger(self.path, self.resolve)
            try:
                return ledger.review('care', 1, 'disputed', name, 'Concurrent interpretation.')['revision']
            except LessonConflict:
                return 'conflict'

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ['Operator A', 'Operator B']))
        self.assertCountEqual(outcomes, [2, 'conflict'])
        self.assertEqual(self.ledger.get('care')['revision'], 2)

    def test_unicode_citation_uses_characters_not_utf8_bytes(self):
        record = self.record()
        record['citations'] = [self.citation(quote='Café 🌱')]
        saved = self.ledger.save('unicode', record, 0)
        citation = saved['record']['citations'][0]
        self.assertEqual(citation['end_char'] - citation['start_char'], len('Café 🌱'))
        self.assertNotEqual(len('Café 🌱'), len('Café 🌱'.encode('utf-8')))
        self.assertEqual(self.ledger.get('unicode'), saved)

    def test_forged_quote_or_span_rolls_back(self):
        for field, forged in [('quote', 'Care guarantees happiness.'), ('end_char', 100000),
                              ('start_char', True), ('source_sha256', 'f' * 64)]:
            with self.subTest(field=field):
                record = self.record()
                record['citations'][0][field] = forged
                with self.assertRaises(LessonLedgerError):
                    self.ledger.save('care', record, 0)
                self.assertIsNone(self.ledger.get('care'))

    def test_source_original_hash_change_fails_reads_and_writes(self):
        self.ledger.save('care', self.record(), 0)
        self.sources['source-a']['source_sha256'] = 'f' * 64
        with self.assertRaises(LessonIntegrityError):
            self.ledger.get('care')
        with self.assertRaises(LessonIntegrityError):
            self.ledger.review('care', 1, 'accepted', 'Operator', 'Stale source.')
        with self.assertRaises(LessonIntegrityError):
            self.ledger.list_lessons()

    def test_changed_text_without_changed_text_hash_is_detected(self):
        self.ledger.save('care', self.record(), 0)
        self.sources['source-a']['text'] = self.sources['source-a']['text'].replace('Care', 'Harm')
        with self.assertRaises(LessonIntegrityError):
            self.ledger.get('care')

    def test_countercitation_revalidation_is_required(self):
        self.ledger.save('care', self.record(), 0)
        self.sources['source-b']['text_sha256'] = 'f' * 64
        with self.assertRaises(LessonIntegrityError):
            self.ledger.get('care')

    def test_missing_source_and_parser_provenance_are_rejected(self):
        record = self.record()
        del self.sources['source-b']
        with self.assertRaises(LessonIntegrityError):
            self.ledger.save('care', record, 0)
        self.sources['source-a'].pop('parser_version')
        record['counter_citations'] = []
        with self.assertRaises(LessonIntegrityError):
            self.ledger.save('care', record, 0)

    def test_unknown_fields_cannot_upgrade_status_or_truth(self):
        for key in ('status', 'verified', 'truth', 'independently_verified'):
            with self.subTest(key=key):
                record = self.record()
                record[key] = True
                with self.assertRaises(LessonLedgerError):
                    self.ledger.save('care', record, 0)
        self.assertEqual(self.ledger.list_lessons(), [])

    def test_review_requires_reason_and_supported_decision(self):
        saved = self.ledger.save('care', self.record(), 0)
        for decision, reason in [('accepted', ''), ('fact', 'Claimed factual upgrade.')]:
            with self.subTest(decision=decision):
                with self.assertRaises(LessonLedgerError):
                    self.ledger.review('care', 1, decision, 'Operator', reason)
        self.assertEqual(self.ledger.get('care'), saved)

    def test_failure_after_insert_rolls_back_and_allows_next_write(self):
        original = self.ledger.save('care', self.record(), 0)
        append = self.ledger._append

        def fail_after_insert(db, event):
            append(db, event)
            raise RuntimeError('Synthetic failure before commit')

        with patch.object(self.ledger, '_append', side_effect=fail_after_insert):
            with self.assertRaises(RuntimeError):
                self.ledger.review('care', 1, 'accepted', 'Operator', 'This must roll back.')
        self.assertEqual(self.ledger.get('care'), original)
        self.assertEqual(self.ledger.review('care', 1, 'accepted', 'Operator', 'Later succeeds.')['revision'], 2)

    def test_database_update_and_delete_are_refused(self):
        self.ledger.save('care', self.record(), 0)
        with closing(sqlite3.connect(self.path)) as db, db:
            for sql in ('UPDATE lesson_revisions SET sha256=?', 'DELETE FROM lesson_revisions WHERE lesson_id=?'):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql, ('care',))
        self.assertEqual(self.ledger.get('care')['revision'], 1)

    def test_payload_tamper_is_detected_even_if_triggers_are_restored(self):
        self.ledger.save('care', self.record(), 0)
        with closing(sqlite3.connect(self.path)) as db, db:
            trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='lesson_revisions_no_update'").fetchone()[0]
            db.execute('DROP TRIGGER lesson_revisions_no_update')
            raw = json.loads(db.execute('SELECT payload FROM lesson_revisions').fetchone()[0])
            raw['record']['statement'] = 'A tampered statement.'
            db.execute('UPDATE lesson_revisions SET payload=?', (json.dumps(raw),))
            db.execute(trigger)
        with self.assertRaises(LessonIntegrityError):
            self.ledger.get('care')

    def test_recomputed_payload_hash_cannot_hide_a_forged_citation(self):
        self.ledger.save('care', self.record(), 0)
        with closing(sqlite3.connect(self.path)) as db, db:
            trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='lesson_revisions_no_update'").fetchone()[0]
            db.execute('DROP TRIGGER lesson_revisions_no_update')
            event = json.loads(db.execute('SELECT payload FROM lesson_revisions').fetchone()[0])
            event['record']['citations'][0]['quote'] = 'A fabricated source quotation.'
            raw = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('UPDATE lesson_revisions SET payload=?,sha256=?', (raw, digest(raw)))
            db.execute(trigger)
        with self.assertRaises(LessonIntegrityError):
            self.ledger.get('care')

    def test_owner_only_file_and_explicit_absolute_path(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(LessonLedgerError):
            LessonLedger(Path('relative.sqlite3'), self.resolve)
        public = self.root / 'public'
        public.mkdir(mode=0o755)
        with self.assertRaises(LessonLedgerError):
            LessonLedger(public / 'lessons.sqlite3', self.resolve)
        os.chmod(self.path, 0o644)
        with self.assertRaises(LessonLedgerError):
            self.ledger.get('care')

    def test_symlinks_and_hardlinks_are_refused(self):
        link = self.root / 'linked.sqlite3'
        link.symlink_to(self.path)
        with self.assertRaises(LessonLedgerError):
            LessonLedger(link, self.resolve)
        link.unlink()
        os.link(self.path, link)
        with self.assertRaises(LessonLedgerError):
            LessonLedger(self.path, self.resolve)

    def test_unknown_lesson_revision_and_list_bounds(self):
        self.assertIsNone(self.ledger.get('missing'))
        with self.assertRaises(LessonLedgerError):
            self.ledger.get('missing', 1)
        self.ledger.save('care', self.record(), 0)
        with self.assertRaises(LessonLedgerError):
            self.ledger.get('care', 2)
        for limit in (0, -1, True, 1001):
            with self.subTest(limit=limit):
                with self.assertRaises(LessonLedgerError):
                    self.ledger.list_lessons(limit)

    def test_outcome_classifications_preserve_evidence_and_no_truth_upgrade(self):
        record = self.record()
        record['outcomes'] = [
            {'kind': 'reported', 'description': 'A source describes the relationship.',
             'method': 'Attributed source reading; not an independently verified effect.',
             'citations': [self.citation()]},
            {'kind': 'measured', 'description': 'The operator classifies a cited observation as measured.',
             'method': 'Synthetic method label; no causal claim follows from this classification.',
             'citations': [self.citation()]},
            {'kind': 'predicted', 'description': 'A different balance might produce a different effect.',
             'method': 'Author hypothesis; no observation is available.', 'citations': []},
        ]
        saved = self.ledger.save('outcomes', record, 0)
        reviewed = self.ledger.review('outcomes', 1, 'accepted', 'Operator', 'Accepting the interpretation.')
        self.assertEqual(saved['record']['outcomes'], record['outcomes'])
        self.assertEqual(reviewed['record']['outcomes'], record['outcomes'])
        self.assertEqual(reviewed['record']['kind'], 'source_claim')
        self.assertEqual(reviewed['verification'], 'citation_integrity_only')
        self.assertEqual(reviewed['review']['provenance'], 'operator_supplied')

    def test_reported_and_measured_outcomes_require_citations_and_method(self):
        for kind in ('reported', 'measured'):
            with self.subTest(kind=kind):
                record = self.record()
                outcome = {'kind': kind, 'description': 'An unsupported consequence.',
                           'method': 'Source reading', 'citations': []}
                record['outcomes'] = [outcome]
                with self.assertRaises(LessonLedgerError):
                    self.ledger.save('outcomes', record, 0)
                outcome['citations'] = [self.citation()]
                outcome['method'] = ''
                with self.assertRaises(LessonLedgerError):
                    self.ledger.save('outcomes', record, 0)
                self.assertIsNone(self.ledger.get('outcomes'))

    def test_conflicting_outcomes_survive_reopen_and_lesson_revisions(self):
        record = self.record()
        record['outcomes'] = [
            {'kind': 'reported', 'description': 'One interpretation favors service.',
             'method': 'Source A narrative', 'citations': [self.citation()]},
            {'kind': 'reported', 'description': 'A competing interpretation emphasizes harm from imbalance.',
             'method': 'Source B narrative',
             'citations': [self.citation('source-b', 'Service without rest may cause harm.')]},
        ]
        first = self.ledger.save('outcomes', record, 0)
        changed = copy.deepcopy(record)
        changed['outcomes'][0]['description'] = 'A later interpretation narrows the first consequence.'
        second = self.ledger.save('outcomes', changed, 1)
        reopened = LessonLedger(self.path, self.resolve)
        self.assertEqual(reopened.get('outcomes', 1), first)
        self.assertEqual(reopened.get('outcomes'), second)
        self.assertEqual(len(reopened.get('outcomes')['record']['outcomes']), 2)

    def test_outcome_only_citation_is_revalidated_for_source_tampering(self):
        record = self.record()
        record['counter_citations'] = []
        record['outcomes'] = [
            {'kind': 'reported', 'description': 'A source reports possible harm.',
             'method': 'Source B narrative; controller and effect not independently verified.',
             'citations': [self.citation('source-b', 'Service without rest may cause harm.')]},
        ]
        self.ledger.save('outcomes', record, 0)
        self.sources['source-b']['text'] = 'A changed source with the old hash.'
        with self.assertRaises(LessonIntegrityError):
            self.ledger.get('outcomes')
        with self.assertRaises(LessonIntegrityError):
            self.ledger.review('outcomes', 1, 'accepted', 'Operator', 'Must reject stale outcome evidence.')


if __name__ == '__main__':
    unittest.main()

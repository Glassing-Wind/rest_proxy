"""Offline research exchange acceptance; synthetic sources and private disposable state."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

from graphrag_core.lessons_corpus.corpus import canonical, sha256
from graphrag_core.lessons_corpus.exchange import (
    DESTINATION, USE, inspect_bundle, make_review_packet, read_artifact, release_bundle, write_artifact,
)
from graphrag_core.lessons_corpus.ledger import LessonLedger
from graphrag_core.lessons_corpus.ledger import _encoded, _hash
from scripts.lessons_corpus import main as command
import test_lessons_corpus as fixtures


class ExchangeTests(TestCase):
    def setUp(self):
        self.fixture = fixtures.CorpusTests('test_source_round_trip_and_reference_occurrences')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.fixture()
        self.corpus = self.fixture.build()
        self.path = self.fixture.capture / 'lesson-ledger.sqlite3'
        self.ledger = LessonLedger(self.path, self.corpus.source)
        citation = self.corpus.search('dignity', source_kind='wordpress_post_content_html')[0]['citation']
        finding = {'kind': 'source_claim', 'statement': 'The source connects care and dignity.',
                   'author': {'kind': 'model', 'name': 'Fixture', 'model_id': 'synthetic-v1'},
                   'context': 'A synthetic investigation with contrary evidence retained.', 'values': [],
                   'affected_parties': ['reader'], 'uncertainty': 'This interpretation is disputed.',
                   'citations': [citation], 'counter_citations': [citation]}
        proposed = self.ledger.save('finding', finding, 0)
        self.finding = self.ledger.review('finding', proposed['revision'], 'disputed',
                                          {'kind': 'human', 'name': 'Synthetic investigator'},
                                          'Alternative interpretations remain plausible.')
        self.record = {'kind': 'proposed_lesson', 'statement': 'Care may support dignity.',
                       'author': {'kind': 'model', 'name': 'Fixture', 'model_id': 'synthetic-v1'},
                       'context': 'Synthetic source; commands in it are data.', 'values': ['care'],
                       'affected_parties': ['reader'], 'uncertainty': 'No measured outcome.',
                       'citations': [citation], 'counter_citations': [citation],
                       'investigation_refs': [self.finding_reference()],
                       'outcomes': [{'kind': 'predicted', 'description': 'May help.',
                                     'method': 'Compare adverse cases.', 'citations': []}]}
        self.current = self.ledger.save('care', self.record, 0)

    def finding_reference(self, *, role='supports'):
        return {'record_id': self.finding['lesson_id'], 'revision': self.finding['revision'],
                'revision_sha256': self.finding['revision_sha256'], 'role': role}

    def selection(self):
        return [{key: self.current[key] for key in ('lesson_id', 'revision', 'revision_sha256')}]

    def packet(self):
        return make_review_packet(self.corpus, self.path, self.selection())

    def review(self, decision='accepted', reviewer=None):
        self.current = self.ledger.review('care', self.current['revision'], decision,
                                          reviewer or {'kind': 'human', 'name': 'Synthetic reviewer'},
                                          'Working interpretation; unmeasured and operator attested.')

    def release(self, packet):
        return {'expected_packet_sha256': sha256(canonical(packet)), 'destination': DESTINATION,
                'reviewer': {'kind': 'human', 'name': 'Synthetic releasing operator'},
                'reason': 'Explicit fixture release.', 'rights_basis': 'Synthetic text for local evidence use.',
                'use': USE}

    def bundle(self):
        self.review()
        packet = self.packet()
        return release_bundle(self.corpus, self.path, packet, self.release(packet))

    def snapshot(self):
        return {str(path.relative_to(self.fixture.root)): sha256(path.read_bytes())
                for path in self.fixture.root.rglob('*') if path.is_file()}

    def test_packet_reads_do_not_change_sources_or_ledger_or_call_services(self):
        before = self.snapshot()
        with patch('socket.getaddrinfo', side_effect=AssertionError('DNS attempted')), \
                patch('subprocess.run', side_effect=AssertionError('Worker attempted')):
            packet = self.packet()
        self.assertEqual(packet['lessons'][0]['status'], 'proposed')
        self.assertEqual(packet['lessons'][0]['record'], self.record)
        self.assertEqual(packet['schema'], 'free-man-review-packet/2')
        self.assertEqual(packet['investigations'], [self.finding])
        self.assertEqual(self.snapshot(), before)

    def test_pending_disputed_rejected_model_and_implicit_operator_reviews_cannot_release(self):
        packet = self.packet()
        with self.assertRaisesRegex(ValueError, 'human reviewer'):
            release_bundle(self.corpus, self.path, packet, self.release(packet))
        for decision, reviewer in [('disputed', {'kind': 'human', 'name': 'Human'}),
                                   ('rejected', {'kind': 'human', 'name': 'Human'}),
                                   ('accepted', {'kind': 'model', 'name': 'Model', 'model_id': 'fixture-v1'}),
                                   ('accepted', 'Unspecified operator')]:
            with self.subTest(decision=decision, reviewer=reviewer):
                self.review(decision, reviewer)
                packet = self.packet()
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'human reviewer'):
                    release_bundle(self.corpus, self.path, packet, self.release(packet))
                self.assertEqual(self.snapshot(), before)

    def test_explicit_human_acceptance_and_separate_release_preserve_dissent_and_outcomes(self):
        bundle = self.bundle()
        before = self.snapshot()
        output = self.fixture.root / 'bundle.json'
        fingerprint = write_artifact(output, bundle)
        consumer = read_artifact(output)
        inspected = inspect_bundle(consumer, fingerprint)
        self.assertEqual(consumer['payload']['packet']['lessons'][0]['record'], self.record)
        self.assertEqual(inspected['destination'], DESTINATION)
        self.assertEqual(inspected['currentness'], 'not_checked_against_source_ledger')
        self.assertEqual(inspected['constraints']['tool_permissions'], [])
        self.assertFalse(inspected['constraints']['training_allowed'])
        self.assertFalse(inspected['constraints']['authenticated_reviewer_identity'])
        self.assertEqual(inspected['schema'], 'free-man-lesson-exchange/2')
        self.assertEqual(inspected['investigations'][0]['status'], 'disputed')
        self.assertEqual(consumer['payload']['packet']['investigations'][0]['record']['uncertainty'],
                         'This interpretation is disputed.')
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
        after = self.snapshot()
        del after['bundle.json']
        self.assertEqual(after, before)

    def test_acceptance_does_not_waive_separate_packet_rights_destination_and_human_release(self):
        self.review()
        packet = self.packet()
        good = self.release(packet)
        bad = [dict(good, expected_packet_sha256='0' * 64), dict(good, destination='public-upload'),
               dict(good, rights_basis=''), dict(good, use='training'),
               dict(good, reviewer={'kind': 'model', 'name': 'M', 'model_id': 'fixture'}),
               dict(good, automatic=True)]
        for release in bad:
            with self.subTest(release=release), self.assertRaises(ValueError):
                release_bundle(self.corpus, self.path, packet, release)

    def test_review_packet_and_selection_become_stale_after_rewording(self):
        self.review()
        packet, selection = self.packet(), self.selection()
        self.current = self.ledger.save('care', dict(self.record, statement='A changed interpretation.'),
                                        self.current['revision'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            make_review_packet(self.corpus, self.path, selection)
        with self.assertRaisesRegex(ValueError, 'changed'):
            release_bundle(self.corpus, self.path, packet, self.release(packet))

    def test_changed_finding_blocks_packet_and_release_even_when_lesson_did_not_change(self):
        self.review()
        packet, lesson_hash = self.packet(), self.current['revision_sha256']
        historical = release_bundle(self.corpus, self.path, packet, self.release(packet))
        self.finding = self.ledger.review('finding', self.finding['revision'], 'rejected',
                                          {'kind': 'human', 'name': 'Synthetic investigator'},
                                          'A later review changed the finding.')
        before = self.snapshot()
        self.assertEqual(self.ledger.get('care')['revision_sha256'], lesson_hash)
        with self.assertRaisesRegex(ValueError, 'changed|current|stale'):
            self.packet()
        with self.assertRaisesRegex(ValueError, 'changed|current|stale'):
            release_bundle(self.corpus, self.path, packet, self.release(packet))
        inspected = inspect_bundle(historical, sha256(canonical(historical)))
        self.assertEqual(inspected['currentness'], 'not_checked_against_source_ledger')
        self.assertEqual(inspected['investigations'][0]['status'], 'disputed')
        self.assertEqual(self.snapshot(), before)

    def test_multiple_lessons_share_exact_findings_without_duplicate_inclusion(self):
        second = self.ledger.save('other', dict(self.record, statement='A second proposed lesson.',
                                              investigation_refs=[self.finding_reference(role='challenges')]), 0)
        selected = self.selection() + [{key: second[key] for key in ('lesson_id', 'revision', 'revision_sha256')}]
        packet = make_review_packet(self.corpus, self.path, selected)
        self.assertEqual(len(packet['lessons']), 2)
        self.assertEqual(packet['investigations'], [self.finding])
        self.assertEqual(packet['lessons'][1]['record']['investigation_refs'][0]['role'], 'challenges')

    @staticmethod
    def repin(bundle):
        payload = bundle['payload']
        for event in payload['packet']['lessons'] + payload['packet'].get('investigations', []):
            event['revision_sha256'] = _hash(_encoded({key: value for key, value in event.items()
                                                       if key != 'revision_sha256'}))
        payload['release']['expected_packet_sha256'] = sha256(canonical(payload['packet']))
        bundle['payload_sha256'] = sha256(canonical(payload))
        return sha256(canonical(bundle))

    def test_self_consistent_bundles_reject_missing_extra_duplicate_and_unbound_findings(self):
        original = self.bundle()
        for change in ('missing', 'extra', 'duplicate', 'wrong_revision', 'wrong_hash',
                       'own_lesson_id', 'unreviewed', 'lesson_kind', 'duplicate_reference',
                       'unknown_role', 'missing_reference'):
            with self.subTest(change=change):
                changed = copy.deepcopy(original)
                packet = changed['payload']['packet']
                finding = packet['investigations'][0]
                refs = packet['lessons'][0]['record']['investigation_refs']
                if change == 'missing':
                    packet['investigations'] = []
                elif change == 'extra':
                    extra = copy.deepcopy(finding)
                    extra['lesson_id'] = 'zz-unreferenced'
                    packet['investigations'].append(extra)
                elif change == 'duplicate':
                    packet['investigations'].append(copy.deepcopy(finding))
                elif change == 'wrong_revision':
                    refs[0]['revision'] += 1
                elif change == 'wrong_hash':
                    refs[0]['revision_sha256'] = '0' * 64
                elif change == 'own_lesson_id':
                    finding['lesson_id'] = refs[0]['record_id'] = 'care'
                elif change == 'unreviewed':
                    finding.update(operation='save', status='proposed', review=None)
                elif change == 'lesson_kind':
                    finding['record'] = copy.deepcopy(packet['lessons'][0]['record'])
                elif change == 'duplicate_reference':
                    refs.append(copy.deepcopy(refs[0]))
                elif change == 'unknown_role':
                    refs[0]['role'] = 'proves_true'
                else:
                    packet['lessons'][0]['record'].pop('investigation_refs')
                with self.assertRaises(ValueError):
                    inspect_bundle(changed, self.repin(changed))

    def test_rejected_finding_is_preserved_as_reviewed_context_without_claiming_truth(self):
        self.finding = self.ledger.review('finding', self.finding['revision'], 'rejected',
                                          {'kind': 'human', 'name': 'Synthetic investigator'},
                                          'Preserve the counterexample as context.')
        self.record['investigation_refs'] = [self.finding_reference(role='context')]
        self.current = self.ledger.save('care', self.record, self.current['revision'])
        bundle = self.bundle()
        inspected = inspect_bundle(bundle, sha256(canonical(bundle)))
        self.assertEqual(inspected['investigations'][0]['status'], 'rejected')
        self.assertIn('currentness_not_checked', inspected['investigation_prerequisite'])

    def test_legacy_bundle_is_inspectable_with_missing_prerequisite_but_cannot_be_released(self):
        legacy = self.bundle()
        legacy['schema'] = 'free-man-lesson-exchange/1'
        packet = legacy['payload']['packet']
        packet['schema'] = 'free-man-review-packet/1'
        packet.pop('investigations')
        for event in packet['lessons']:
            event['schema_version'] = 1
            event['record'].pop('investigation_refs')
        pinned = self.repin(legacy)
        inspected = inspect_bundle(legacy, pinned)
        self.assertEqual(inspected['schema'], 'free-man-lesson-exchange/1')
        self.assertEqual(inspected['investigation_prerequisite'], 'unavailable_in_legacy_artifact')
        self.assertEqual(inspected['currentness'], 'not_checked_against_source_ledger')
        with self.assertRaisesRegex(ValueError, 'packet contract'):
            release_bundle(self.corpus, self.path, packet, self.release(packet))
        expanded = copy.deepcopy(legacy)
        expanded['payload']['constraints']['training_allowed'] = True
        with self.assertRaisesRegex(ValueError, 'constraints'):
            inspect_bundle(expanded, self.repin(expanded))

    def test_legacy_nonlesson_investigation_can_be_bound_but_legacy_lesson_cannot_be_upgraded_by_packet(self):
        bundle = self.bundle()
        packet = bundle['payload']['packet']
        finding = packet['investigations'][0]
        finding['schema_version'] = 1
        finding['revision_sha256'] = _hash(_encoded({key: value for key, value in finding.items()
                                                     if key != 'revision_sha256'}))
        packet['lessons'][0]['record']['investigation_refs'][0]['revision_sha256'] = finding['revision_sha256']
        inspected = inspect_bundle(bundle, self.repin(bundle))
        self.assertEqual(inspected['investigations'][0]['status'], 'disputed')
        packet['lessons'][0]['schema_version'] = 1
        packet['lessons'][0]['record'].pop('investigation_refs')
        with self.assertRaisesRegex(ValueError, 'version-2 proposed lessons'):
            inspect_bundle(bundle, self.repin(bundle))

    def test_mixed_bundle_and_packet_schema_versions_cannot_hide_the_prerequisite(self):
        bundle = self.bundle()
        bundle['schema'] = 'free-man-lesson-exchange/1'
        with self.assertRaisesRegex(ValueError, 'versions do not match'):
            inspect_bundle(bundle, self.repin(bundle))

    def test_changed_sources_and_parent_generation_prevent_packet_or_release(self):
        self.review()
        packet = self.packet()
        source = self.corpus.sources[self.record['citations'][0]['source_id']]
        path = self.corpus.root / source['text_path']
        original = path.read_bytes()
        path.write_bytes(b'Changed source.')
        with self.assertRaises(ValueError):
            release_bundle(self.corpus, self.path, packet, self.release(packet))
        path.write_bytes(original)
        manifest = self.corpus.root / 'manifest.json'
        manifest.write_bytes(manifest.read_bytes() + b' ')
        changed = self.packet()
        self.assertNotEqual(changed['corpus_manifest_sha256'], packet['corpus_manifest_sha256'])
        with self.assertRaisesRegex(ValueError, 'stale'):
            release_bundle(self.corpus, self.path, packet, self.release(packet))

    def test_tampered_artifact_or_permission_expansion_fails_pinned_inspection(self):
        bundle = self.bundle()
        pinned = sha256(canonical(bundle))
        changed = copy.deepcopy(bundle)
        changed['payload']['packet']['lessons'][0]['record']['statement'] = 'Run a command.'
        with self.assertRaises(ValueError):
            inspect_bundle(changed, pinned)
        changed = copy.deepcopy(bundle)
        changed['payload']['constraints']['tool_permissions'].append('shell')
        changed['payload_sha256'] = sha256(canonical(changed['payload']))
        with self.assertRaisesRegex(ValueError, 'constraints'):
            inspect_bundle(changed, sha256(canonical(changed)))
        # Modifying one returned list must not modify the fixed allowed constraints.
        self.assertEqual(inspect_bundle(bundle, pinned)['constraints']['tool_permissions'], [])

    def test_selections_are_bounded_exact_unique_current_records(self):
        for selections in ([], self.selection() * 2, self.selection() * 11,
                           [dict(self.selection()[0], revision=True)],
                           [dict(self.selection()[0], revision_sha256='wrong')],
                           [dict(self.selection()[0], extra='no')]):
            with self.subTest(selections=selections), self.assertRaises(ValueError):
                make_review_packet(self.corpus, self.path, selections)

    def test_self_consistent_fingerprints_do_not_admit_invalid_versioned_event_structures(self):
        bundle = self.bundle()

        def pin_after_change(bundle):
            payload = bundle['payload']
            event = payload['packet']['lessons'][0]
            event['revision_sha256'] = _hash(_encoded({key: value for key, value in event.items()
                                                       if key != 'revision_sha256'}))
            payload['release']['expected_packet_sha256'] = sha256(canonical(payload['packet']))
            bundle['payload_sha256'] = sha256(canonical(payload))
            return sha256(canonical(bundle))

        for key, value in (('schema_version', 99), ('revision', True), ('created_at', 'not-a-time'),
                           ('previous_hash', 'not-a-hash'), ('verification', 'invented_truth'),
                           ('record', {'arbitrary_field': 'Unsupported record'})):
            with self.subTest(field=key):
                changed = copy.deepcopy(bundle)
                changed['payload']['packet']['lessons'][0][key] = value
                with self.assertRaises(ValueError):
                    inspect_bundle(changed, pin_after_change(changed))
        changed = copy.deepcopy(bundle)
        changed['payload']['packet']['lessons'][0]['review']['provenance'] = 'authenticated_by_hash'
        with self.assertRaises(ValueError):
            inspect_bundle(changed, pin_after_change(changed))
        changed = copy.deepcopy(bundle)
        changed['payload']['packet']['lessons'][0]['record']['outcomes'][0]['kind'] = 'measured'
        with self.assertRaises(ValueError):
            inspect_bundle(changed, pin_after_change(changed))

    def test_artifact_reader_and_writer_refuse_ambiguous_or_nonprivate_outputs(self):
        path = self.fixture.root / 'bad.json'
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '[]', 'x' * (1024 * 1024 + 1)):
            path.write_text(raw)
            with self.assertRaises(ValueError):
                read_artifact(path)
        with self.assertRaises(FileExistsError):
            write_artifact(path, self.packet())
        public = self.fixture.root / 'not-private'
        public.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, '700'):
            write_artifact(public / 'packet.json', self.packet())
        with self.assertRaises(ValueError):
            write_artifact(Path('relative.json'), self.packet())

    def test_cli_packet_summary_and_inspection_do_not_print_source_quotes(self):
        selection = self.fixture.root / 'selection.json'
        selection.write_bytes(canonical({'lessons': self.selection()}))
        packet = self.fixture.root / 'review.json'
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = command(['review-packet', '--corpus', str(self.corpus.root), '--ledger', str(self.path),
                              '--selection', str(selection), '--output', str(packet)])
        self.assertEqual(result, 0)
        summary = json.loads(stream.getvalue())
        self.assertFalse(summary['operational_state_changed'])
        self.assertNotIn(self.record['statement'], stream.getvalue())
        bundle = self.bundle()
        bundle_path = self.fixture.root / 'release.json'
        fingerprint = write_artifact(bundle_path, bundle)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(command(['inspect-bundle', '--bundle', str(bundle_path),
                                      '--expected-sha256', fingerprint]), 0)

    def test_explicit_human_review_cli_can_prepare_and_separately_release_a_fixture(self):
        reviewer = self.fixture.root / 'reviewer.json'
        write_artifact(reviewer, {'kind': 'human', 'name': 'Synthetic human operator'})
        with redirect_stdout(io.StringIO()):
            self.assertEqual(command(['review', '--corpus', str(self.corpus.root), '--lesson-id', 'care',
                                      '--expected-revision', '1', '--decision', 'accepted',
                                      '--reviewer-record', str(reviewer), '--reason', 'Fixture working lesson.']), 0)
        self.current = self.ledger.get('care')
        self.assertEqual(self.current['review']['reviewer']['kind'], 'human')
        packet = self.packet()
        packet_path = self.fixture.root / 'accepted-packet.json'
        release_path = self.fixture.root / 'release-record.json'
        bundle_path = self.fixture.root / 'released-fixture.json'
        write_artifact(packet_path, packet)
        write_artifact(release_path, self.release(packet))
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(command(['release-bundle', '--corpus', str(self.corpus.root),
                                      '--ledger', str(self.path), '--packet', str(packet_path),
                                      '--release', str(release_path), '--output', str(bundle_path)]), 0)
        inspected = inspect_bundle(read_artifact(bundle_path), json.loads(stream.getvalue())['artifact_sha256'])
        self.assertEqual(inspected['lessons'][0]['status'], 'accepted')
        self.assertEqual(inspected['constraints']['tool_permissions'], [])


if __name__ == '__main__':
    main()

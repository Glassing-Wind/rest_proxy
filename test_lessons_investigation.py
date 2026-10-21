"""Offline investigation-to-lesson CLI acceptance; disposable synthetic sources only."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from graphrag_core.lessons_corpus.corpus import canonical, sha256
from graphrag_core.lessons_corpus.ledger_reader import LessonLedgerReader
import test_lessons_corpus as fixtures


class InvestigationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CorpusTests('test_source_round_trip_and_reference_occurrences')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.fixture()
        self.corpus = self.fixture.build()
        self.path = self.fixture.capture / 'lesson-ledger.sqlite3'
        self.root = Path(__file__).resolve().parent
        self.artifact_number = 0
        self.reviewer = self.input({'kind': 'human', 'name': 'Synthetic human reviewer'})
        citation = self.corpus.search('dignity', source_kind='wordpress_post_content_html')[0]['citation']
        self.claim = {
            'kind': 'source_claim', 'statement': 'The source associates service and dignity.',
            'author': {'kind': 'model', 'name': 'Fixture', 'model_id': 'synthetic-only'},
            'context': 'A bounded synthetic interpretation, with a competing reading retained.',
            'values': ['care'], 'affected_parties': ['reader'],
            'uncertainty': 'Source wording alone establishes no measured causal effect.',
            'citations': [citation], 'counter_citations': [citation],
        }

    def input(self, value):
        self.artifact_number += 1
        path = self.fixture.root / f'input-{self.artifact_number}.json'
        path.write_bytes(canonical(value))
        path.chmod(0o600)
        return path

    def cli(self, *arguments, fails=False):
        result = subprocess.run(
            [sys.executable, '-m', 'scripts.lessons_corpus', *map(str, arguments)],
            cwd=self.root, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 1 if fails else 0, result.stdout + result.stderr)
        return json.loads(result.stderr if fails else result.stdout)

    def save(self, identifier, record, revision):
        return self.cli('propose', '--corpus', self.corpus.root, '--lesson-id', identifier,
                        '--expected-revision', revision, '--record', self.input(record))

    def review(self, identifier, revision, decision):
        return self.cli('review', '--corpus', self.corpus.root, '--lesson-id', identifier,
                        '--expected-revision', revision, '--decision', decision,
                        '--reviewer-record', self.reviewer, '--reason', 'Synthetic bounded assessment.')

    def reference(self, finding):
        return {'record_id': finding['lesson_id'], 'revision': finding['revision'],
                'revision_sha256': finding['revision_sha256'], 'role': 'context'}

    def packet(self, lesson, filename, *, fails=False):
        selected = [{key: lesson[key] for key in ('lesson_id', 'revision', 'revision_sha256')}]
        output = self.fixture.root / filename
        result = self.cli('review-packet', '--corpus', self.corpus.root, '--ledger', self.path,
                          '--selection', self.input({'lessons': selected}), '--output', output, fails=fails)
        return output, result

    def release(self, packet, filename, *, fails=False):
        approval = {'expected_packet_sha256': sha256(packet.read_bytes()),
                    'destination': 'rest-proxy:evidence-only',
                    'reviewer': {'kind': 'human', 'name': 'Synthetic releasing operator'},
                    'reason': 'Explicit synthetic fixture release.',
                    'rights_basis': 'Synthetic fixture for private local evidence use.',
                    'use': 'local_inference_evidence_only'}
        output = self.fixture.root / filename
        result = self.cli('release-bundle', '--corpus', self.corpus.root, '--ledger', self.path,
                          '--packet', packet, '--release', self.input(approval),
                          '--output', output, fails=fails)
        return output, result

    def source_snapshot(self):
        return {str(path.relative_to(self.fixture.capture)): sha256(path.read_bytes())
                for path in self.fixture.capture.rglob('*') if path.is_file() and path != self.path}

    def test_review_correction_rebinding_and_release_survive_separate_processes(self):
        sources_before = self.source_snapshot()
        self.save('finding', self.claim, 0)
        finding = self.review('finding', 1, 'disputed')
        record = dict(self.claim, kind='proposed_lesson',
                      statement='Consider service while retaining uncertainty about outcomes.',
                      investigation_refs=[self.reference(finding)])
        proposed = self.save('care', record, 0)
        pending_packet, _ = self.packet(proposed, 'pending.json')
        before = sha256(self.path.read_bytes())
        refused, _ = self.release(pending_packet, 'refused-pending.json', fails=True)
        self.assertFalse(refused.exists())
        self.assertEqual(sha256(self.path.read_bytes()), before)

        accepted = self.review('care', proposed['revision'], 'accepted')
        packet, _ = self.packet(accepted, 'accepted.json')
        bundle, receipt = self.release(packet, 'bundle.json')
        inspected = self.cli('inspect-bundle', '--bundle', bundle,
                             '--expected-sha256', receipt['artifact_sha256'])
        self.assertEqual(inspected['investigations'][0]['status'], 'disputed')
        self.assertEqual(inspected['currentness'], 'not_checked_against_source_ledger')
        self.assertFalse(inspected['constraints']['training_allowed'])

        changed = dict(self.claim, statement='Correction: the association supports no causal conclusion.')
        self.save('finding', changed, finding['revision'])
        corrected = self.review('finding', finding['revision'] + 1, 'rejected')
        before = sha256(self.path.read_bytes())
        stale_packet, _ = self.packet(accepted, 'refused-stale-packet.json', fails=True)
        stale_bundle, _ = self.release(packet, 'refused-stale-bundle.json', fails=True)
        self.assertFalse(stale_packet.exists())
        self.assertFalse(stale_bundle.exists())
        self.assertEqual(sha256(self.path.read_bytes()), before)
        reader = LessonLedgerReader(self.path, self.corpus.source)
        self.assertEqual(reader.get('care')['revision_sha256'], accepted['revision_sha256'])
        self.assertEqual(reader.get('care', 1)['record'], record)
        self.assertEqual(reader.get('finding', 2)['status'], 'disputed')
        # Historical bundle inspection makes no live-currentness claim after a correction.
        self.cli('inspect-bundle', '--bundle', bundle, '--expected-sha256', receipt['artifact_sha256'])

        rebound = copy.deepcopy(record)
        rebound['investigation_refs'] = [self.reference(corrected)]
        revised = self.save('care', rebound, accepted['revision'])
        self.assertEqual(revised['status'], 'proposed')
        revised_packet, _ = self.packet(revised, 'rebound-pending.json')
        self.release(revised_packet, 'refused-rebound-pending.json', fails=True)
        reviewed = self.review('care', revised['revision'], 'accepted')
        final_packet, _ = self.packet(reviewed, 'rebound-accepted.json')
        final_bundle, final_receipt = self.release(final_packet, 'rebound-bundle.json')
        final = self.cli('inspect-bundle', '--bundle', final_bundle,
                         '--expected-sha256', final_receipt['artifact_sha256'])
        self.assertEqual(final['investigations'][0]['status'], 'rejected')
        self.assertEqual(self.source_snapshot(), sources_before)
        reopened = self.cli('lessons', '--corpus', self.corpus.root)
        self.assertEqual({item['lesson_id']: item['revision'] for item in reopened},
                         {'care': reviewed['revision'], 'finding': corrected['revision']})


if __name__ == '__main__':
    unittest.main()

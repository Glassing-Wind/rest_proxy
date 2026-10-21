"""Offline James investigation checks using explicitly synthetic verse sources.

No network, model inference, real corpus, service, or human decision is used.
The resolver is synthetic; ledger and review-packet storage are real.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from graphrag_core.lessons_corpus.corpus import canonical, sha256
from graphrag_core.lessons_corpus.exchange import (
    DESTINATION, USE, make_review_packet, release_bundle,
)
from graphrag_core.lessons_corpus.james_investigation import (
    LESSON_ID, MODEL_IDENTITY, SOURCE_IDS, prepare_investigation,
)
from graphrag_core.lessons_corpus.ledger import LessonConflict, LessonLedger
from graphrag_core.lessons_corpus.ledger_reader import LessonLedgerReader


class SyntheticCorpus:
    """A source resolver fixture with real generation-fingerprint input files."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(mode=0o700)
        self.sources = {}
        for identifier in SOURCE_IDS:
            edition = identifier.split(':')[1]
            text, verses = 'Synthetic edition 😀\n', []
            for number in range(14, 27):
                marker = f'JAS.2.{number}'
                text += marker + '\t'
                first = len(text)
                quote = f'{edition}: synthetic James 2:{number} wording, with 😀 and \"quotes\".'
                if number == 14:
                    quote = ('Synthetic example. Can faith save him?' if edition == 'engwebp'
                             else 'Synthetic example. can that faith save him?')
                if number == 20:
                    quote = 'Synthetic faith apart from works is ' + ('dead?' if edition == 'engwebp' else 'barren?')
                text += quote
                verses.append({'id': marker, 'chapter': 2, 'verse': number,
                               'text_span': [first, len(text)], 'text': quote,
                               'source_span': [first, len(text)], 'marker_span': [first - len(marker) - 1, first - 1]})
                text += '\n'
            self.sources[identifier] = {
                'source_id': identifier, 'source_sha256': sha256(('raw:' + text).encode()),
                'text_sha256': sha256(text.encode()), 'parser_version': 'synthetic-verse-fixture/1',
                'text': text, 'ast': {'verses': verses},
                'metadata': {'edition_id': edition, 'edition_title': edition + ' synthetic fixture',
                             'rights_basis': 'Synthetic source fixture',
                             'source_url': 'https://example.test/' + edition,
                             'rights_url': 'https://example.test/rights'},
            }
        raw = b''.join(canonical({'source_id': identifier}) for identifier in SOURCE_IDS)
        (root / 'sources.jsonl').write_bytes(raw)
        self.manifest = {'capture_metadata_hashes': {}, 'files': {'sources.jsonl': sha256(raw)}}
        (root / 'manifest.json').write_bytes(canonical(self.manifest))

    def source(self, identifier):
        return copy.deepcopy(self.sources[identifier])

    def _checked(self, name):
        raw = (self.root / name).read_bytes()
        if sha256(raw) != self.manifest['files'][name]:
            raise ValueError('Synthetic corpus generation changed')
        return raw


class JamesInvestigationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.corpus = SyntheticCorpus(self.root / 'corpus')
        self.ledger_path = self.root / 'investigation.sqlite3'
        self.output = self.root / 'review'

    def prepare(self):
        return prepare_investigation(self.corpus, self.ledger_path, self.output)

    def selection(self, event):
        return [{key: event[key] for key in ('lesson_id', 'revision', 'revision_sha256')}]

    def test_exact_unicode_citations_and_model_only_review(self):
        before = {path.name: sha256(path.read_bytes()) for path in self.corpus.root.iterdir()}
        with patch('socket.getaddrinfo', side_effect=AssertionError('Network attempted')), \
                patch('subprocess.run', side_effect=AssertionError('Worker attempted')):
            summary = self.prepare()
        self.assertEqual(summary['finding_count'], 4)
        self.assertEqual(summary['human_review'], 'not_recorded')
        self.assertEqual(summary['lesson_status'], 'proposed')
        events = LessonLedgerReader(self.ledger_path, self.corpus.source).list_lessons()
        self.assertEqual(len(events), 5)
        for event in events:
            self.assertEqual(event['record']['author'], MODEL_IDENTITY)
            self.assertEqual(event['record']['outcomes'], [])
            if event['lesson_id'] == LESSON_ID:
                self.assertIsNone(event['review'])
                self.assertEqual(event['revision'], 1)
            else:
                self.assertEqual(event['review']['reviewer'], MODEL_IDENTITY)
                self.assertEqual(event['operation'], 'review')
            for citation in event['record']['citations'] + event['record']['counter_citations']:
                source = self.corpus.source(citation['source_id'])
                self.assertEqual(source['text'][citation['start_char']:citation['end_char']], citation['quote'])
                self.assertEqual(citation['text_sha256'], source['text_sha256'])
                self.assertEqual(citation['source_sha256'], source['source_sha256'])
        self.assertEqual(before, {path.name: sha256(path.read_bytes()) for path in self.corpus.root.iterdir()})

    def test_packet_keeps_dissent_dual_editions_and_exact_dependency_hashes(self):
        summary = self.prepare()
        packet = json.loads(Path(summary['review_packet_path']).read_text())
        self.assertEqual(packet['schema'], 'free-man-review-packet/2')
        self.assertEqual(len(packet['investigations']), 4)
        findings = {event['lesson_id']: event for event in packet['investigations']}
        self.assertEqual(findings['james-evidential-reading']['status'], 'disputed')
        self.assertEqual(findings['james-completion-reading']['status'], 'disputed')
        for event in findings.values():
            self.assertEqual({citation['source_id'] for citation in event['record']['citations']}, set(SOURCE_IDS))
        for reference in packet['lessons'][0]['record']['investigation_refs']:
            event = findings[reference['record_id']]
            self.assertEqual(reference['revision'], event['revision'])
            self.assertEqual(reference['revision_sha256'], event['revision_sha256'])
        self.assertEqual({ref['role'] for ref in packet['lessons'][0]['record']['investigation_refs']},
                         {'supports', 'context'})

    def test_private_artifacts_match_summary_and_display_wording_limits(self):
        summary = self.prepare()
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in [self.ledger_path, *self.output.iterdir()]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(Path(summary['summary_path']).read_text()), summary)
        document = Path(summary['review_document_path']).read_bytes()
        packet = Path(summary['review_packet_path']).read_bytes()
        self.assertEqual(sha256(document), summary['review_document_sha256'])
        self.assertEqual(sha256(packet), summary['review_packet_sha256'])
        wording = document.decode()
        for phrase in ('Can faith save him?', 'can that faith save him?', 'dead?', 'barren?',
                       'No Greek or manuscript explanation', 'pending human review',
                       'not words found in the cited passage'):
            self.assertIn(phrase, wording)

    def test_proposed_lesson_cannot_release(self):
        summary = self.prepare()
        packet = json.loads(Path(summary['review_packet_path']).read_text())
        release = {'expected_packet_sha256': summary['review_packet_sha256'],
                   'destination': DESTINATION, 'use': USE,
                   'reviewer': {'kind': 'human', 'name': 'Synthetic release request'},
                   'reason': 'Synthetic rejection test only.',
                   'rights_basis': 'Synthetic source fixtures.'}
        with self.assertRaisesRegex(ValueError, 'human reviewer'):
            release_bundle(self.corpus, self.ledger_path, packet, release)
        lesson = LessonLedgerReader(self.ledger_path, self.corpus.source).get(LESSON_ID)
        self.assertEqual(lesson['status'], 'proposed')
        self.assertIsNone(lesson['review'])

    def test_revised_finding_blocks_unchanged_lesson_packet(self):
        self.prepare()
        ledger = LessonLedger(self.ledger_path, self.corpus.source)
        finding = ledger.get('james-evidential-reading')
        changed = dict(finding['record'], uncertainty='Synthetic correction retaining uncertainty.')
        ledger.save(finding['lesson_id'], changed, finding['revision'])
        lesson = ledger.get(LESSON_ID)
        with self.assertRaises(LessonConflict):
            make_review_packet(self.corpus, self.ledger_path, self.selection(lesson))
        self.assertEqual(ledger.get(LESSON_ID)['revision'], lesson['revision'])

    def test_existing_ledger_or_output_are_not_reused(self):
        self.prepare()
        before = self.ledger_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'fresh'):
            self.prepare()
        self.assertEqual(self.ledger_path.read_bytes(), before)
        other_ledger = self.root / 'other.sqlite3'
        with self.assertRaisesRegex(ValueError, 'fresh'):
            prepare_investigation(self.corpus, other_ledger, self.output)
        self.assertFalse(other_ledger.exists())

    def test_missing_or_invalid_verse_refused_before_creating_state(self):
        for mode in ('missing', 'span', 'duplicate', 'numbering', 'non_object'):
            with self.subTest(mode=mode):
                original = copy.deepcopy(self.corpus.sources[SOURCE_IDS[0]])
                verses = self.corpus.sources[SOURCE_IDS[0]]['ast']['verses']
                if mode == 'missing':
                    verses.pop()
                elif mode == 'span':
                    verses[0]['text_span'][0] += 1
                elif mode == 'duplicate':
                    verses.append(copy.deepcopy(verses[0]))
                elif mode == 'numbering':
                    verses[0]['chapter'] = 1
                else:
                    verses[0] = 'Not a verse object'
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.ledger_path.exists())
                self.assertFalse(self.output.exists())
                self.corpus.sources[SOURCE_IDS[0]] = original

    def test_changed_generation_and_wrong_source_identity_refused_before_writes(self):
        manifest = self.corpus.root / 'manifest.json'
        original = manifest.read_bytes()
        manifest.write_bytes(canonical({'changed': True}))
        with self.assertRaisesRegex(ValueError, 'generation changed'):
            self.prepare()
        self.assertFalse(self.ledger_path.exists())
        self.assertFalse(self.output.exists())
        manifest.write_bytes(original)
        self.corpus.sources[SOURCE_IDS[0]]['source_id'] = 'scripture:wrong:JAS'
        with self.assertRaisesRegex(ValueError, 'frozen source identifier'):
            self.prepare()
        self.assertFalse(self.ledger_path.exists())
        self.assertFalse(self.output.exists())

    def test_symlink_sidecar_refused_without_output_or_target_changes(self):
        target = self.root / 'target'
        target.write_text('Retain synthetic evidence.')
        sidecar = Path(str(self.ledger_path) + '-journal')
        sidecar.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'sidecar paths must be fresh'):
            self.prepare()
        self.assertEqual(target.read_text(), 'Retain synthetic evidence.')
        self.assertFalse(self.output.exists())
        self.assertFalse(self.ledger_path.exists())


if __name__ == '__main__':
    unittest.main()

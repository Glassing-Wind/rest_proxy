"""Offline James import/relationship tests; synthetic archives, no network or services."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from graphrag_core.lessons_corpus.corpus import Corpus, build_corpus, canonical, sha256
from graphrag_core.lessons_corpus.scripture_pilot import (
    DATASET_URL, EDITION_URLS, import_james_capture, pilot_relationships, select_crossrefs,
)


HEADER = 'From Verse\tTo Verse\tVotes\t#www.openbible.info CC-BY 2026-10-19\n'


def zipped(member, payload):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, payload)
    return buffer.getvalue()


class JamesPilotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.receipt_path = self.root / 'acquisition.json'
        self.output = self.root / 'capture'
        self.records, self.originals = [], {}
        for edition, url in EDITION_URLS.items():
            text = '\\id JAS\n\\h James\n'
            for chapter, count in enumerate((27, 26, 18, 17, 20), 1):
                text += f'\\c {chapter}\n\\p\n'
                for verse in range(1, count + 1):
                    text += f'\\v {verse} {edition} care 😀 café {chapter}:{verse}.\n'
            self.originals[edition] = text
            self.download(edition, url, f'89-JAS{edition}.usfm', text)
        self.crossrefs = (HEADER + 'Jas.2.15\tJas.2.16\t-2\n'
                          'Jas.2.15\tJas.2.18-Jas.2.20\t4\n'
                          'Jas.2.15\tRom.4.1\t9\n'
                          'Rom.4.1\tJas.2.15\t6\n')
        self.download('openbible', DATASET_URL, 'cross_references.txt', self.crossrefs)
        self.write_receipt()

    def download(self, edition, url, member, text):
        raw = zipped(member, text.encode('utf-8'))
        path = self.root / (edition + '.zip')
        path.write_bytes(raw)
        path.chmod(0o600)
        self.records.append({'edition': edition, 'url': url, 'final_url': url, 'status': 200,
                             'path': str(path), 'bytes': len(raw), 'sha256': sha256(raw),
                             'acquired_at': '2026-10-19T00:00:00+00:00', 'headers': {}})

    def write_receipt(self):
        self.receipt_path.write_bytes(canonical({'schema': 'source-research-receipt/1', 'sources': self.records}))

    def build(self):
        with patch('socket.getaddrinfo', side_effect=AssertionError('Network attempted')):
            import_james_capture(self.receipt_path, self.output)
            return Corpus(build_corpus(self.output)['output_dir'])

    def test_two_editions_exact_unicode_and_attributed_resolved_and_unresolved_relationships(self):
        corpus = self.build()
        self.assertEqual(len(corpus.sources), 3)
        self.assertEqual(corpus.verify()['original_responses'], 3)
        for edition in EDITION_URLS:
            source = corpus.source(f'scripture:{edition}:JAS')
            ast = source['ast']
            self.assertEqual(''.join(item['raw'] for item in ast['source_tokens']), self.originals[edition])
            self.assertEqual(len(ast['verses']), 108)
            self.assertEqual(source['metadata']['edition_id'], edition)
            for verse in ast['verses']:
                first, last = verse['text_span']
                self.assertEqual(source['text'][first:last], verse['text'])
                self.assertIn('😀 café', verse['text'])
        result = pilot_relationships(corpus)
        self.assertEqual(result['edition_verse_counts'], {'engwebp': 108, 'eng-asv': 108})
        self.assertEqual((result['dataset_rows'], result['selected_rows']), (4, 3))
        edges = result['relationships']
        self.assertEqual(edges[0]['votes'], -2)
        for edge in edges:
            citation = edge['citation']
            source = corpus.source(citation['source_id'])
            self.assertEqual(source['text'][citation['start_char']:citation['end_char']], citation['quote'])
            first, last = edge['original_text_span']
            self.assertEqual(self.crossrefs[first:last], edge['quote'])
            self.assertEqual(edge['dataset']['license'], 'CC BY 4.0')
            self.assertEqual(edge['kind'], 'editorial_cross_reference')
        self.assertEqual(len(edges[1]['editions']['eng-asv']['to']['passages']), 3)
        self.assertEqual(edges[2]['editions']['engwebp']['to'],
                         {'status': 'outside_pilot_scope', 'passages': []})
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)

    def test_invalid_receipt_refused_before_capture_creation(self):
        original = copy.deepcopy(self.records)
        for field, value in [('sha256', '0' * 64), ('url', 'https://other.test/archive'),
                              ('status', 404), ('acquired_at', '2026-10-19')]:
            with self.subTest(field=field):
                self.records = copy.deepcopy(original)
                self.records[0][field] = value
                self.write_receipt()
                with self.assertRaises(ValueError):
                    import_james_capture(self.receipt_path, self.output)
                self.assertFalse(self.output.exists())

    def test_receipt_does_not_follow_outside_files_or_symlinks(self):
        link = self.root / 'linked.zip'
        link.symlink_to(self.records[0]['path'])
        self.records[0]['path'] = str(link)
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, 'sibling'):
            import_james_capture(self.receipt_path, self.output)
        self.assertFalse(self.output.exists())

    def test_import_refuses_incomplete_book_and_preserves_existing_capture(self):
        original = copy.deepcopy(self.records)
        original_raw = Path(self.records[0]['path']).read_bytes()
        self.records = self.records[1:]
        self.download('engwebp', EDITION_URLS['engwebp'], '89-JASengwebp.usfm', '\\id JAS\n\\c 1\n\\p\n\\v 1 Partial.\n')
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, '108'):
            import_james_capture(self.receipt_path, self.output)
        self.assertFalse(self.output.exists())
        self.records = original
        Path(self.records[0]['path']).write_bytes(original_raw)
        self.write_receipt()
        self.build()
        before = (self.output / 'inventory.json').read_bytes()
        with self.assertRaises(FileExistsError):
            import_james_capture(self.receipt_path, self.output)
        self.assertEqual((self.output / 'inventory.json').read_bytes(), before)

    def test_malformed_crossrefs_and_unsafe_archives_refused(self):
        for member, text in [('cross_references.txt', HEADER + 'Jas.2.1\tRom.1.1\ttruth\n'),
                              ('cross_references.txt', HEADER + 'Jas.2.1\tbroken\t1\n'),
                              ('../cross_references.txt', HEADER + 'Jas.2.1\tRom.1.1\t1\n')]:
            with self.subTest(member=member, text=text), self.assertRaises(ValueError):
                select_crossrefs(zipped(member, text))
        with self.assertRaisesRegex(ValueError, 'Invalid cross-reference ZIP'):
            select_crossrefs(b'not a ZIP')

    def test_import_fingerprints_exact_receipt_used_even_if_input_is_replaced(self):
        original = self.receipt_path.read_bytes()
        from graphrag_core.lessons_corpus.scripture_pilot import scripture_source

        def replace_receipt(*args, **kwargs):
            self.receipt_path.write_text('{}')
            return scripture_source(*args, **kwargs)

        with patch('graphrag_core.lessons_corpus.scripture_pilot.scripture_source', side_effect=replace_receipt):
            result = import_james_capture(self.receipt_path, self.output)
        self.assertEqual(result['inventory']['source_receipt_sha256'], sha256(original))

    def test_module_help_works_in_separate_process_without_pythonpath(self):
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, '-m', 'scripts.james_pilot', '--help'],
                                cwd=Path(__file__).resolve().parent, env=environment,
                                capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--acquisitions', result.stdout)

    def test_recomputed_catalog_hashes_cannot_forge_structured_verse_spans(self):
        corpus = self.build()
        row = corpus.sources['scripture:engwebp:JAS']
        ast = json.loads((corpus.root / row['ast_path']).read_bytes())
        ast['verses'][0]['text_span'][1] -= 1
        payload = canonical(ast)
        row['ast_sha256'] = sha256(payload)
        (corpus.root / row['ast_path']).write_bytes(payload)
        (corpus.root / 'sources.jsonl').write_bytes(b''.join(canonical(r) for r in corpus.sources.values()))
        for relative in (row['ast_path'], 'sources.jsonl'):
            corpus.manifest['files'][relative] = sha256((corpus.root / relative).read_bytes())
        (corpus.root / 'manifest.json').write_bytes(canonical(corpus.manifest))
        with self.assertRaisesRegex(ValueError, 'verse structure'):
            Corpus(corpus.root).source(row['source_id'])


if __name__ == '__main__':
    unittest.main()

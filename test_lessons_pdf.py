"""Actual PDF parser tests; run with the bundled PDF Python, no services/network."""
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest

from graphrag_core.lessons_corpus.corpus import Corpus, build_corpus, canonical
from scripts.extract_lessons_pdf import extract


@unittest.skipUnless(importlib.util.find_spec('pypdf'), 'Optional pypdf runtime required')
class PdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def pdf(self, *, encrypted=False):
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject

        writer = PdfWriter()
        font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                                   NameObject('/Subtype'): NameObject('/Type1'),
                                                   NameObject('/BaseFont'): NameObject('/Helvetica')}))
        for text in ('Service and dignity.', 'Learning continues. Last words.'):
            page = writer.add_blank_page(width=612, height=792)
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
            stream = DecodedStreamObject()
            stream.set_data(('BT /F1 12 Tf 72 720 Td (' + text + ') Tj ET').encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
        action = writer._add_object(DictionaryObject({NameObject('/S'): NameObject('/URI'),
                                                       NameObject('/URI'): TextStringObject('https://reference.test/context')}))
        annotation = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Annot'),
                                                           NameObject('/Subtype'): NameObject('/Link'),
                                                           NameObject('/Rect'): ArrayObject([NumberObject(i) for i in (0, 0, 100, 100)]),
                                                           NameObject('/A'): action}))
        writer.pages[0][NameObject('/Annots')] = writer._add_object(ArrayObject([annotation]))
        if encrypted:
            writer.encrypt('synthetic-test-password')
        result = io.BytesIO()
        writer.write(result)
        return result.getvalue()

    def test_all_pages_and_indirect_annotation_action(self):
        raw = self.pdf()
        path = self.root / 'source.pdf'
        path.write_bytes(raw)
        result = extract(path)
        self.assertEqual(result['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(len(result['pages']), 2)
        self.assertIn('Service and dignity.', result['pages'][0]['text'])
        self.assertIn('Last words.', result['pages'][-1]['text'])
        self.assertEqual(result['pages'][0]['annotation_urls'], ['https://reference.test/context'])
        self.assertEqual(result['blank_pages'], [])

    def test_pdf_corpus_original_and_page_reference_lineage(self):
        raw = self.pdf()
        capture = self.root / 'capture'
        capture.mkdir(mode=0o700)
        (capture / 'raw').mkdir(mode=0o700)
        digest = hashlib.sha256(raw).hexdigest()
        relative = 'raw/' + digest + '.bin'
        (capture / relative).write_bytes(raw)
        acquisition = {'id': 'pdf1', 'status': 200, 'media_type': 'application/pdf',
                       'requested_url': 'https://example.test/source.pdf',
                       'final_url': 'https://example.test/source.pdf',
                       'raw_path': relative, 'body_sha256': digest}
        (capture / 'acquisitions.jsonl').write_bytes(canonical(acquisition))
        (capture / 'documents.jsonl').write_bytes(b'')
        (capture / 'inventory.json').write_bytes(canonical({'seed_url': 'https://example.test/'}))
        (capture / 'coverage.json').write_bytes(canonical({'complete_within_declared_scope': True}))
        result = build_corpus(capture, pdf_python=sys.executable)
        corpus = Corpus(result['output_dir'])
        source = corpus.source('pdf:pdf1:page:1')
        self.assertIn('Service', source['text'])
        self.assertEqual(source['reference_occurrences'], 1)
        self.assertEqual(result['coverage']['reference_occurrences'], 1)
        self.assertEqual(corpus.verify()['original_responses'], 1)
        self.assertEqual(corpus.search('learning')[0]['source_id'], 'pdf:pdf1:page:2')

    def test_encrypted_original_is_preserved_but_not_claimed_extracted(self):
        path = self.root / 'encrypted.pdf'
        raw = self.pdf(encrypted=True)
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'Encrypted'):
            extract(path)
        self.assertEqual(path.read_bytes(), raw)

    def test_octet_stream_referenced_pdf_is_extracted_and_original_preserved(self):
        raw = self.pdf()
        capture = self.root / 'capture'
        capture.mkdir(mode=0o700)
        (capture / 'raw').mkdir(mode=0o700)
        digest = hashlib.sha256(raw).hexdigest()
        relative = 'raw/' + digest + '.bin'
        (capture / relative).write_bytes(raw)
        acquisition = {'id': 'pdf-octet', 'status': 200, 'media_type': 'application/octet-stream',
                       'purpose': 'referenced_pdf', 'requested_url': 'https://example.test/source.pdf',
                       'final_url': 'https://example.test/source.pdf',
                       'raw_path': relative, 'body_sha256': digest}
        (capture / 'acquisitions.jsonl').write_bytes(canonical(acquisition))
        (capture / 'documents.jsonl').write_bytes(b'')
        (capture / 'inventory.json').write_bytes(canonical({'seed_url': 'https://example.test/'}))
        (capture / 'coverage.json').write_bytes(canonical({'complete_within_declared_scope': True}))
        result = build_corpus(capture, pdf_python=sys.executable)
        corpus = Corpus(result['output_dir'])
        self.assertIn('Service and dignity.', corpus.source('pdf:pdf-octet:page:1')['text'])
        self.assertIn('Last words.', corpus.source('pdf:pdf-octet:page:2')['text'])
        self.assertEqual(result['coverage']['pdf_extraction'][0]['pages'], 2)
        self.assertTrue(result['coverage']['representation_complete'])
        self.assertEqual(result['coverage']['processing_failures'], [])
        self.assertEqual((capture / relative).read_bytes(), raw)
        self.assertEqual(corpus.verify()['original_responses'], 1)


if __name__ == '__main__':
    unittest.main()

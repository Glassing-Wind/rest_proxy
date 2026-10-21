"""Synthetic read-only reference frontier acceptance; no remote sources or services."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from graphrag_core.lessons_corpus.acquire import _document
from graphrag_core.lessons_corpus.corpus import Corpus, build_corpus, canonical, sha256
from graphrag_core.lessons_corpus.frontier import collect_targets, reference_frontier


class ReferenceFrontierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.capture = self.root / 'capture'
        self.capture.mkdir(mode=0o700)
        (self.capture / 'raw').mkdir(mode=0o700)

    def fixture(self, *, repeats=3, long_url=False):
        links = ''.join(f'<a href="https://outside.test/book#part{index}">History {index}</a>'
                        for index in range(repeats))
        links += ('<a href="https://example.test/current#section">Current</a>'
                  '<a href="https://example.test/missing">Missing</a>'
                  '<a href="https://www.example.test/alias">Alias</a>'
                  '<a href="https://OUTSIDE.test/book">Exact spelling</a>'
                  '<a href="mailto:reader@example.test">Mail</a><a href="javascript:do_not_run()">Data</a>')
        if long_url:
            links += '<a href="https://outside.test/' + 'x' * 4200 + '">Long URL</a>'
        objects = [{'id': 1, 'link': 'https://example.test/current', 'title': {'rendered': 'Care'},
                    'content': {'rendered': links}},
                   {'id': 2, 'link': 'https://example.test/page', 'title': {'rendered': 'History'},
                    'content': {'rendered': '<a href="https://outside.test/book#page">Second source</a>'}}]
        acquisitions, documents = [], []
        for kind, obj in zip(('post', 'page'), objects):
            payload = canonical([obj])
            relative = 'raw/' + sha256(payload) + '.bin'
            (self.capture / relative).write_bytes(payload)
            acquisitions.append({'id': 'api-' + kind, 'purpose': 'rest:' + kind, 'status': 200,
                                 'media_type': 'application/json',
                                 'final_url': 'https://example.test/wp-json/wp/v2/' + kind + 's',
                                 'raw_path': relative, 'body_sha256': sha256(payload)})
            documents.append(_document(kind, obj, 'api-' + kind))
        page = b'<p>Template <a href="https://outside.test/template">Link</a></p>'
        relative = 'raw/' + sha256(page) + '.bin'
        (self.capture / relative).write_bytes(page)
        acquisitions.append({'id': 'html', 'status': 200, 'media_type': 'text/html', 'encoding': 'utf-8',
                             'final_url': 'https://example.test/current',
                             'raw_path': relative, 'body_sha256': sha256(page)})
        (self.capture / 'acquisitions.jsonl').write_bytes(b''.join(canonical(row) for row in acquisitions))
        (self.capture / 'documents.jsonl').write_bytes(b''.join(canonical(row) for row in documents))
        (self.capture / 'inventory.json').write_bytes(canonical({'seed_url': 'https://example.test/'}))
        (self.capture / 'coverage.json').write_bytes(canonical({'complete_within_declared_scope': True}))
        return Corpus(build_corpus(self.capture)['output_dir'])

    def rewrite_references(self, corpus, rows):
        raw = b''.join(canonical(row) for row in rows)
        (corpus.root / 'references.jsonl').write_bytes(raw)
        manifest = json.loads((corpus.root / 'manifest.json').read_bytes())
        manifest['files']['references.jsonl'] = sha256(raw)
        (corpus.root / 'manifest.json').write_bytes(canonical(manifest))
        return Corpus(corpus.root)

    def test_exact_defragmented_targets_preserve_duplicate_occurrences_and_sources(self):
        corpus = self.fixture()
        targets = collect_targets(corpus)
        self.assertEqual([row['url'] for row in targets], sorted(row['url'] for row in targets))
        book = next(row for row in targets if row['url'] == 'https://outside.test/book')
        self.assertEqual(book['occurrence_count'], 4)
        self.assertEqual(book['source_count'], 2)
        self.assertEqual(book['acquisition_status_counts'], {'external_unacquired': 4})
        self.assertEqual(book['target_id'], sha256(book['url'].encode()))
        self.assertTrue(any(row['url'] == 'https://OUTSIDE.test/book' for row in targets))
        self.assertFalse(any('#' in row['url'] for row in targets))

    def test_scope_origin_and_acquired_occurrences_remain_explicit(self):
        corpus = self.fixture()
        authored = reference_frontier(corpus)
        current = next(row for row in authored['targets'] if row['url'] == 'https://example.test/current')
        self.assertEqual(current['acquired_original_occurrences'], 1)
        self.assertEqual(current['acquisition_status_counts'], {'acquired_original': 1})
        self.assertEqual(authored['totals']['non_http_reference_occurrences'], 2)
        self.assertFalse(any(row['url'] == 'https://outside.test/template' for row in authored['targets']))
        all_sources = reference_frontier(corpus, scope='all')
        self.assertTrue(any(row['url'] == 'https://outside.test/template' for row in all_sources['targets']))
        same = reference_frontier(corpus, origin='same-site')
        self.assertTrue(all(row['origin'] == 'same-site' for row in same['targets']))
        external = reference_frontier(corpus, origin='external')
        alias = next(row for row in external['targets'] if row['url'] == 'https://www.example.test/alias')
        self.assertEqual(alias['origin'], 'external')
        self.assertEqual(alias['acquisition_status_counts'], {'same_site_unacquired': 1})
        self.assertEqual(authored['parent_manifest_sha256'], sha256((corpus.root / 'manifest.json').read_bytes()))

    def test_samples_and_pagination_are_bounded_and_stable(self):
        corpus = self.fixture(repeats=8)
        targets = collect_targets(corpus)
        book = next(row for row in targets if row['url'] == 'https://outside.test/book')
        self.assertEqual(len(book['occurrence_samples']), 5)
        self.assertTrue(book['samples_truncated'])
        self.assertEqual(book['occurrence_count'], 9)
        first = reference_frontier(corpus, limit=2)
        second = reference_frontier(corpus, offset=first['next_offset'], limit=2)
        self.assertEqual(first['next_offset'], 2)
        self.assertEqual(second['offset'], 2)
        self.assertEqual(first['targets'] + second['targets'], reference_frontier(corpus, limit=4)['targets'])
        self.assertEqual(reference_frontier(corpus), reference_frontier(corpus))

    def test_display_urls_are_truncated_but_recovery_targets_remain_complete(self):
        corpus = self.fixture(long_url=True)
        full = next(row for row in collect_targets(corpus) if len(row['url']) > 4096)
        shown = next(row for row in reference_frontier(corpus)['targets'] if row['target_id'] == full['target_id'])
        self.assertEqual(len(shown['url']), 4096)
        self.assertTrue(shown['url_truncated'])
        self.assertEqual(shown['url'], full['url'][:4096])

    def test_reference_and_catalog_hash_tampering_are_refused(self):
        for name in ('references.jsonl', 'sources.jsonl'):
            with self.subTest(name=name):
                corpus = self.fixture()
                path = corpus.root / name
                old = path.read_bytes()
                path.write_bytes(old + b'\n')
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    collect_targets(corpus)
                path.write_bytes(old)
                # The fixture root must be fresh for the second subtest.
                shutil.rmtree(corpus.root)

    def test_reference_contract_source_hash_and_duplicate_occurrence_checks(self):
        corpus = self.fixture()
        original = [json.loads(line) for line in corpus._checked('references.jsonl').splitlines()]
        for change in ('hash', 'source', 'status', 'target', 'duplicate', 'unexpected'):
            with self.subTest(change=change):
                rows = json.loads(json.dumps(original))
                if change == 'hash':
                    rows[0]['source_sha256'] = '0' * 64
                elif change == 'source':
                    rows[0]['source_id'] = 'missing'
                elif change == 'status':
                    rows[0]['acquisition_status'] = 'resolved'
                elif change == 'target':
                    rows[0]['resolved_url'] = 'https://bad host/path'
                elif change == 'duplicate':
                    rows.append(rows[0])
                else:
                    rows[0]['execute'] = 'never'
                changed = self.rewrite_references(corpus, rows)
                with self.assertRaises(ValueError):
                    collect_targets(changed)
        self.rewrite_references(corpus, original)

    def test_catalog_in_memory_and_parent_manifest_changes_are_refused(self):
        corpus = self.fixture()
        corpus.sources['post:1:content_html']['source_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'catalog changed'):
            collect_targets(corpus)
        corpus = Corpus(corpus.root)
        (corpus.root / 'manifest.json').write_bytes((corpus.root / 'manifest.json').read_bytes() + b' ')
        # Whitespace changes manifest identity while preserving its parsed object.
        self.assertNotEqual(reference_frontier(corpus)['parent_manifest_sha256'], sha256(canonical(corpus.manifest)))
        corpus.manifest['scope'] = 'changed-in-memory'
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            collect_targets(corpus)

    def test_reads_do_not_use_network_models_workers_or_mutate_files(self):
        corpus = self.fixture()
        before = {path.relative_to(self.capture): sha256(path.read_bytes())
                  for path in self.capture.rglob('*') if path.is_file()}
        with patch('socket.getaddrinfo', side_effect=AssertionError('DNS attempted')), \
                patch('subprocess.run', side_effect=AssertionError('Worker attempted')):
            self.assertTrue(reference_frontier(corpus)['targets'])
            self.assertTrue(collect_targets(corpus))
        after = {path.relative_to(self.capture): sha256(path.read_bytes())
                 for path in self.capture.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_strict_scope_origin_and_pagination_parameters(self):
        corpus = self.fixture()
        for arguments in ({'scope': 'unknown'}, {'scope': True}, {'origin': 'unknown'},
                          {'origin': None}, {'limit': True}, {'limit': 0}, {'limit': 101},
                          {'offset': True}, {'offset': -1}, {'offset': 1000001}, {'limit': 2.0}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                reference_frontier(corpus, **arguments)


if __name__ == '__main__':
    unittest.main()

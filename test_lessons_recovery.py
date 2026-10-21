"""Offline source-recovery contracts; synthetic sources, no listener or remote calls."""
import asyncio
import json
from unittest import TestCase, main
from unittest.mock import patch

import httpx

from graphrag_core.lessons_corpus.acquire import _Response
from graphrag_core.lessons_corpus.corpus import Corpus, build_corpus, sha256
from graphrag_core.lessons_corpus.frontier import collect_targets
from graphrag_core.lessons_corpus.recovery import capture_references
from graphrag_core.lessons_corpus.server import create_app
import test_lessons_corpus as fixtures


class RecoveryTests(TestCase):
    def setUp(self):
        self.fixture = fixtures.CorpusTests('test_source_round_trip_and_reference_occurrences')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.fixture()
        self.parent = self.fixture.build()
        self.output = self.fixture.root / 'recovery'
        self.selection = {'target_id': sha256(b'https://reference.test/book'),
                          'candidate_url': 'https://candidate.test/book/', 'relation': 'candidate_counterpart'}
        self.permissions = {'candidate.test': 'Synthetic private processing, not a license grant.'}
        self.resolve = patch('graphrag_core.lessons_corpus.acquire._resolve_public_host',
                             return_value={'mode': 'synthetic-test'})
        self.resolve.start()
        self.addCleanup(self.resolve.stop)
        self.raw = b'<h1>Backstory</h1><p>Care &amp; correction. Last words.</p>'

    def capture(self, *, status=200, body=None, content_type='text/html; charset=utf-8', after=None):
        def fetch(url, limit):
            if after:
                after()
            return _Response(url, status, {'content-type': content_type}, self.raw if body is None else body)
        with patch('graphrag_core.lessons_corpus.acquire._Transport.fetch', side_effect=fetch):
            return capture_references(self.parent, self.output, [self.selection], permissions=self.permissions)

    def test_new_snapshot_preserves_parent_and_qualified_source_context(self):
        before = self.parent.verify()
        parent_manifest = (self.parent.root / 'manifest.json').read_bytes()
        result = self.capture()
        self.assertTrue(result['coverage']['complete_within_declared_scope'])
        self.assertEqual(result['inventory']['bindings'][0]['historical_equivalence'], 'not_established')
        built = build_corpus(self.output)
        recovered = Corpus(built['output_dir'])
        self.assertTrue(set(recovered.sources).isdisjoint(self.parent.sources))
        source_id = next(iter(recovered.sources))
        self.assertTrue(source_id.startswith('html:recovery:'))
        source = recovered.source(source_id)
        self.assertIn('Care & correction.', source['text'])
        context = source['metadata']['reference_recovery']
        self.assertEqual(context['parent_manifest_sha256'], sha256(parent_manifest))
        self.assertEqual(context['original_target_url'], 'https://reference.test/book')
        match = recovered.search('correction')[0]
        self.assertIn('historical_equivalence_not_established', match['interpretation_status'])
        self.assertEqual(match['citation']['quote'], source['text'])
        self.assertEqual(self.parent.verify(), before)
        self.assertEqual((self.parent.root / 'manifest.json').read_bytes(), parent_manifest)
        self.assertEqual(recovered.verify()['original_responses'], 1)

    def test_http_failure_retains_original_and_unreviewed_target(self):
        result = self.capture(status=404, body=b'Not found.')
        self.assertFalse(result['coverage']['complete_within_declared_scope'])
        self.assertEqual(result['inventory']['bindings'][0]['status'], 404)
        self.assertEqual(len(result['coverage']['unreviewed_original_targets']), 1)
        acquisitions = [json.loads(line) for line in (self.output / 'acquisitions.jsonl').read_text().splitlines()]
        self.assertEqual((self.output / acquisitions[0]['raw_path']).read_bytes(), b'Not found.')

    def test_unknown_target_duplicate_and_mislabelled_recheck_refuse_before_fetch(self):
        bad = [dict(self.selection, target_id='0' * 64),
               dict(self.selection, relation='same_url_recheck'), dict(self.selection, relation=['bad'])]
        with patch('graphrag_core.lessons_corpus.acquire._Transport.fetch') as fetch:
            for selection in bad:
                with self.subTest(selection=selection):
                    with self.assertRaises(ValueError):
                        capture_references(self.parent, self.output, [selection], permissions=self.permissions)
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                capture_references(self.parent, self.output, [self.selection] * 2, permissions=self.permissions)
        fetch.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_unpermitted_host_and_admin_paths_refuse_without_state(self):
        with patch('graphrag_core.lessons_corpus.acquire._Transport.fetch') as fetch:
            for url in ('https://other.test/book/', 'https://candidate.test/%77p-admin/users.php',
                        'https://user:secret@candidate.test/book/'):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    capture_references(self.parent, self.output, [dict(self.selection, candidate_url=url)],
                                       permissions=self.permissions)
        fetch.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_www_alias_without_separate_permission_refuses_before_fetch(self):
        selection = dict(self.selection, candidate_url='https://www.candidate.test/book/')
        with patch('graphrag_core.lessons_corpus.acquire._Transport.fetch') as fetch:
            with self.assertRaises(ValueError):
                capture_references(self.parent, self.output, [selection], permissions=self.permissions)
        fetch.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_bare_and_www_hosts_can_be_permitted_separately(self):
        selections = [self.selection, dict(self.selection, candidate_url='https://www.candidate.test/book/')]
        permissions = {**self.permissions, 'www.candidate.test': 'Separate synthetic host processing permission.'}

        def fetch(url, limit):
            return _Response(url, 200, {'content-type': 'text/html; charset=utf-8'}, self.raw)

        with patch('graphrag_core.lessons_corpus.acquire._Transport.fetch', side_effect=fetch) as transport:
            result = capture_references(self.parent, self.output, selections, permissions=permissions)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual([call.args[0] for call in transport.call_args_list],
                         [selection['candidate_url'] for selection in selections])
        self.assertEqual(result['inventory']['rights_basis_by_host'], permissions)
        self.assertEqual(len(result['inventory']['bindings']), 2)
        self.assertTrue(result['coverage']['complete_within_declared_scope'])
        self.assertTrue(all(binding['assessment'] == 'unreviewed' for binding in result['inventory']['bindings']))

    def test_parent_manifest_mutation_after_frontier_collection_refuses_before_fetch(self):
        def mutate_after_collection(*args, **kwargs):
            targets = collect_targets(*args, **kwargs)
            path = self.parent.root / 'manifest.json'
            path.write_bytes(path.read_bytes() + b'\n')
            return targets

        with patch('graphrag_core.lessons_corpus.recovery.collect_targets', side_effect=mutate_after_collection), \
                patch('graphrag_core.lessons_corpus.acquire._Transport.fetch') as fetch:
            with self.assertRaisesRegex(ValueError, '(?i)(parent|manifest).*changed'):
                capture_references(self.parent, self.output, [self.selection], permissions=self.permissions)
        fetch.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_parent_mutation_during_recovery_is_a_retained_warning(self):
        def mutate():
            path = self.parent.root / 'references.jsonl'
            path.write_bytes(path.read_bytes() + b'\n')
        result = self.capture(after=mutate)
        self.assertTrue(result['coverage']['parent_changed'])
        self.assertFalse(result['coverage']['complete_within_declared_scope'])

    def test_nontext_body_does_not_claim_representation_eligibility(self):
        result = self.capture(body=b'GIF89a', content_type='image/gif')
        self.assertFalse(result['coverage']['complete_within_declared_scope'])
        self.assertFalse(result['inventory']['bindings'][0]['representation_eligible'])

    def test_rest_source_and_search_keep_the_candidate_qualification(self):
        self.capture()
        built = build_corpus(self.output)
        corpus = Corpus(built['output_dir'])
        source_id = next(iter(corpus.sources))
        token = 'synthetic-capability-' + 'x' * 32
        app = create_app(str(corpus.root), token, '127.0.0.1:8781')

        async def probe():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 51000)),
                                         base_url='http://127.0.0.1:8781',
                                         headers={'Authorization': 'Bearer ' + token}) as client:
                for path in ('/source?source_id=' + source_id, '/search?query=correction', '/sources'):
                    response = await client.get(path)
                    self.assertEqual(response.status_code, 200)
                    data = response.json()
                    items = data['results'] if 'results' in data else data['sources'] if 'sources' in data else [data]
                    self.assertTrue(items)
                    for item in items:
                        self.assertIn('reference_recovery_unreviewed', item['interpretation_status'])
                        self.assertIn('historical_equivalence_not_established', item['interpretation_status'])
        asyncio.run(probe())


if __name__ == '__main__':
    main()

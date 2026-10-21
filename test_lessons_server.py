"""Offline REST adapter acceptance using synthetic corpus fixtures and ASGI.

Run .venv/bin/python -W error::ResourceWarning test_lessons_server.py. No socket
listener, personal records, model runtime, network or background daemon is used.
"""
from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch

import httpx

from graphrag_core.lessons_corpus.server import MAX_SLICE, MAX_URL, create_app
import test_lessons_corpus as corpus_fixtures


HOST = '127.0.0.1:8781'
BASE = 'http://' + HOST
TOKEN = 'synthetic-lessons-capability-0123456789abcdef'
AUTHORIZATION = ('authorization', 'Bearer ' + TOKEN)


class LessonsServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = corpus_fixtures.CorpusTests('test_source_round_trip_and_reference_occurrences')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.fixture()
        self.corpus = self.fixture.build()
        self.app = create_app(str(self.corpus.root), TOKEN, HOST)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=('127.0.0.1', 54321),
                                         raise_app_exceptions=False), base_url=BASE,
        )
        self.addAsyncCleanup(self.client.aclose)

    async def request(self, path, *, authorized=True, headers=(), method='GET', **kwargs):
        request_headers = [AUTHORIZATION] if authorized else []
        request_headers.extend(headers)
        return await self.client.request(method, path, headers=request_headers, **kwargs)

    def assert_private(self, response):
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        self.assertEqual(response.headers.get('x-content-type-options'), 'nosniff')
        self.assertEqual(response.headers.get('referrer-policy'), 'no-referrer')
        self.assertEqual(response.headers.get('x-frame-options'), 'DENY')
        self.assertIn("frame-ancestors 'none'", response.headers.get('content-security-policy', ''))
        self.assertNotIn('access-control-allow-origin', response.headers)
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn(str(self.corpus.root), response.text)
        self.assertNotIn('Traceback', response.text)

    async def test_all_endpoints_require_a_single_bearer(self):
        paths = ['/health', '/sources', '/source?source_id=post:11:content_html',
                 '/sources/post:11:content_html', '/search?query=dignity', '/references']
        for path in paths:
            with self.subTest(path=path):
                response = await self.request(path, authorized=False)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {'error': 'local-capability-required'})
                self.assert_private(response)
        duplicate = await self.request('/health', headers=[AUTHORIZATION])
        self.assertEqual(duplicate.status_code, 401)
        wrong = await self.request('/health', authorized=False, headers=[('authorization', 'Bearer wrong')])
        self.assertEqual(wrong.status_code, 401)
        query_token = await self.request('/health?token=' + TOKEN, authorized=False)
        self.assertEqual(query_token.status_code, 401)
        self.assert_private(query_token)

    async def test_health_is_minimal_and_authenticated(self):
        response = await self.request('/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'surface': 'local-lessons-corpus', 'read_only': True})
        self.assert_private(response)

    async def test_host_validation_rejects_alternatives_and_duplicates(self):
        for host in ('localhost:8781', '127.0.0.1', '127.0.0.1:8782', '127.0.0.1:8781.attacker.invalid'):
            with self.subTest(host=host):
                response = await self.request('/health', headers=[('host', host)])
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()['error'], 'local-host-required')
                self.assert_private(response)
        response = await self.request('/health', headers=[('host', HOST), ('host', HOST)])
        self.assertEqual(response.status_code, 403)

    async def test_remote_peer_cannot_spoof_loopback_using_forwarded_headers(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=('192.0.2.8', 54321)), base_url=BASE,
        ) as client:
            response = await client.get('/sources', headers=[AUTHORIZATION, ('x-forwarded-for', '127.0.0.1')])
        self.assertEqual(response.status_code, 403)
        self.assert_private(response)

    async def test_browser_origins_are_refused_even_if_same_host(self):
        for origin in (BASE, 'null', 'https://attacker.invalid', ''):
            with self.subTest(origin=origin):
                response = await self.request('/sources', headers=[('origin', origin)])
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()['error'], 'browser-origin-not-supported')
                self.assert_private(response)
        duplicate = await self.request('/health', headers=[('origin', BASE), ('origin', BASE)])
        self.assertEqual(duplicate.status_code, 403)

    async def test_sources_are_paginated_metadata_without_paths_text_or_ast(self):
        response = await self.request('/sources?offset=0&limit=2')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(len(data['sources']), 2)
        self.assertEqual(data['total'], len(self.corpus.sources))
        self.assertEqual(data['next_offset'], 2)
        for row in data['sources']:
            self.assertNotIn('text', row)
            self.assertNotIn('ast', row)
            self.assertNotIn('source_path', row)
            self.assertNotIn('text_path', row)
            self.assertIn('source_sha256', row)
        following = (await self.request('/sources?offset=2&limit=2')).json()
        self.assertNotEqual(data['sources'][0]['source_id'], following['sources'][0]['source_id'])
        self.assert_private(response)

    async def test_large_original_url_is_exact_in_corpus_but_bounded_on_all_read_apis(self):
        fixture = corpus_fixtures.CorpusTests('test_source_round_trip_and_reference_occurrences')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.fixture()
        acquisitions_path = fixture.capture / 'acquisitions.jsonl'
        acquisitions = [json.loads(line) for line in acquisitions_path.read_text().splitlines()]
        objects = json.loads((fixture.capture / acquisitions[0]['raw_path']).read_bytes())
        original_url = 'https://example.test/' + 'x' * 150000
        objects[0]['link'] = original_url
        raw = corpus_fixtures.canonical(objects)
        raw_path = 'raw/' + corpus_fixtures.sha256(raw) + '.bin'
        (fixture.capture / raw_path).write_bytes(raw)
        acquisitions[0].update(raw_path=raw_path, body_sha256=corpus_fixtures.sha256(raw))
        acquisitions_path.write_bytes(b''.join(corpus_fixtures.canonical(row) for row in acquisitions))
        document = corpus_fixtures._document('post', objects[0], 'a1')
        (fixture.capture / 'documents.jsonl').write_bytes(corpus_fixtures.canonical(document))
        corpus = fixture.build()
        source_id = 'post:11:content_html'
        self.assertEqual(corpus.source(source_id)['url'], original_url)
        offset = sorted(corpus.sources).index(source_id)
        app = create_app(str(corpus.root), TOKEN, HOST)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 54321)), base_url=BASE,
            headers=[AUTHORIZATION],
        ) as client:
            responses = [
                await client.get('/sources', params={'offset': offset, 'limit': 1}),
                await client.get('/source', params={'source_id': source_id}),
                await client.get('/search', params={'query': 'dignity', 'limit': 1,
                                                   'source_kind': 'wordpress_post_content_html'}),
            ]
        for position, response in enumerate(responses):
            with self.subTest(api=position):
                self.assertEqual(response.status_code, 200, response.text[:200])
                data = response.json()
                projected = data['sources'][0] if position == 0 else data['results'][0] if position == 2 else data
                self.assertEqual(projected['url'], original_url[:MAX_URL])
                self.assertTrue(projected['url_truncated'])
                self.assertLess(len(response.content), 7000)
        self.assertEqual(corpus.source(source_id)['url'], original_url)
        self.assertEqual(json.loads((fixture.capture / raw_path).read_bytes())[0]['link'], original_url)

    async def test_malformed_source_identity_outputs_are_refused_not_truncated(self):
        row = self.app.state.corpus.sources['post:11:content_html']
        for field, budget in (('source_id', 2048), ('source_kind', 128), ('parser_version', 128)):
            with self.subTest(field=field):
                original = row[field]
                row[field] = 'x' * (budget + 1)
                try:
                    for route in ('/sources?limit=1', '/source?source_id=post:11:content_html',
                                  '/search?query=dignity', '/references'):
                        response = await self.request(route)
                        self.assertEqual(response.status_code, 409)
                        self.assertEqual(response.json(), {'error': 'corpus-integrity-check-failed'})
                        self.assert_private(response)
                finally:
                    row[field] = original

    async def test_unicode_excerpt_has_exact_half_open_citation(self):
        source = self.corpus.source('post:11:content_html')
        quote = 'dignity 😀'
        start = source['text'].index(quote)
        end = start + len(quote)
        response = await self.request('/source', params={
            'source_id': source['source_id'], 'start_char': start, 'end_char': end,
        })
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['text'], quote)
        citation = data['citation']
        self.assertEqual(source['text'][citation['start_char']:citation['end_char']], citation['quote'])
        self.assertEqual(citation['source_sha256'], source['source_sha256'])
        self.assertEqual(citation['text_sha256'], source['text_sha256'])
        self.assertNotIn('ast', data)
        self.assert_private(response)
        alias = await self.request('/sources/post:11:content_html', params={'start_char': start, 'end_char': end})
        self.assertEqual(alias.json(), data)

    async def test_excerpt_default_is_bounded_and_end_of_text_has_no_false_citation(self):
        source = self.corpus.source('post:11:content_html')
        response = await self.request('/source?source_id=post:11:content_html')
        self.assertLessEqual(len(response.json()['text']), 512)
        at_end = await self.request('/source', params={
            'source_id': source['source_id'], 'start_char': len(source['text']),
        })
        self.assertEqual(at_end.status_code, 200)
        self.assertEqual(at_end.json()['text'], '')
        self.assertIsNone(at_end.json()['citation'])
        self.assertIsNone(at_end.json()['next_start_char'])

    async def test_search_returns_source_bound_citations_and_kind_filter(self):
        response = await self.request('/search', params={
            'query': 'dignity', 'limit': 1, 'source_kind': 'wordpress_post_content_html',
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()['results'][0]
        source = self.corpus.source(result['source_id'])
        citation = result['citation']
        self.assertEqual(source['text'][citation['start_char']:citation['end_char']], citation['quote'])
        self.assertEqual(result['source_kind'], 'wordpress_post_content_html')
        self.assertNotIn('ast', result)
        self.assert_private(response)

    async def test_references_preserve_occurrences_with_bounded_projection(self):
        response = await self.request('/references', params={
            'source_id': 'post:11:content_html', 'status': 'external_unacquired', 'limit': 1,
        })
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['total'], 2)
        self.assertEqual(len(data['references']), 1)
        self.assertEqual(data['next_offset'], 1)
        second = (await self.request('/references', params={
            'source_id': 'post:11:content_html', 'status': 'external_unacquired', 'limit': 1, 'offset': 1,
        })).json()['references'][0]
        first = data['references'][0]
        self.assertEqual(first['resolved_url'], second['resolved_url'])
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotIn('raw_attribute_value', first)
        self.assertNotIn('json_literal', first)
        self.assert_private(response)

    async def test_invalid_query_bounds_and_ambiguous_parameters_are_400(self):
        paths = [
            '/sources?limit=101', '/sources?limit=0', '/sources?offset=-1',
            '/sources?limit=1&limit=2', '/sources?unexpected=value',
            '/source', '/source?source_id=post:11:content_html&start_char=-1',
            '/source?source_id=post:11:content_html&start_char=8&end_char=2',
            '/source?source_id=post:11:content_html&end_char=' + str(MAX_SLICE + 1),
            '/source?source_id=post:11:content_html&start_char=999',
            '/search?query=', '/search?query=dignity&limit=51',
            '/references?status=verified-truth', '/references?limit=101',
        ]
        for path in paths:
            with self.subTest(path=path):
                response = await self.request(path)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json(), {'error': 'invalid-query'})
                self.assert_private(response)
        huge = await self.request('/search', params={'query': 'x' * 1025})
        self.assertEqual(huge.status_code, 400)

    async def test_missing_sources_have_structured_404(self):
        for path in ('/source?source_id=missing', '/sources/missing', '/references?source_id=missing'):
            response = await self.request(path)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {'error': 'source-not-found'})
            self.assert_private(response)

    async def test_changed_source_text_and_search_are_integrity_409(self):
        source = self.corpus.sources['post:11:content_html']
        (self.corpus.root / source['text_path']).write_text('Tampered source text.')
        for path in ('/source?source_id=post:11:content_html', '/search?query=dignity'):
            response = await self.request(path)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {'error': 'corpus-integrity-check-failed'})
            self.assert_private(response)

    async def test_changed_catalog_or_reference_artifact_is_integrity_409(self):
        path = self.corpus.root / 'sources.jsonl'
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        response = await self.request('/sources')
        self.assertEqual(response.status_code, 409)
        path.write_bytes(original)
        references = self.corpus.root / 'references.jsonl'
        references.write_bytes(references.read_bytes() + b'\n')
        response = await self.request('/references')
        self.assertEqual(response.status_code, 409)
        self.assert_private(response)

    async def test_blocking_source_reads_run_outside_event_loop_thread(self):
        event_thread = threading.get_ident()
        worker_threads = []
        source = self.app.state.corpus.source

        def observe(source_id):
            worker_threads.append(threading.get_ident())
            return source(source_id)

        with patch.object(self.app.state.corpus, 'source', side_effect=observe):
            response = await self.request('/source?source_id=post:11:content_html')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(worker_threads)
        self.assertTrue(all(worker != event_thread for worker in worker_threads))

    async def test_unavailable_operation_does_not_leak_exception_or_path(self):
        secret_exception = OSError(str(self.corpus.root) + ' ' + TOKEN)
        with patch.object(self.app.state.corpus, 'source', side_effect=secret_exception):
            response = await self.request('/source?source_id=post:11:content_html')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'error': 'corpus-operation-unavailable'})
        self.assert_private(response)

    async def test_read_surface_creates_no_state_and_executes_no_source_instructions(self):
        before = {str(path.relative_to(self.fixture.root)): path.stat().st_mtime_ns
                  for path in self.fixture.root.rglob('*') if path.is_file()}
        with patch('socket.getaddrinfo', side_effect=AssertionError('Unexpected network request')), \
                patch('subprocess.run', side_effect=AssertionError('Unexpected subprocess')):
            for route in ('/sources', '/source?source_id=html:a3', '/search?query=page', '/references'):
                self.assertEqual((await self.request(route)).status_code, 200)
        after = {str(path.relative_to(self.fixture.root)): path.stat().st_mtime_ns
                 for path in self.fixture.root.rglob('*') if path.is_file()}
        self.assertEqual(after, before)
        self.assertFalse(list(self.fixture.root.rglob('lessons.sqlite3')))

    async def test_mutations_and_unknown_routes_are_structured_and_private(self):
        for method in ('POST', 'PUT', 'DELETE'):
            response = await self.request('/sources', method=method)
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response.json(), {'error': 'method-not-supported'})
            self.assert_private(response)
        response = await self.request('/unavailable')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'error': 'route-not-found'})
        self.assert_private(response)

    def test_factory_requires_explicit_path_long_capability_and_loopback_host(self):
        for token in ('', 'short', 'x' * 31, ' ' * 32, 'é' * 32):
            with self.subTest(token_kind=len(token)):
                with self.assertRaises(ValueError):
                    create_app(str(self.corpus.root), token, HOST)
        for host in ('0.0.0.0:8781', 'attacker.invalid', '127.0.0.1:0', '127.0.0.1:65536'):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    create_app(str(self.corpus.root), TOKEN, host)
        with self.assertRaises(ValueError):
            create_app('relative-corpus', TOKEN, HOST)
        link = self.fixture.root / 'linked-representation'
        link.symlink_to(self.corpus.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            create_app(str(link), TOKEN, HOST)


if __name__ == '__main__':
    unittest.main()

"""Offline synthetic acceptance for source-mapped document syntax; no external data."""
from __future__ import annotations

import hashlib
import html
import json
import unittest
from unittest.mock import patch

from graphrag_core.lessons_corpus.ast import parse_html_document, parse_text_document


BASE = 'https://example.test/books/chapter.html'


class DocumentAstTests(unittest.TestCase):
    def assert_spans(self, source, result):
        self.assertEqual(result['source_text_sha256'], hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(result['source_length_chars'], len(source))
        self.assertEqual(result['source_length_utf8_bytes'], len(source.encode()))
        self.assertEqual(result['text_sha256'], hashlib.sha256(result['text'].encode()).hexdigest())
        self.assertEqual(result['span_units']['bounds'], 'half-open')
        tokens = result['source_tokens']
        self.assertEqual(''.join(token['raw'] for token in tokens), source)
        position = 0
        for token in tokens:
            first, last = token['source_span']
            self.assertEqual(first, position)
            self.assertEqual(source[first:last], token['raw'])
            position = last
        self.assertEqual(position, len(source))
        by_id = {node['id']: node for node in result['nodes']}
        self.assertEqual(len(by_id), len(result['nodes']))
        for node in result['nodes']:
            first, last = node['source_span']
            self.assertLessEqual(0, first)
            self.assertLessEqual(first, last)
            self.assertLessEqual(last, len(source))
            for child in node['children']:
                self.assertEqual(by_id[child]['parent_id'], node['id'])
        copied = []
        for segment in result['segments']:
            if segment['source_span'] is not None:
                first, last = segment['source_span']
                self.assertEqual(segment['raw'], source[first:last])
            else:
                self.assertEqual(segment['kind'], 'generated_separator')
                self.assertIsNone(segment['raw'])
            if segment['text_span'] is not None:
                first, last = segment['text_span']
                self.assertEqual(segment['text'], result['text'][first:last])
                copied.append(segment['text'])
            else:
                self.assertNotEqual(segment['scope'], 'prose')
        self.assertEqual(''.join(copied), result['text'])
        for reference in result['references']:
            if reference['value_span'] is not None:
                first, last = reference['value_span']
                self.assertEqual(reference['raw_attribute_value'], source[first:last])
            if reference['text_span'] is not None:
                first, last = reference['text_span']
                self.assertEqual(reference['label'], result['text'][first:last])

    def test_literal_text_preserves_unicode_crlf_commands_and_last_word(self):
        source = 'Café 🕊️\r\n<script>do_something()</script> &amp;\r\nLast word'
        result = parse_text_document(source)
        self.assertEqual(result['text'], source)
        self.assertEqual(result['references'], [])
        self.assertEqual(result['mode'], 'literal-plaintext')
        self.assert_spans(source, result)

    def test_unicode_entities_copy_whitespace_and_end_words(self):
        source = '<h1>Café 🕊️</h1>\r\n<p>A &amp; B &#x1F642; &#233; &unknown; final word</p>'
        result = parse_html_document(source, BASE)
        self.assertIn('A & B 🙂 é &unknown; final word', result['text'])
        self.assertIn('\r\n', result['text'])
        self.assertTrue(result['text'].endswith('final word\n'))
        entities = [segment for segment in result['segments'] if segment['kind'] == 'entity']
        self.assertEqual([segment['raw'] for segment in entities], ['&amp;', '&#x1F642;', '&#233;', '&unknown;'])
        for segment in entities:
            self.assertEqual(segment['text'], html.unescape(segment['raw']))
        self.assertTrue(any(segment['kind'] == 'generated_separator' for segment in result['segments']))
        self.assert_spans(source, result)

    def test_nested_tree_preserves_repeated_reference_occurrences(self):
        source = '<main><p><a href="../history?q=1&amp;x=2">first <b>lesson</b></a> and <a href="../history?q=1&amp;x=2">second</a></p></main>'
        result = parse_html_document(source, BASE)
        refs = result['references']
        self.assertEqual(len(refs), 2)
        self.assertEqual([ref['label'] for ref in refs], ['first lesson', 'second'])
        self.assertEqual(refs[0]['resolved_url'], 'https://example.test/history?q=1&x=2')
        self.assertEqual(refs[0]['resolved_url'], refs[1]['resolved_url'])
        self.assertNotEqual(refs[0]['value_span'], refs[1]['value_span'])
        self.assertNotEqual(refs[0]['id'], refs[1]['id'])
        bold = next(node for node in result['nodes'] if node.get('tag') == 'b')
        anchor = next(node for node in result['nodes'] if node['id'] == bold['parent_id'])
        self.assertEqual(anchor['tag'], 'a')
        self.assert_spans(source, result)

    def test_image_iframe_attributes_and_duplicate_href_values_preserved(self):
        source = '<img src="/portrait.png" alt="A &amp; B"><iframe src="./video" title="History"></iframe><a href="/one" href=\'/two\'>Both</a>'
        result = parse_html_document(source, BASE)
        refs = result['references']
        self.assertEqual([ref['attribute'] for ref in refs], ['src', 'src', 'href', 'href'])
        self.assertEqual([ref['raw_attribute_value'] for ref in refs], ['/portrait.png', './video', '/one', '/two'])
        self.assertEqual([ref['label'] for ref in refs], ['A & B', 'History', 'Both', 'Both'])
        self.assert_spans(source, result)

    def test_nonprose_originals_are_preserved_without_script_execution(self):
        source = '<!DOCTYPE html><head><title>Hidden title</title><script src="/code.js">raise Exception("must not run")</script><style>body { color: red }</style></head><!--a private comment--><p>Only prose</p><template>Later text</template>'
        with patch('socket.create_connection', side_effect=AssertionError('No network')):
            result = parse_html_document(source, BASE)
        self.assertEqual(result['text'], 'Only prose\n')
        nonprose = ''.join(segment['raw'] or '' for segment in result['segments'] if segment['scope'] == 'nonprose')
        self.assertIn('Hidden title', nonprose)
        self.assertIn('raise Exception', nonprose)
        self.assertIn('a private comment', nonprose)
        self.assertIn('Later text', nonprose)
        self.assertEqual(result['references'][0]['scope'], 'nonprose')
        self.assert_spans(source, result)

    def test_unsafe_and_malformed_targets_are_observations_not_actions(self):
        source = '<a href="javascript:alert(1)">x</a><a href="java&#10;script:alert(1)">y</a><a href="https://[bad">z</a><a href="https://name:secret@example.test">c</a><a href="mailto:someone@example.test">mail</a><a href>none</a><img srcset="a.png 1x, b.png 2x"><a href="#chapter">chapter</a>'
        result = parse_html_document(source, BASE)
        self.assertEqual([ref['target_status'] for ref in result['references']],
                         ['unsafe', 'unsafe', 'malformed', 'malformed', 'non_fetchable', 'malformed',
                          'unsupported_compound', 'http'])
        self.assertEqual(result['references'][-1]['resolved_url'], BASE + '#chapter')
        self.assertEqual(result['references'][-2]['raw_attribute_value'], 'a.png 1x, b.png 2x')
        self.assert_spans(source, result)

    def test_malformed_nesting_and_tail_have_complete_source_coverage(self):
        for source in ['<div><p>A<b>B</div>Last word', '</orphan>Text <broken',
                       '<p a="unfinished> words', '<!--unfinished comment',
                       '<?instruction?>Text<![CDATA[raw text]]>', 'One &amp two &#65 Last word',
                       '<!foo>after', '<!----!>after', '<!>after', '<!-->after',
                       '<![if IE]>yes<![endif]>after']:
            with self.subTest(source=source):
                result = parse_html_document(source, BASE)
                self.assert_spans(source, result)
        result = parse_html_document('<div><p>A<b>B</div>Last word', BASE)
        self.assertTrue(result['text'].endswith('Last word'))
        self.assertEqual(next(node for node in result['nodes'] if node.get('tag') == 'b')['closing_reason'],
                         'ancestor-end-tag')

    def test_ambiguous_whitespace_and_percent_targets_are_flagged(self):
        source = '<a href="https://bad host/path">a</a><a href="this path">b</a><a href="/bad%xx">c</a><a href="https:\\ambiguous.test">d</a>'
        result = parse_html_document(source, BASE)
        self.assertEqual([reference['target_status'] for reference in result['references']],
                         ['malformed', 'malformed', 'malformed', 'unsafe'])
        self.assert_spans(source, result)

    def test_literal_private_hosts_and_invalid_labels_are_flagged_without_dns(self):
        targets = ['http://127.0.0.1/path', 'http://10.1.2.3/', 'http://[::1]/', 'http://localhost/',
                   'http://host.local/', 'http://127.1/', 'https://example..com/', 'https://-bad.example/',
                   'https://good.example/path']
        source = ''.join(f'<a href="{target}">reference</a>' for target in targets)
        with patch('socket.getaddrinfo', side_effect=AssertionError('No DNS lookup')):
            result = parse_html_document(source, BASE)
        self.assertEqual([reference['target_status'] for reference in result['references']],
                         ['unsafe'] * 6 + ['malformed', 'malformed', 'http'])
        self.assertEqual([reference['raw_attribute_value'] for reference in result['references']], targets)
        self.assert_spans(source, result)

    def test_empty_inputs_and_deterministic_serialization(self):
        for parser in (parse_text_document, lambda source: parse_html_document(source, BASE)):
            self.assert_spans('', parser(''))
        source = '<p>Same <a href="//example.test/other">lesson</a>.</p>'
        first = parse_html_document(source, BASE)
        second = parse_html_document(source, BASE)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(first['findings_status'], 'syntax-observations-not-interpretation')

    def test_invalid_input_and_base_url_fail_explicitly(self):
        for source in (None, b'bytes', '\ud800'):
            with self.subTest(source=repr(source)), self.assertRaises(ValueError):
                parse_text_document(source)
        for base in (None, 'file:///private/local', 'relative/path', 'https://[bad', 'https://example.test:invalid'):
            with self.subTest(base=base), self.assertRaises(ValueError):
                parse_html_document('<p>source</p>', base)


if __name__ == '__main__':
    unittest.main()

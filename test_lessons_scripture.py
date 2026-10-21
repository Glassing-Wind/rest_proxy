"""Offline, synthetic James USFM acceptance; no network, services or real corpus."""
from __future__ import annotations

import hashlib
import io
import stat
import unittest
from unittest.mock import patch
import zipfile

from graphrag_core.lessons_corpus.scripture import (
    ADAPTER_VERSION,
    parse_usfm_document,
    read_james_archive,
)


def document(verses='\\v 1 Care for the reader.\n'):
    return '\\id JAS Synthetic fixture\n\\h James\n\\c 1\n\\p\n' + verses


def archive(entries, *, compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=compression) as output:
        for name, content in entries:
            output.writestr(name, content)
    return stream.getvalue()


class JamesUsfmTests(unittest.TestCase):
    def assert_mapping(self, source, result):
        self.assertEqual(result['parser_version'], ADAPTER_VERSION)
        self.assertEqual(result['source_text_sha256'], hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(result['text_sha256'], hashlib.sha256(result['text'].encode()).hexdigest())
        self.assertEqual(result['source_length_chars'], len(source))
        self.assertEqual(result['source_length_utf8_bytes'], len(source.encode()))
        self.assertEqual(''.join(token['raw'] for token in result['source_tokens']), source)
        position = 0
        for token in result['source_tokens']:
            first, last = token['source_span']
            self.assertEqual(first, position)
            self.assertEqual(source[first:last], token['raw'])
            position = last
        self.assertEqual(position, len(source))
        self.assertEqual(''.join(segment['text'] for segment in result['segments']
                                 if segment['text_span'] is not None), result['text'])
        for segment in result['segments']:
            if segment['source_span'] is not None:
                self.assertEqual(source[slice(*segment['source_span'])], segment['raw'])
            else:
                self.assertEqual(segment['kind'], 'generated_separator')
            if segment['text_span'] is not None:
                self.assertEqual(result['text'][slice(*segment['text_span'])], segment['text'])
        by_id = {node['id']: node for node in result['nodes']}
        self.assertEqual(len(by_id), len(result['nodes']))
        for node in result['nodes']:
            first, last = node['source_span']
            self.assertLessEqual(0, first)
            self.assertLessEqual(first, last)
            self.assertLessEqual(last, len(source))
            for child_id in node['children']:
                child = by_id[child_id]
                self.assertEqual(child['parent_id'], node['id'])
                self.assertLessEqual(first, child['source_span'][0])
                self.assertLessEqual(child['source_span'][1], last)
        for verse in result['verses']:
            self.assertEqual(verse['text'], result['text'][slice(*verse['text_span'])])

    def test_exact_unicode_bom_crlf_source_and_literal_commands(self):
        source = '\ufeff' + document('\\v 1 Café 😀 <script>do_not_execute()</script> &amp;.\n').replace('\n', '\r\n')
        with patch('socket.create_connection', side_effect=AssertionError('No network')):
            result = parse_usfm_document(source)
        self.assertEqual(result['text'], 'Café 😀 <script>do_not_execute()</script> &amp;.')
        self.assert_mapping(source, result)

    def test_verse_milestones_cross_paragraphs_without_ending_verse(self):
        source = document('\\v 1 First clause\n\\p\ncontinued here.\n\\v 2 Another verse.\n')
        result = parse_usfm_document(source)
        self.assertEqual([verse['id'] for verse in result['verses']], ['JAS.1.1', 'JAS.1.2'])
        self.assertEqual(result['verses'][0]['text'], 'First clause continued here.')
        self.assertEqual(result['text'], 'First clause continued here.\nAnother verse.')
        self.assertEqual(result['paragraph_milestones'][1]['verse_id'], 'JAS.1.1')
        self.assert_mapping(source, result)

    def test_word_annotations_do_not_insert_spaces_inside_contractions(self):
        source = document('\\v 1 Don’\\w t|strong="G3588"\\w* stop.\n')
        result = parse_usfm_document(source)
        self.assertEqual(result['text'], 'Don’t stop.')
        word = next(node for node in result['nodes'] if node['kind'] == 'word')
        attribute = word['attributes'][0]
        self.assertEqual(source[slice(*attribute['source_span'])], 'strong="G3588"')
        self.assertEqual(source[slice(*attribute['value_span'])], 'G3588')
        self.assert_mapping(source, result)

    def test_inline_additions_and_explicit_nested_styles_preserve_wording(self):
        source = document('\\v 1 Love \\add your \\+it neighbor\\+it*\\add*.\n')
        result = parse_usfm_document(source)
        self.assertEqual(result['text'], 'Love your neighbor.')
        self.assertEqual(len([node for node in result['nodes'] if node['kind'] == 'inline_style']), 2)
        self.assert_mapping(source, result)

    def test_notes_are_preserved_separately_and_references_are_observations(self):
        source = document('\\v 1 Words\\f + \\fr 1:1 \\ft An editorial opinion.\\f* remain'
                          '\\x + \\xo 1:1 \\xt Example 2:3; Other 4:5\\x*.\n')
        result = parse_usfm_document(source)
        self.assertEqual(result['text'], 'Words remain.')
        self.assertEqual(len(result['notes']), 2)
        for note in result['notes']:
            self.assertEqual(source[slice(*note['source_span'])], note['raw'])
            self.assertIsNone(note['text_span'])
            self.assertEqual(note['verse_id'], 'JAS.1.1')
        reference = result['references'][0]
        self.assertEqual(reference['label'], 'Example 2:3; Other 4:5')
        self.assertEqual(reference['target_status'], 'non_fetchable')
        self.assertIsNone(reference['resolved_url'])
        self.assertEqual(source[slice(*reference['value_span'])], reference['raw_attribute_value'])
        self.assert_mapping(source, result)

    def test_metadata_is_not_quoted_as_verse_wording(self):
        source = document('\\v 1 First.\n\\s1 Editorial heading\n\\p\nSecond clause.\n')
        result = parse_usfm_document(source)
        self.assertEqual(result['text'], 'First. Second clause.')
        self.assertNotIn('Editorial', result['text'])
        metadata = [segment for segment in result['segments'] if segment['scope'] == 'nonprose']
        self.assertTrue(any('Editorial heading' in segment['raw'] for segment in metadata))
        self.assert_mapping(source, result)

    def test_whitespace_normalization_is_explicit_and_end_words_survive(self):
        source = document('\\v 1    First\t\tword\r\nlast word   \n\\v 2 Final word')
        result = parse_usfm_document(source)
        self.assertEqual(result['text'], 'First word last word\nFinal word')
        self.assertTrue(any(segment['raw'] == '\t\t' and segment['text'] == ' '
                            for segment in result['segments']))
        self.assert_mapping(source, result)

    def test_multiple_chapters_have_distinct_addresses(self):
        source = document('\\v 1 First.\n\\c 2\n\\v 1 Second.\n')
        result = parse_usfm_document(source)
        self.assertEqual([verse['id'] for verse in result['verses']], ['JAS.1.1', 'JAS.2.1'])
        self.assertEqual([chapter['id'] for chapter in result['chapters']], ['JAS.1', 'JAS.2'])
        self.assert_mapping(source, result)

    def test_duplicate_or_reordered_addresses_and_bridges_are_rejected(self):
        for verses in ['\\v 1 First.\n\\v 1 Different.\n', '\\v 2 First.\n\\v 1 Different.\n',
                       '\\v 1-2 Bridged.\n', '\\v 1a Split.\n', '\\v 0 Zero.\n',
                       '\\v 1 First.\n\\c 1\n\\v 2 Duplicate chapter.\n']:
            with self.subTest(verses=verses), self.assertRaises(ValueError):
                parse_usfm_document(document(verses))

    def test_unsupported_or_malformed_syntax_is_not_silently_deleted(self):
        for verses in ['\\v 1 Text \\unknown hidden.\n', '\\v 1 Text \\zaln-s |x="a"\\* end.\n',
                       '\\v 1 Text \\? malformed.\n', '\\v 1 Text\\w word\\w*.\n',
                       '\\v 1 Text\\w word|strong="a" strong="b"\\w*.\n',
                       '\\v 1 Text\\w word|strong="a"lemma="b"\\w*.\n',
                       '\\v 1 \\+w word|strong="a"\\+w*.\n',
                       '\\v 1 \\+it style\\+it*.\n',
                       '\\v 1 Text\\w word|bad-attribute\\w*.\n']:
            with self.subTest(verses=verses), self.assertRaises(ValueError):
                parse_usfm_document(document(verses))

    def test_unclosed_crossing_or_implicit_nested_inline_styles_fail(self):
        for verses in ['\\v 1 Text \\add added\n', '\\v 1 \\add first\n\\v 2 Next\\add*.\n',
                       '\\v 1 \\add one \\it two\\it*\\add*.\n', '\\v 1 Text\\it*.\n',
                       '\\v 1 \\addword\\add*.\n']:
            with self.subTest(verses=verses), self.assertRaises(ValueError):
                parse_usfm_document(document(verses))

    def test_malformed_notes_reject_unclassified_or_unclosed_content(self):
        for verses in ['\\v 1 Text\\f + \\fr 1:1 \\ft Unclosed.\n',
                       '\\v 1 Text\\f + unclassified \\ft Note\\f*.\n',
                       '\\v 1 Text\\f + \\fr 1:1\\f*.\n',
                       '\\v 1 Text\\x + \\ft Wrong note field\\x*.\n',
                       '\\v 1 Text\\f + \\ft Note \\it styled\\it*\\f*.\n']:
            with self.subTest(verses=verses), self.assertRaises(ValueError):
                parse_usfm_document(document(verses))

    def test_missing_wrong_duplicate_or_out_of_order_book_id_fails(self):
        for source in ['\\c 1\n\\v 1 Words.', document().replace('\\id JAS', '\\id GEN'),
                       '\\h Title\n' + document(), document() + '\\id JAS Duplicate\n',
                       '\\id JAS\n\\c 1\n', '\\id JAS\n\\v 1 Words.\n']:
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_usfm_document(source)
        with self.assertRaises(ValueError):
            parse_usfm_document(document(), book='GEN')

    def test_metadata_continuations_empty_verses_and_empty_chapters_fail(self):
        for source in [document().replace('\\h James\n', '\\h James\nUnclassified wording\n'),
                       document('\\v 1   \n'), document('\\c 2\n\\v 1 Next.\n'),
                       document('\\v 1 Text. \\v 2 Same line.\n')]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_usfm_document(source)

    def test_input_type_unicode_and_size_are_bounded(self):
        for source in [b'bytes', '\ud800', document('\\v 1 A\x00B')]:
            with self.subTest(source=repr(source)), self.assertRaises(ValueError):
                parse_usfm_document(source)
        with patch('graphrag_core.lessons_corpus.scripture.MAX_JAMES_BYTES', 10), self.assertRaises(ValueError):
            parse_usfm_document(document())


class JamesArchiveTests(unittest.TestCase):
    def test_selects_by_book_id_and_preserves_decoding_and_line_endings(self):
        source = '\ufeff' + document().replace('\n', '\r\n')
        raw = archive([('nested/not-named-james.usfm', source), ('JAS-but-genesis.usfm', '\\id GEN\n')])
        with patch.object(zipfile.ZipFile, 'extract', side_effect=AssertionError('No extraction')), \
                patch.object(zipfile.ZipFile, 'extractall', side_effect=AssertionError('No extraction')):
            name, decoded = read_james_archive(raw)
        self.assertEqual(name, 'nested/not-named-james.usfm')
        self.assertEqual(decoded, source)
        self.assertEqual(decoded.encode(), source.encode())

    def test_zero_or_multiple_james_books_fail(self):
        for entries in [[('JAS.usfm', '\\id GEN\n')],
                        [('one.usfm', document()), ('two.usfm', document())]]:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                read_james_archive(archive(entries))

    def test_unsafe_paths_fail_even_for_non_james_members(self):
        for name in ['../outside', '/absolute', 'a/../b', 'a\\b', 'C:drive', './relative', 'a//b']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                read_james_archive(archive([('James.usfm', document()), (name, 'other')]))

    def test_case_colliding_member_paths_fail(self):
        with self.assertRaises(ValueError):
            read_james_archive(archive([('James.usfm', document()), ('james.USFM', 'other')]))

    def test_symlink_members_fail(self):
        symbolic = zipfile.ZipInfo('link')
        symbolic.create_system = 3
        symbolic.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(ValueError):
            read_james_archive(archive([('James.usfm', document()), (symbolic, '../other')]))

    def test_compression_bomb_and_unsupported_compression_fail(self):
        with self.assertRaises(ValueError):
            read_james_archive(archive([('James.usfm', document()), ('bomb', 'a' * 100000)]))
        with self.assertRaises(ValueError):
            read_james_archive(archive([('James.usfm', document())], compression=zipfile.ZIP_BZIP2))

    def test_archive_member_total_and_selected_sizes_are_bounded(self):
        raw = archive([('James.usfm', document()), ('other', 'content')])
        settings = [('MAX_ARCHIVE_BYTES', 2), ('MAX_ARCHIVE_MEMBERS', 1),
                    ('MAX_ARCHIVE_EXPANDED_BYTES', 2), ('MAX_MEMBER_BYTES', 2), ('MAX_JAMES_BYTES', 2)]
        for name, limit in settings:
            with self.subTest(name=name), patch(f'graphrag_core.lessons_corpus.scripture.{name}', limit), \
                    self.assertRaises(ValueError):
                read_james_archive(raw)

    def test_invalid_archive_and_invalid_utf8_fail(self):
        for raw in [b'', b'not a zip', archive([('James.usfm', b'\\id JAS\n\xff')]), None]:
            with self.subTest(raw=repr(raw)[:30]), self.assertRaises(ValueError):
                read_james_archive(raw)


if __name__ == '__main__':
    unittest.main()
